"""Чтение метрик: ряд, значение, разрез."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from xcopilot.clock import Clock
from xcopilot.db.uow import DbSession


@dataclass(frozen=True, slots=True)
class MetricPoint:
    period_start: date
    value: Decimal | None
    sample_size: int


@dataclass(frozen=True, slots=True)
class BreakdownRow:
    label: str
    value: Decimal | None
    sample_size: int


async def get_series(
    session: DbSession,
    account_id: UUID,
    key: str,
    date_from: date,
    date_to: date,
    dims: dict[str, Any] | None = None,
    *,
    clock: Clock | None = None,
) -> list[MetricPoint]:
    """Точка на каждый период диапазона; нет свёртки — value None (не ноль)."""
    raise NotImplementedError


async def get_value(
    session: DbSession,
    account_id: UUID,
    key: str,
    period: date,
    dims: dict[str, Any] | None = None,
    *,
    clock: Clock | None = None,
) -> MetricPoint:
    raise NotImplementedError


async def get_breakdown(
    session: DbSession,
    account_id: UUID,
    key: str,
    period: date,
    dimension: str,
    *,
    clock: Clock | None = None,
) -> list[BreakdownRow]:
    raise NotImplementedError
