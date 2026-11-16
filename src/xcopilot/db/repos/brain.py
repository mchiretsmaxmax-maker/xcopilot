"""Запросы «Мозга» этапа 9A: материалы с разбором и исходом, детекторы, доказательства,
переходы, откат и сид. Прикладной код (`xcopilot.brain`) ходит в БД только отсюда.

Разборы читаются из `material_analyses` напрямую (договорённость 7A/9A): функции этапа 7A
не импортируются.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

WORKING = ("candidate", "probation", "active", "weakened", "conflicted")
_ANALYSIS_COLS = (
    "format, archetype, lang, hook_type, hook_text, structure, structure_outline, tone, devices,"
    " closer, human_markers, ai_markers, features, why_hypotheses, declared_claims, quality_flags"
)


_MA = ", ".join("ma." + c.strip() for c in _ANALYSIS_COLS.split(","))


def _rows(result: Any) -> list[dict[str, Any]]:
    return [dict(r._mapping) for r in result.all()]


class BrainRepo:
    """Запросы одного аккаунта; account_id обязателен."""

    def __init__(self, session: AsyncSession, account_id: UUID) -> None:
        if not isinstance(account_id, UUID):
            raise ValueError("BrainRepo: account_id обязателен (UUID)")
        self.session = session
        self.account_id = account_id

    async def _all(self, sql: str, **params: Any) -> list[dict[str, Any]]:
        return _rows(await self.session.execute(text(sql), {"a": self.account_id, **params}))

    async def _one(self, sql: str, **params: Any) -> dict[str, Any] | None:
        rows = await self._all(sql, **params)
        return rows[0] if rows else None

    async def _scalar(self, sql: str, **params: Any) -> Any:
        return (await self.session.execute(text(sql), {"a": self.account_id, **params})).scalar()

    # ── материалы ──────────────────────────────────────────────────────────────────────

    async def material(self, source_id: UUID) -> dict[str, Any] | None:
        """Материал с текущим разбором, текущим исходом, автором и нишей аккаунта."""
        row = await self._one(
            f"SELECT s.id, s.kind, s.stance, s.origin, s.author_id, s.posted_at, s.created_at,"
            f" s.removed_at, s.analysis_status,"
            f" au.niche AS author_niche, au.trust AS author_trust, au.is_self AS author_is_self,"
            f" acc.niche AS account_niche,"
            f" (SELECT d.text FROM documents d WHERE d.source_id = s.id ORDER BY d.id LIMIT 1)"
            f"   AS text,"
            f" ma.id AS analysis_id, {_MA},"
            f" mo.signal_origin, mo.basis AS outcome_basis, mo.reliability,"
            f" mo.tier_reach, mo.tier_engagement, mo.tier_replies, mo.tier_saves"
            f" FROM sources s"
            f" JOIN accounts acc ON acc.id = s.account_id"
            f" LEFT JOIN authors au ON au.id = s.author_id"
            f" LEFT JOIN material_analyses ma ON ma.source_id = s.id AND ma.is_current"
            f" LEFT JOIN material_outcomes mo ON mo.source_id = s.id AND mo.is_current"
            f" WHERE s.account_id = :a AND s.id = :s",
            s=source_id,
        )
        return row

    async def pending_materials(self) -> list[dict[str, Any]]:
        """Материалы с текущим разбором, ещё не прошедшие извлечение."""
        return await self._all(
            "SELECT s.id, ma.format, s.stance, s.origin, s.created_at, mo.pct_engagement,"
            " mo.tier_engagement"
            " FROM sources s"
            " JOIN material_analyses ma ON ma.source_id = s.id AND ma.is_current"
            " LEFT JOIN material_outcomes mo ON mo.source_id = s.id AND mo.is_current"
            " LEFT JOIN authors au ON au.id = s.author_id"
            " WHERE s.account_id = :a AND s.removed_at IS NULL"
            " AND s.analysis_status <> 'quarantined' AND coalesce(au.trust, 0) <> -1"
            " AND NOT EXISTS (SELECT 1 FROM events e WHERE e.account_id = :a"
            "   AND e.type = 'brain.extraction_done'"
            "   AND e.props -> 'materials' ? s.id::text)"
            " ORDER BY s.created_at, s.id"
        )

    async def batch_materials(self, source_ids: Sequence[UUID]) -> list[dict[str, Any]]:
        """Разборы, отношение и исходы для пачки (порядок — как в source_ids)."""
        rows = await self._all(
            f"SELECT s.id, s.stance, s.author_id, {_MA},"
            " mo.reliability, mo.tier_reach, mo.tier_engagement, mo.tier_replies, mo.tier_saves"
            " FROM sources s"
            " JOIN material_analyses ma ON ma.source_id = s.id AND ma.is_current"
            " LEFT JOIN material_outcomes mo ON mo.source_id = s.id AND mo.is_current"
            " WHERE s.account_id = :a AND s.id = ANY(:ids)",
            ids=list(source_ids),
        )
        by_id = {r["id"]: r for r in rows}
        return [by_id[i] for i in source_ids if i in by_id]

    # ── знания ─────────────────────────────────────────────────────────────────────────

    async def detector_items(self) -> list[dict[str, Any]]:
        """Рабочие знания с детектором (не none)."""
        return await self._all(
            "SELECT id, layer, target_metric, detector FROM knowledge_items"
            " WHERE account_id = :a AND status = ANY(:st) AND detector ->> 'type' <> 'none'"
            " ORDER BY quality DESC, id",
            st=list(WORKING),
        )

    async def nearest(
        self,
        vec: Sequence[float],
        *,
        limit: int,
        ids: Sequence[UUID] | None = None,
        min_similarity: float | None = None,
        exclude: Sequence[UUID] = (),
    ) -> list[dict[str, Any]]:
        """Знания аккаунта по косинусному сходству вектора (активная модель эмбеддингов)."""
        sql = (
            "SELECT k.id AS item_id, k.statement, k.created_at,"
            " 1 - (e.vec <=> CAST(:v AS vector)) AS similarity"
            " FROM knowledge_embeddings e"
            " JOIN embedding_models m ON m.id = e.model_id AND m.active"
            " JOIN knowledge_items k ON k.id = e.item_id"
            " WHERE e.account_id = :a AND k.status = ANY(:st) AND NOT (k.id = ANY(:ex))"
        )
        params: dict[str, Any] = {
            "v": "[" + ",".join(f"{x:.6f}" for x in vec) + "]",
            "st": list(WORKING),
            "ex": list(exclude),
            "n": limit,
        }
        if ids is not None:
            sql += " AND k.id = ANY(:ids)"
            params["ids"] = list(ids)
        if min_similarity is not None:
            sql += " AND 1 - (e.vec <=> CAST(:v AS vector)) >= :ms"
            params["ms"] = min_similarity
        sql += " ORDER BY e.vec <=> CAST(:v AS vector), k.created_at LIMIT :n"
        return await self._all(sql, **params)

    async def account_niche(self) -> dict[str, Any]:
        row = await self._one("SELECT niche, lang FROM accounts WHERE id = :a")
        return row or {"niche": None, "lang": "ru"}

    async def related_threshold(self) -> float:
        value = await self._scalar("SELECT related_threshold FROM embedding_models WHERE active")
        return 0.70 if value is None else float(value)

    async def active_embedding_model(self) -> UUID | None:
        return cast(UUID | None, await self._scalar("SELECT id FROM embedding_models WHERE active"))

    async def existing_for_extract(self, limit: int) -> list[dict[str, Any]]:
        return await self._all(
            "SELECT id, kind, layer, element, statement FROM knowledge_items"
            " WHERE account_id = :a AND status = ANY(:st)"
            " ORDER BY quality DESC, created_at LIMIT :n",
            st=list(WORKING),
            n=limit,
        )

    async def item(self, item_id: UUID) -> dict[str, Any] | None:
        return await self._one(
            "SELECT * FROM knowledge_items WHERE account_id = :a AND id = :i", i=item_id
        )

    async def all_item_ids(self) -> list[UUID]:
        """Все знания аккаунта в любом статусе."""
        rows = await self._all("SELECT id FROM knowledge_items WHERE account_id = :a")
        return [r["id"] for r in rows]

    async def item_ids(self, statuses: Sequence[str] = WORKING) -> list[UUID]:
        rows = await self._all(
            "SELECT id FROM knowledge_items WHERE account_id = :a AND status = ANY(:st)"
            " ORDER BY created_at, id",
            st=list(statuses),
        )
        return [r["id"] for r in rows]

    async def set_priors(
        self, item_id: UUID, *, eff: tuple[float, float], fit: tuple[float, float]
    ) -> None:
        await self.session.execute(
            text(
                "UPDATE knowledge_items SET eff_prior_alpha = :ea, eff_prior_beta = :eb,"
                " fit_prior_alpha = :fa, fit_prior_beta = :fb,"
                " eff_alpha = :ea, eff_beta = :eb, fit_alpha = :fa, fit_beta = :fb"
                " WHERE account_id = :a AND id = :i"
            ),
            {
                "a": self.account_id,
                "i": item_id,
                "ea": eff[0],
                "eb": eff[1],
                "fa": fit[0],
                "fb": fit[1],
            },
        )

    # ── доказательства ─────────────────────────────────────────────────────────────────

    async def author_evidence_count(self, item_id: UUID, author_id: UUID) -> int:
        """Сколько живых доказательств eff этого автора уже есть у знания."""
        return int(
            await self._scalar(
                "SELECT count(*) FROM knowledge_evidence WHERE account_id = :a AND item_id = :i"
                " AND author_id = :au AND dimension = 'eff' AND revoked_at IS NULL"
                " AND basis <> 'own_outcome'",
                i=item_id,
                au=author_id,
            )
        )

    async def insert_evidence(self, **values: Any) -> UUID | None:
        """Новая строка доказательства; повтор (знание, материал, основание, измерение) — None."""
        values = {"account_id": self.account_id, **values}
        cols = ", ".join(values)
        params = ", ".join(f":{k}" for k in values)
        conflict = (
            "(item_id, draft_id, basis, dimension)"
            " WHERE revoked_at IS NULL AND draft_id IS NOT NULL AND source_id IS NULL"
            if values.get("draft_id") is not None
            else "(item_id, source_id, basis, dimension)"
            " WHERE revoked_at IS NULL AND source_id IS NOT NULL"
        )
        result = await self.session.execute(
            text(
                f"INSERT INTO knowledge_evidence ({cols}) VALUES ({params})"
                f" ON CONFLICT {conflict} DO NOTHING RETURNING id"
            ),
            values,
        )
        return cast(UUID | None, result.scalar())

    async def revoke_evidence(
        self,
        *,
        at: datetime,
        reason: str,
        source_id: UUID | None = None,
        author_id: UUID | None = None,
        ids: Sequence[UUID] | None = None,
        created_after: datetime | None = None,
    ) -> list[UUID]:
        """Отозвать живые доказательства по условию; вернуть id затронутых знаний."""
        where = ["account_id = :a", "revoked_at IS NULL"]
        params: dict[str, Any] = {"at": at, "r": reason}
        if source_id is not None:
            where.append("source_id = :s")
            params["s"] = source_id
        if author_id is not None:
            where.append("author_id = :au")
            params["au"] = author_id
        if ids is not None:
            where.append("id = ANY(:ids)")
            params["ids"] = list(ids)
        if created_after is not None:
            where.append("created_at > :ca")
            params["ca"] = created_after
        rows = await self._all(
            f"UPDATE knowledge_evidence SET revoked_at = :at, revoked_reason = :r"
            f" WHERE {' AND '.join(where)} RETURNING item_id",
            **params,
        )
        return [r["item_id"] for r in rows]

    async def unrevoke_evidence(self, reason: str) -> list[UUID]:
        rows = await self._all(
            "UPDATE knowledge_evidence SET revoked_at = NULL, revoked_reason = NULL"
            " WHERE account_id = :a AND revoked_reason = :r RETURNING item_id",
            r=reason,
        )
        return [r["item_id"] for r in rows]

    async def move_evidence(self, from_item: UUID, to_item: UUID, *, at: datetime) -> int:
        """Слияние: живые доказательства переходят к старшему знанию (копия + отзыв)."""
        moved = await self._all(
            "INSERT INTO knowledge_evidence (account_id, item_id, source_id, draft_id, dimension,"
            " basis, polarity, detector_score, outcome_tier, target_metric, format, author_id,"
            " w_source, w_scope, w_magnitude, w_author, observed_at)"
            " SELECT account_id, :to, source_id, draft_id, dimension, basis, polarity,"
            " detector_score, outcome_tier, target_metric, format, author_id, w_source, w_scope,"
            " w_magnitude, w_author, observed_at FROM knowledge_evidence"
            " WHERE account_id = :a AND item_id = :fr AND revoked_at IS NULL"
            " ON CONFLICT DO NOTHING RETURNING id",
            to=to_item,
            fr=from_item,
        )
        await self.session.execute(
            text(
                "UPDATE knowledge_evidence SET revoked_at = :at, revoked_reason = 'merged'"
                " WHERE account_id = :a AND item_id = :fr AND revoked_at IS NULL"
            ),
            {"a": self.account_id, "fr": from_item, "at": at},
        )
        return len(moved)

    async def block_author(self, author_id: UUID, note: str) -> None:
        await self.session.execute(
            text("UPDATE authors SET trust = -1, note = :n WHERE account_id = :a AND id = :au"),
            {"a": self.account_id, "au": author_id, "n": note},
        )

    async def review(self, review_id: UUID) -> dict[str, Any] | None:
        return await self._one(
            "SELECT id, draft_id, action, reason_code, edit_distance, created_at FROM reviews"
            " WHERE account_id = :a AND id = :r",
            r=review_id,
        )

    async def applied_items(self, draft_id: UUID) -> list[dict[str, Any]]:
        return await self._all(
            "SELECT DISTINCT ka.item_id, k.layer FROM knowledge_applications ka"
            " JOIN knowledge_items k ON k.id = ka.item_id"
            " WHERE ka.account_id = :a AND ka.draft_id = :d AND ka.applied IS NOT FALSE",
            d=draft_id,
        )

    # ── состояние для правил переходов ─────────────────────────────────────────────────

    async def lifecycle_state(self, item_id: UUID, *, week_from: datetime) -> dict[str, Any] | None:
        return await self._one(
            "SELECT k.id, k.status, k.risk_class, k.owner_state, k.target_metric, k.layer,"
            " k.eff_conf::float, k.fit_conf::float, k.quality::float, k.evidence_n,"
            " k.independent_authors, k.own_support, k.own_contra, k.probation_started_at,"
            " (SELECT (v.provenance ->> 'voice_compat')::float FROM knowledge_item_versions v"
            "   WHERE v.item_id = k.id AND v.provenance ? 'voice_compat'"
            "   ORDER BY v.version DESC LIMIT 1) AS voice_compat,"
            " EXISTS (SELECT 1 FROM knowledge_links l WHERE l.account_id = :a"
            "   AND l.status = 'open' AND l.relation = 'contradicts'"
            "   AND k.id IN (l.from_item_id, l.to_item_id)) AS open_conflict,"
            " (SELECT count(*) FROM knowledge_evidence e WHERE e.item_id = k.id"
            "   AND e.basis = 'review' AND e.polarity = 1 AND e.revoked_at IS NULL)"
            "   AS approvals_clean,"
            " (SELECT count(*) FROM knowledge_evidence e WHERE e.item_id = k.id"
            "   AND e.basis = 'review' AND e.polarity = -1 AND e.w_source >= 1.5"
            "   AND e.revoked_at IS NULL) AS voice_rejections,"
            " coalesce((SELECT max(t.ts) FROM knowledge_transitions t WHERE t.item_id = k.id"
            "   AND t.reverted_at IS NULL), k.created_at) AS status_since,"
            " (SELECT max(ka.created_at) FROM knowledge_applications ka WHERE ka.item_id = k.id)"
            "   AS last_applied_at,"
            " (SELECT max(e.observed_at) FROM knowledge_evidence e WHERE e.item_id = k.id"
            "   AND e.polarity = 1 AND e.revoked_at IS NULL) AS last_support_at,"
            " (SELECT count(*) FROM knowledge_transitions t WHERE t.account_id = :a"
            "   AND t.rule = 'auto_probation' AND t.reverted_at IS NULL AND t.ts > :wf)"
            "   AS auto_probations_week"
            " FROM knowledge_items k WHERE k.account_id = :a AND k.id = :i",
            i=item_id,
            wf=week_from,
        )

    async def open_conflicts(self) -> list[dict[str, Any]]:
        return await self._all(
            "SELECT l.id, l.from_item_id, l.to_item_id,"
            " f.own_support - f.own_contra AS from_net, t.own_support - t.own_contra AS to_net"
            " FROM knowledge_links l"
            " JOIN knowledge_items f ON f.id = l.from_item_id"
            " JOIN knowledge_items t ON t.id = l.to_item_id"
            " WHERE l.account_id = :a AND l.status = 'open' AND l.relation = 'contradicts'"
            " ORDER BY l.created_at, l.id"
        )

    async def conflict_transition(self, item_id: UUID) -> dict[str, Any] | None:
        """Последний неотменённый переход знания в conflicted."""
        return await self._one(
            "SELECT id, from_status FROM knowledge_transitions WHERE account_id = :a"
            " AND item_id = :i AND to_status = 'conflicted' AND reverted_at IS NULL"
            " ORDER BY ts DESC, id DESC LIMIT 1",
            i=item_id,
        )

    async def probation_candidates(
        self, facets: dict[str, str], *, since: datetime
    ) -> list[dict[str, Any]]:
        """Знания на испытании, допущенные к применению и подходящие задаче по области;
        сначала наименее проверенные (меньше применений с since), затем дольше на испытании."""
        scope = " ".join(
            f"AND (NOT EXISTS (SELECT 1 FROM knowledge_item_scopes sc WHERE sc.item_id = u.id"
            f" AND sc.facet = '{facet}') OR EXISTS (SELECT 1 FROM knowledge_item_scopes sc"
            f" WHERE sc.item_id = u.id AND sc.facet = '{facet}' AND sc.value = :f_{facet}))"
            for facet in facets
        )
        return await self._all(
            "SELECT u.id, u.statement, u.guidance, u.skeleton,"
            " (SELECT count(*) FROM knowledge_applications ka WHERE ka.item_id = u.id"
            "   AND ka.created_at >= :since) AS applied"
            " FROM v_knowledge_usable u"
            f" WHERE u.account_id = :a AND u.status = 'probation' {scope}"
            " ORDER BY applied, u.probation_started_at NULLS LAST, u.id",
            since=since,
            **{f"f_{k}": v for k, v in facets.items()},
        )

    # ── снимки ─────────────────────────────────────────────────────────────────────────

    async def has_snapshot(self, day: Any) -> bool:
        return bool(
            await self._scalar(
                "SELECT EXISTS (SELECT 1 FROM knowledge_snapshots"
                " WHERE account_id = :a AND snapshot_date = :d)",
                d=day,
            )
        )

    # ── откат ──────────────────────────────────────────────────────────────────────────

    async def created_after(self, ts: datetime) -> list[UUID]:
        rows = await self._all(
            "SELECT id FROM knowledge_items WHERE account_id = :a AND created_at > :t"
            " ORDER BY created_at, id",
            t=ts,
        )
        return [r["id"] for r in rows]

    async def transitions_after(self, ts: datetime) -> list[dict[str, Any]]:
        return await self._all(
            "SELECT id, item_id, from_status, to_status, ts FROM knowledge_transitions"
            " WHERE account_id = :a AND ts > :t AND reverted_at IS NULL ORDER BY ts, id",
            t=ts,
        )

    async def versions_after(self, ts: datetime) -> list[dict[str, Any]]:
        return await self._all(
            "SELECT item_id, version FROM knowledge_item_versions"
            " WHERE account_id = :a AND created_at > :t ORDER BY created_at, item_id, version",
            t=ts,
        )

    async def version_at(self, item_id: UUID, ts: datetime) -> dict[str, Any] | None:
        return await self._one(
            "SELECT * FROM knowledge_item_versions WHERE account_id = :a AND item_id = :i"
            " AND created_at <= :t ORDER BY version DESC LIMIT 1",
            i=item_id,
            t=ts,
        )

    async def version(self, item_id: UUID, version: int) -> dict[str, Any] | None:
        return await self._one(
            "SELECT * FROM knowledge_item_versions WHERE account_id = :a AND item_id = :i"
            " AND version = :v",
            i=item_id,
            v=version,
        )

    async def rollback_versions(self, rollback_id: UUID) -> list[dict[str, Any]]:
        return await self._all(
            "SELECT item_id, version FROM knowledge_item_versions WHERE account_id = :a"
            " AND reason = 'rollback' AND provenance ->> 'rollback_id' = :r ORDER BY item_id",
            r=str(rollback_id),
        )

    async def transition(self, transition_id: UUID) -> dict[str, Any] | None:
        return await self._one(
            "SELECT id, item_id, from_status, to_status, payload FROM knowledge_transitions"
            " WHERE account_id = :a AND id = :t",
            t=transition_id,
        )

    async def rollback_transitions(self, rollback_id: UUID) -> list[UUID]:
        rows = await self._all(
            "SELECT id FROM knowledge_transitions WHERE account_id = :a AND rule = 'rollback'"
            " AND payload ->> 'rollback_id' = :r AND reverted_at IS NULL ORDER BY ts DESC, id DESC",
            r=str(rollback_id),
        )
        return [r["id"] for r in rows]

    async def count_live_evidence_after(self, ts: datetime) -> int:
        return int(
            await self._scalar(
                "SELECT count(*) FROM knowledge_evidence WHERE account_id = :a"
                " AND created_at > :t AND revoked_at IS NULL",
                t=ts,
            )
        )

    async def snapshot_owner_state(self, day: Any) -> dict[UUID, str]:
        rows = await self._all("SELECT item_id, owner_state FROM knowledge_state_on(:a, :d)", d=day)
        return {r["item_id"]: r["owner_state"] for r in rows}

    async def set_owner_state(self, item_id: UUID, owner_state: str) -> None:
        await self.session.execute(
            text("UPDATE knowledge_items SET owner_state = :o WHERE account_id = :a AND id = :i"),
            {"a": self.account_id, "i": item_id, "o": owner_state},
        )

    # ── сид ────────────────────────────────────────────────────────────────────────────

    async def seed_items(self) -> list[dict[str, Any]]:
        return await self._all(
            "SELECT * FROM knowledge_items WHERE account_id = :a AND origin = 'seed'"
            " AND parent_item_id IS NULL ORDER BY created_at, id"
        )

    async def seed_keys(self) -> set[str]:
        rows = await self._all(
            "SELECT v.provenance ->> 'seed_key' AS k FROM knowledge_item_versions v"
            " WHERE v.account_id = :a AND v.provenance ? 'seed_key'"
        )
        return {r["k"] for r in rows}

    async def copied_parents(self) -> set[UUID]:
        rows = await self._all(
            "SELECT parent_item_id FROM knowledge_items WHERE account_id = :a"
            " AND parent_item_id IS NOT NULL"
        )
        return {r["parent_item_id"] for r in rows}

    async def item_scopes(self, item_id: UUID) -> dict[str, list[str]]:
        rows = await self._all(
            "SELECT facet, value FROM knowledge_item_scopes WHERE item_id = :i"
            " ORDER BY facet, value",
            i=item_id,
        )
        out: dict[str, list[str]] = {}
        for r in rows:
            out.setdefault(r["facet"], []).append(r["value"])
        return out


def as_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
