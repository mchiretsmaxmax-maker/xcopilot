"""Сигналы «зашло / не зашло» (ТЗ v3.1, раздел 1.3; этап 7A).

Наблюдения только добавляются (`material_signals`); исход считает `brain.outcomes` по
лучшему из них (представление v_material_best_signal).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from xcopilot.db.uow import DbSession

OWNER_LABELS = ("hit", "normal", "flop")


@dataclass(frozen=True)
class Signal:
    origin: str
    views: int | None = None
    likes: int | None = None
    replies: int | None = None
    reposts: int | None = None
    quotes: int | None = None
    bookmarks: int | None = None
    author_followers: int | None = None
    owner_label: str | None = None
    read_confidence: float = 1.0
    post_age_hours: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def parse_numbers(text: str) -> Signal | None:
    raise NotImplementedError


async def add_signal(
    session: DbSession,
    account_id: UUID,
    source_id: UUID,
    signal: Signal,
    *,
    user_id: UUID | None = None,
) -> UUID:
    raise NotImplementedError


async def label(
    session: DbSession, account_id: UUID, source_id: UUID, owner_label: str, user_id: UUID
) -> UUID:
    raise NotImplementedError


async def default_pick(session: DbSession, account_id: UUID, source_id: UUID) -> UUID | None:
    raise NotImplementedError
