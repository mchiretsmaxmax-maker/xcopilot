"""Запросы этапа 9C: роль владельца, подавление отклонённых знаний, автономия, сегменты, поиск,
карточка и доказательства. Прикладной код (`xcopilot.brain.owner/segments/digest`) ходит в БД
только отсюда."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from xcopilot.db.repos.brain import WORKING, _rows

_ITEM_COLS = (
    "k.id, k.statement, k.guidance, k.kind, k.layer, k.element, k.status, k.owner_state,"
    " k.risk_class, k.quality::float AS quality"
)


def like_pattern(query: str) -> str:
    """Подстрока для ILIKE: служебные символы экранируются."""
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class OwnerRepo:
    def __init__(self, session: AsyncSession, account_id: UUID) -> None:
        if not isinstance(account_id, UUID):
            raise ValueError("OwnerRepo: account_id обязателен (UUID)")
        self.session = session
        self.account_id = account_id

    async def _all(self, sql: str, **params: Any) -> list[dict[str, Any]]:
        return _rows(await self.session.execute(text(sql), {"a": self.account_id, **params}))

    async def _one(self, sql: str, **params: Any) -> dict[str, Any] | None:
        rows = await self._all(sql, **params)
        return rows[0] if rows else None

    # ── роль и настройки ──────────────────────────────────────────────────────────────

    async def role(self, user_id: UUID) -> str | None:
        row = await self._one(
            "SELECT role FROM account_members WHERE account_id = :a AND user_id = :u", u=user_id
        )
        return None if row is None else str(row["role"])

    async def autonomy(self) -> str:
        row = await self._one(
            "SELECT settings #>> '{brain,autonomy}' AS v FROM accounts WHERE id = :a"
        )
        return "manual" if row and row["v"] == "manual" else "auto"

    async def set_autonomy(self, value: str) -> None:
        await self.session.execute(
            text(
                "UPDATE accounts SET settings = jsonb_set(coalesce(settings, '{}'::jsonb),"
                " '{brain}', coalesce(settings -> 'brain', '{}'::jsonb)"
                " || CAST(:v AS jsonb), true) WHERE id = :a"
            ),
            {"a": self.account_id, "v": json.dumps({"autonomy": value})},
        )

    # ── решения владельца ─────────────────────────────────────────────────────────────

    async def has_owner_confirmation(self, item_id: UUID) -> bool:
        row = await self._one(
            "SELECT 1 AS x FROM knowledge_evidence WHERE account_id = :a AND item_id = :i"
            " AND basis = 'owner_decision' AND polarity = 1 AND revoked_at IS NULL LIMIT 1",
            i=item_id,
        )
        return row is not None

    async def rejected_similar(
        self, vec: Sequence[float], *, since: datetime, threshold: float
    ) -> bool:
        """Есть ли отклонённое после `since` знание, близкое по вектору к `vec`."""
        row = await self._one(
            "SELECT 1 AS x FROM knowledge_embeddings e"
            " JOIN embedding_models m ON m.id = e.model_id AND m.active"
            " JOIN knowledge_items k ON k.id = e.item_id"
            " WHERE e.account_id = :a AND k.status = 'rejected'"
            " AND 1 - (e.vec <=> CAST(:v AS vector)) >= :th"
            " AND EXISTS (SELECT 1 FROM knowledge_transitions t WHERE t.item_id = k.id"
            "   AND t.to_status = 'rejected' AND t.reverted_at IS NULL AND t.ts >= :since)"
            " LIMIT 1",
            v="[" + ",".join(f"{x:.6f}" for x in vec) + "]",
            th=threshold,
            since=since,
        )
        return row is not None

    async def evidence_row(self, evidence_id: UUID) -> dict[str, Any] | None:
        return await self._one(
            "SELECT id, item_id FROM knowledge_evidence WHERE account_id = :a AND id = :e",
            e=evidence_id,
        )

    async def link(self, link_id: UUID) -> dict[str, Any] | None:
        return await self._one(
            "SELECT id, from_item_id, to_item_id, relation, status FROM knowledge_links"
            " WHERE account_id = :a AND id = :l",
            l=link_id,
        )

    async def transition(self, transition_id: UUID) -> dict[str, Any] | None:
        return await self._one(
            "SELECT id, item_id, actor, from_status, to_status, rule, reverted_at"
            " FROM knowledge_transitions WHERE account_id = :a AND id = :t",
            t=transition_id,
        )

    # ── сегменты ──────────────────────────────────────────────────────────────────────

    async def copy_default_segments(self, global_id: UUID) -> int:
        rows = await self._all(
            "INSERT INTO segments (account_id, key, title, filter, position, is_default)"
            " SELECT :a, key, title, filter, position, true FROM segments"
            " WHERE account_id = :g AND is_default ON CONFLICT (account_id, key) DO NOTHING"
            " RETURNING id",
            g=global_id,
        )
        return len(rows)

    async def add_segment(self, key: str, title: str, flt: dict[str, Any], position: int) -> bool:
        rows = await self._all(
            "INSERT INTO segments (account_id, key, title, filter, position, is_default)"
            " VALUES (:a, :k, :t, CAST(:f AS jsonb), :p, true)"
            " ON CONFLICT (account_id, key) DO NOTHING RETURNING id",
            k=key,
            t=title,
            f=json.dumps(flt),
            p=position,
        )
        return bool(rows)

    async def segment_exists(self, key: str) -> bool:
        return (
            await self._one("SELECT 1 AS x FROM segments WHERE account_id = :a AND key = :k", k=key)
            is not None
        )

    async def segment_total(self, key: str) -> int:
        row = await self._one(
            "SELECT count(*) AS n FROM v_segment_items WHERE account_id = :a AND segment_key = :k"
            " AND status = ANY(:st)",
            k=key,
            st=list(WORKING),
        )
        return int(row["n"]) if row else 0

    async def segment_page(self, key: str, *, limit: int, offset: int) -> list[dict[str, Any]]:
        return await self._all(
            f"SELECT {_ITEM_COLS} FROM v_segment_items s JOIN knowledge_items k ON k.id = s.item_id"
            " WHERE s.account_id = :a AND s.segment_key = :k AND s.status = ANY(:st)"
            " ORDER BY k.quality DESC, k.id LIMIT :n OFFSET :o",
            k=key,
            st=list(WORKING),
            n=limit,
            o=offset,
        )

    async def segment_counts(self) -> list[dict[str, Any]]:
        return await self._all(
            "SELECT g.key, g.title, count(s.item_id) FILTER (WHERE s.status = ANY(:st)) AS n"
            " FROM segments g LEFT JOIN v_segment_items s"
            "   ON s.account_id = g.account_id AND s.segment_key = g.key"
            " WHERE g.account_id = :a GROUP BY g.key, g.title, g.position"
            " ORDER BY g.position, g.key",
            st=list(WORKING),
        )

    async def status_counts(self) -> dict[str, int]:
        rows = await self._all(
            "SELECT status, count(*) AS n FROM knowledge_items WHERE account_id = :a"
            " GROUP BY status"
        )
        return {r["status"]: int(r["n"]) for r in rows}

    async def material_count(self) -> int:
        row = await self._one(
            "SELECT count(*) AS n FROM sources WHERE account_id = :a AND removed_at IS NULL"
        )
        return int(row["n"]) if row else 0

    async def conflict_count(self) -> int:
        row = await self._one(
            "SELECT count(*) AS n FROM knowledge_links WHERE account_id = :a"
            " AND status = 'open' AND relation = 'contradicts'"
        )
        return int(row["n"]) if row else 0

    async def class_a_pending(self) -> list[dict[str, Any]]:
        return await self._all(
            f"SELECT {_ITEM_COLS} FROM knowledge_items k WHERE k.account_id = :a"
            " AND k.risk_class = 'A' AND k.status = 'candidate' AND k.owner_state = 'none'"
            " ORDER BY k.created_at, k.id"
        )

    async def search(self, pattern: str, limit: int) -> list[dict[str, Any]]:
        return await self._all(
            f"SELECT {_ITEM_COLS} FROM knowledge_items k WHERE k.account_id = :a"
            " AND k.status = ANY(:st) AND (k.statement ILIKE :p OR k.guidance ILIKE :p)"
            " ORDER BY k.quality DESC, k.id LIMIT :n",
            st=list(WORKING),
            p=pattern,
            n=limit,
        )

    # ── карточка и доказательства ─────────────────────────────────────────────────────

    async def card(self, item_id: UUID) -> dict[str, Any] | None:
        return await self._one(
            f"SELECT {_ITEM_COLS}, k.version, k.eff_conf::float AS eff_conf,"
            " k.fit_conf::float AS fit_conf, k.evidence_n, k.own_support, k.own_contra,"
            " k.origin, k.target_metric,"
            " (SELECT count(DISTINCT e.source_id) FROM knowledge_evidence e WHERE e.item_id = k.id"
            "   AND e.dimension = 'eff' AND e.revoked_at IS NULL) AS posts,"
            " (SELECT count(DISTINCT e.author_id) FROM knowledge_evidence e WHERE e.item_id = k.id"
            "   AND e.dimension = 'eff' AND e.revoked_at IS NULL) AS authors,"
            " EXISTS (SELECT 1 FROM knowledge_evidence e WHERE e.item_id = k.id"
            "   AND e.dimension = 'fit' AND e.revoked_at IS NULL) AS fit_known"
            " FROM knowledge_items k WHERE k.account_id = :a AND k.id = :i",
            i=item_id,
        )

    async def evidence(self, item_id: UUID, limit: int) -> list[dict[str, Any]]:
        return await self._all(
            "SELECT e.id, e.source_id, au.handle AS author_handle, s.url,"
            " left(d.text, 120) AS snippet, e.basis, e.dimension, e.polarity, e.outcome_tier,"
            " e.observed_at FROM knowledge_evidence e"
            " LEFT JOIN sources s ON s.id = e.source_id"
            " LEFT JOIN authors au ON au.id = e.author_id"
            " LEFT JOIN LATERAL (SELECT x.text FROM documents x WHERE x.source_id = e.source_id"
            "   ORDER BY x.id LIMIT 1) d ON true"
            " WHERE e.account_id = :a AND e.item_id = :i AND e.revoked_at IS NULL"
            " ORDER BY e.observed_at DESC, e.id LIMIT :n",
            i=item_id,
            n=limit,
        )

    # ── сводка ────────────────────────────────────────────────────────────────────────

    async def count_materials_between(self, start: datetime, end: datetime) -> int:
        row = await self._one(
            "SELECT count(*) AS n FROM sources WHERE account_id = :a AND removed_at IS NULL"
            " AND created_at >= :s AND created_at < :e",
            s=start,
            e=end,
        )
        return int(row["n"]) if row else 0

    async def count_new_items_between(self, start: datetime, end: datetime) -> int:
        row = await self._one(
            "SELECT count(*) AS n FROM knowledge_items WHERE account_id = :a"
            " AND status <> 'merged' AND origin <> 'seed' AND created_at >= :s AND created_at < :e",
            s=start,
            e=end,
        )
        return int(row["n"]) if row else 0

    async def transitions_between(
        self,
        start: datetime,
        end: datetime,
        *,
        to_status: str | None = None,
        rule: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT t.id, t.item_id, k.statement FROM knowledge_transitions t"
            " JOIN knowledge_items k ON k.id = t.item_id"
            " WHERE t.account_id = :a AND t.actor = 'system' AND t.reverted_at IS NULL"
            " AND t.ts >= :s AND t.ts < :e"
        )
        params: dict[str, Any] = {"s": start, "e": end}
        if to_status is not None:
            sql += " AND t.to_status = :ts"
            params["ts"] = to_status
        if rule is not None:
            sql += " AND t.rule = :r"
            params["r"] = rule
        return await self._all(sql + " ORDER BY t.ts, t.id", **params)

    async def open_conflict_statements(self) -> list[dict[str, Any]]:
        return await self._all(
            "SELECT l.id, f.statement AS first, t.statement AS second FROM knowledge_links l"
            " JOIN knowledge_items f ON f.id = l.from_item_id"
            " JOIN knowledge_items t ON t.id = l.to_item_id"
            " WHERE l.account_id = :a AND l.status = 'open' AND l.relation = 'contradicts'"
            " ORDER BY l.created_at, l.id"
        )

    async def applied_posts_between(self, start: datetime, end: datetime) -> int:
        row = await self._one(
            "SELECT count(DISTINCT draft_id) AS n FROM knowledge_applications"
            " WHERE account_id = :a AND applied IS TRUE AND created_at >= :s AND created_at < :e",
            s=start,
            e=end,
        )
        return int(row["n"]) if row else 0
