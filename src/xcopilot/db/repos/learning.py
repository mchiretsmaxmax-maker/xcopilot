"""Запросы обучения на правках (этап 13): ревью после границы, окна решений, подтверждения."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

DECISIONS = ("approve", "edit", "reject")
_REVIEW_COLS = (
    "r.id, r.draft_id, r.action, r.reason_code, r.text_before, r.text_after,"
    " r.edit_distance::float AS edit_distance, r.comment, r.created_at"
)


def _rows(result: Any) -> list[dict[str, Any]]:
    return [dict(r._mapping) for r in result.all()]


class LearningRepo:
    """Чтения одного аккаунта; всё ограничено account_id."""

    def __init__(self, session: AsyncSession, account_id: UUID) -> None:
        if not isinstance(account_id, UUID):
            raise ValueError("LearningRepo: account_id обязателен (UUID)")
        self.session = session
        self.account_id = account_id

    async def _all(self, sql: str, **params: Any) -> list[dict[str, Any]]:
        return _rows(await self.session.execute(text(sql), {"a": self.account_id, **params}))

    async def last_run_at(self) -> datetime | None:
        """Время последнего прохода обучения (событие `learning.proposed`)."""
        row = await self.session.execute(
            text("SELECT max(ts) FROM events WHERE account_id = :a AND type = 'learning.proposed'"),
            {"a": self.account_id},
        )
        value = row.scalar()
        return value if isinstance(value, datetime) else None

    async def reviews_after(self, since: datetime | None, limit: int = 200) -> list[dict[str, Any]]:
        """Ревью строго после `since` (или все), от старых к новым."""
        return await self._all(
            f"SELECT {_REVIEW_COLS} FROM reviews r WHERE r.account_id = :a"
            " AND (CAST(:since AS timestamptz) IS NULL"
            " OR r.created_at > CAST(:since AS timestamptz))"
            " ORDER BY r.created_at, r.id LIMIT :n",
            since=since,
            n=limit,
        )

    async def review(self, review_id: UUID) -> dict[str, Any] | None:
        rows = await self._all(
            f"SELECT {_REVIEW_COLS} FROM reviews r WHERE r.account_id = :a AND r.id = :r",
            r=review_id,
        )
        return rows[0] if rows else None

    async def edit_reviews_applying(
        self, item_id: UUID, *, since: datetime, limit: int
    ) -> list[dict[str, Any]]:
        """Правки черновиков с применённым знанием: последние `limit`, от старых к новым."""
        rows = await self._all(
            f"SELECT {_REVIEW_COLS} FROM reviews r WHERE r.account_id = :a AND r.action = 'edit'"
            " AND r.text_after IS NOT NULL AND r.created_at >= :since AND EXISTS ("
            "  SELECT 1 FROM knowledge_applications ka WHERE ka.account_id = :a"
            "  AND ka.draft_id = r.draft_id AND ka.item_id = :i AND ka.applied IS NOT FALSE)"
            " ORDER BY r.created_at DESC, r.id DESC LIMIT :n",
            i=item_id,
            since=since,
            n=limit,
        )
        return rows[::-1]

    async def decisions_before(self, at: datetime, limit: int) -> list[str]:
        """Действия последних `limit` решений до `at` (approve / edit / reject)."""
        rows = await self._all(
            "SELECT action FROM reviews WHERE account_id = :a AND action = ANY(:acts)"
            " AND created_at < :at ORDER BY created_at DESC, id DESC LIMIT :n",
            acts=list(DECISIONS),
            at=at,
            n=limit,
        )
        return [r["action"] for r in rows]

    async def decisions_after(self, at: datetime, limit: int) -> list[str]:
        """Действия первых `limit` решений с момента `at`."""
        rows = await self._all(
            "SELECT action FROM reviews WHERE account_id = :a AND action = ANY(:acts)"
            " AND created_at >= :at ORDER BY created_at, id LIMIT :n",
            acts=list(DECISIONS),
            at=at,
            n=limit,
        )
        return [r["action"] for r in rows]

    async def confirmed_at(self, item_id: UUID) -> datetime | None:
        """Когда владелец впервые взял знание в работу или закрепил (событие решения)."""
        row = await self.session.execute(
            text(
                "SELECT min(ts) FROM events WHERE account_id = :a"
                " AND type = 'knowledge.owner_decision' AND entity_id = :i"
                " AND props ->> 'decision' IN ('confirm', 'pin')"
            ),
            {"a": self.account_id, "i": item_id},
        )
        value = row.scalar()
        return value if isinstance(value, datetime) else None

    async def removal_candidates(self, draft_id: UUID) -> list[dict[str, Any]]:
        """Знания с детектором, применённые в черновике: id, слой, детектор."""
        return await self._all(
            "SELECT DISTINCT k.id, k.layer, k.detector FROM knowledge_applications ka"
            " JOIN knowledge_items k ON k.id = ka.item_id"
            " WHERE ka.account_id = :a AND ka.draft_id = :d AND ka.applied IS NOT FALSE"
            " AND k.detector ->> 'type' IN ('feature', 'rubric')",
            d=draft_id,
        )
