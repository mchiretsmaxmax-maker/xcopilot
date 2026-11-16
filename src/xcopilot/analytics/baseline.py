"""Базовый уровень: медиана за 30 дней по формату (этап 14, задача 5). Заглушка."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from xcopilot.db.uow import DbSession


@dataclass(frozen=True)
class Baseline:
    fmt: str
    days: int
    median_impressions: float | None
    sample_size: int


async def baseline(
    session: DbSession, account_id: UUID, fmt: str, days: int = 30, *, now: datetime
) -> Baseline:
    raise NotImplementedError
