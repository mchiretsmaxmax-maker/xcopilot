"""Запросы пакета знаний (этап 9B): кандидаты под задачу, карточки, версии, чужие тексты.

Выбор кандидатов делает БД: жёсткий фильтр по области, лучшие по близости к теме в каждом
смысловом слое (хватает на слоты и MMR) и все закреплённые. На 10 тыс. знаний в Python
приходит около двухсот строк.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from xcopilot.db.repos.brain import _rows

_FACETS = ("format", "goal", "lang", "niche", "archetype")
_CLASS = (
    "CASE WHEN u.owner_state = 'pinned' THEN 'pinned'"
    " WHEN u.status <> 'active' THEN NULL"
    " WHEN u.kind = 'anti_pattern' THEN 'anti'"
    " WHEN u.kind = 'structure' THEN 'structure'"
    " WHEN u.element = 'hook' THEN 'hook'"
    " WHEN u.element = 'device' THEN 'device'"
    " WHEN u.element = 'closer' THEN 'closer'"
    " WHEN u.layer = 'platform' OR u.element IN ('packaging', 'timing') THEN 'platform'"
    " WHEN u.owner_state = 'confirmed' AND u.element = 'general' THEN 'owner' END"
)
_COLS = (
    "u.id, u.version, u.kind, u.layer, u.element, u.statement, u.guidance, u.skeleton,"
    " u.status, u.owner_state, u.quality, u.own_support, u.own_contra, u.last_evidence_at,"
    " u.probation_started_at"
)
_SCOPES = (
    "coalesce((SELECT jsonb_object_agg(x.facet, x.vals) FROM (SELECT s.facet,"
    " jsonb_agg(s.value ORDER BY s.value) AS vals FROM knowledge_item_scopes s"
    " WHERE s.item_id = u.id GROUP BY s.facet) x), '{}'::jsonb) AS scopes"
)


def vec_literal(vec: Sequence[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def parse_vec(raw: Any) -> list[float] | None:
    if raw is None:
        return None
    return [float(x) for x in json.loads(raw)]


class PackRepo:
    def __init__(self, session: AsyncSession, account_id: UUID) -> None:
        if not isinstance(account_id, UUID):
            raise ValueError("PackRepo: account_id обязателен (UUID)")
        self.session = session
        self.account_id = account_id

    async def _all(self, sql: str, **params: Any) -> list[dict[str, Any]]:
        return _rows(await self.session.execute(text(sql), {"a": self.account_id, **params}))

    async def candidates(
        self, facets: Mapping[str, str], topic_vec: Sequence[float], *, per_class: int = 40
    ) -> list[dict[str, Any]]:
        """Допущенные знания, подходящие задаче по области: лучшие в каждом слое + закреплённые."""
        scope = "".join(
            f" AND (NOT EXISTS (SELECT 1 FROM knowledge_item_scopes sc WHERE sc.item_id = u.id"
            f" AND sc.facet = '{f}') OR EXISTS (SELECT 1 FROM knowledge_item_scopes sc"
            f" WHERE sc.item_id = u.id AND sc.facet = '{f}' AND lower(sc.value) = lower(:f_{f})))"
            for f in facets
            if f in _FACETS
        )
        # JIT на таком запросе компилируется дольше, чем он выполняется
        await self.session.execute(text("SET LOCAL jit = off"))
        picked = await self._all(
            "SELECT t.id, t.cls, t.rn FROM (SELECT u.id,"
            f"  {_CLASS} AS cls,"
            "  row_number() OVER (PARTITION BY"
            f"   {_CLASS}"
            "   ORDER BY coalesce(1 - (e.vec <=> qv.q), 0) * u.quality DESC, u.id)"
            "   AS rn"
            " FROM v_knowledge_usable u"
            " CROSS JOIN (SELECT CAST(:v AS vector) AS q) qv"
            " LEFT JOIN embedding_models m ON m.active"
            " LEFT JOIN knowledge_embeddings e ON e.item_id = u.id AND e.model_id = m.id"
            f" WHERE u.account_id = :a {scope}) t"
            " WHERE t.cls IS NOT NULL AND (t.cls = 'pinned' AND t.rn <= 20 OR t.rn <= :per)"
            " ORDER BY t.cls, t.rn",
            v=vec_literal(topic_vec),
            per=per_class,
            **{f"f_{k}": v for k, v in facets.items() if k in _FACETS},
        )
        if not picked:
            return []
        by_id = {
            r["id"]: r
            for r in await self._all(
                f"SELECT {_COLS}, {_SCOPES}, e.vec::text AS vec FROM knowledge_items u"
                " LEFT JOIN embedding_models m ON m.active"
                " LEFT JOIN knowledge_embeddings e ON e.item_id = u.id AND e.model_id = m.id"
                " WHERE u.account_id = :a AND u.id = ANY(:ids)",
                ids=[p["id"] for p in picked],
            )
        }
        rows = [{**by_id[p["id"]], "cls": p["cls"]} for p in picked]
        for r in rows:
            r["vec"] = parse_vec(r["vec"])
        return rows

    async def has_usable(self) -> bool:
        rows = await self._all("SELECT 1 FROM v_knowledge_usable u WHERE u.account_id = :a LIMIT 1")
        return bool(rows)

    async def duplicate_pairs(self, ids: Sequence[UUID]) -> set[frozenset[UUID]]:
        rows = await self._all(
            "SELECT from_item_id, to_item_id FROM knowledge_links WHERE account_id = :a"
            " AND relation = 'duplicates' AND from_item_id = ANY(:ids) AND to_item_id = ANY(:ids)",
            ids=list(ids),
        )
        return {frozenset((r["from_item_id"], r["to_item_id"])) for r in rows}

    async def foreign_texts(self, ids: Sequence[UUID]) -> dict[UUID, list[str]]:
        """Тексты чужих материалов, на которых держится знание (для защиты от переноса)."""
        rows = await self._all(
            "SELECT DISTINCT e.item_id, coalesce(d.text_clean, d.text) AS body"
            " FROM knowledge_evidence e JOIN sources s ON s.id = e.source_id"
            " JOIN documents d ON d.source_id = s.id"
            " WHERE e.account_id = :a AND e.item_id = ANY(:ids) AND e.revoked_at IS NULL"
            " AND s.stance <> 'own'",
            ids=list(ids),
        )
        out: dict[UUID, list[str]] = {}
        for r in rows:
            out.setdefault(r["item_id"], []).append(r["body"])
        return out

    async def cards(self, ids: Sequence[UUID]) -> list[dict[str, Any]]:
        return await self._all(
            "SELECT * FROM v_knowledge_cards WHERE account_id = :a AND id = ANY(:ids)",
            ids=list(ids),
        )

    async def all_cards(self) -> list[dict[str, Any]]:
        return await self._all("SELECT * FROM v_knowledge_cards WHERE account_id = :a ORDER BY id")

    async def search(
        self, vec: Sequence[float], filters: Mapping[str, str], *, k: int
    ) -> list[dict[str, Any]]:
        allowed = {"kind", "layer", "element", "status"}
        where = "".join(f" AND u.{f} = :x_{f}" for f in filters if f in allowed)
        return await self._all(
            f"SELECT {_COLS}, 1 - (e.vec <=> CAST(:v AS vector)) AS sim"
            " FROM v_knowledge_usable u"
            " JOIN embedding_models m ON m.active"
            " JOIN knowledge_embeddings e ON e.item_id = u.id AND e.model_id = m.id"
            f" WHERE u.account_id = :a {where}"
            " ORDER BY e.vec <=> CAST(:v AS vector), u.id LIMIT :k",
            v=vec_literal(vec),
            k=k,
            **{f"x_{f}": v for f, v in filters.items() if f in allowed},
        )

    async def versions(
        self, pairs: Sequence[tuple[UUID, int | None]]
    ) -> dict[UUID, dict[str, Any]]:
        """Тексты знаний на сохранённых версиях (None — текущая версия)."""
        out: dict[UUID, dict[str, Any]] = {}
        for item_id, version in pairs:
            rows = await self._all(
                "SELECT v.version, v.kind, v.layer, v.element, v.statement, v.guidance,"
                " v.skeleton FROM knowledge_item_versions v"
                " WHERE v.account_id = :a AND v.item_id = :i"
                " AND (CAST(:ver AS int) IS NULL OR v.version = :ver)"
                " ORDER BY v.version DESC LIMIT 1",
                i=item_id,
                ver=version,
            )
            if rows:
                out[item_id] = rows[0]
        return out

    async def pack(self, pack_id: UUID) -> dict[str, Any] | None:
        rows = await self._all(
            "SELECT id, consumer, task, schema_version, taxonomy_version, items, token_count,"
            " created_at FROM knowledge_packs WHERE account_id = :a AND id = :p",
            p=pack_id,
        )
        return rows[0] if rows else None
