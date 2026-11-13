"""Свёртки: исполнение формул в READ ONLY и запись в metric_values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from xcopilot.clock import Clock
from xcopilot.db.uow import DbSession


@dataclass(frozen=True, slots=True)
class MetricRow:
    period_start: date
    dims: dict[str, Any]
    value: Decimal | None
    sample_size: int


def dims_hash(dims: dict[str, Any]) -> str:
    raise NotImplementedError


async def compute(
    session: DbSession, key: str, account_id: UUID, date_from: date, date_to: date
) -> list[MetricRow]:
    """Посчитать метрику за локальные дни date_from..date_to включительно, ничего не записывая."""
    raise NotImplementedError


async def run_rollups(
    session: DbSession, account_id: UUID, day: date, *, clock: Clock | None = None
) -> int:
    """Посчитать и записать все активные метрики за локальный день (недельные — за его неделю)."""
    raise NotImplementedError


async def backfill(
    session: DbSession,
    account_id: UUID,
    date_from: date,
    date_to: date,
    *,
    clock: Clock | None = None,
) -> int:
    """Пересчитать все активные метрики за диапазон дней."""
    raise NotImplementedError


def local_today(now: datetime, tz: str) -> date:
    raise NotImplementedError
