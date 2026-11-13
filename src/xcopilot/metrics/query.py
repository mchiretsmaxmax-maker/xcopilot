"""Чтение метрик: ряд, значение, разрез.

Свёртки лежат в metric_values; период, который ещё не закончился (сегодня по поясу
аккаунта, для недельных — текущая неделя), считается на лету. Нет данных — `None`, не ноль.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from xcopilot.clock import Clock, SystemClock
from xcopilot.db.repos import MetricDefsRepo, MetricValuesRepo
from xcopilot.db.uow import DbSession
from xcopilot.metrics.rollup import (
    MetricRow,
    _compute,
    account_tz,
    dims_hash,
    local_today,
    week_start,
)


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


@dataclass(frozen=True, slots=True)
class _Source:
    grain: str
    sql: str
    tz: str
    live_from: date  # с этого периода данные считаются на лету


async def _source(session: DbSession, account_id: UUID, key: str, clock: Clock | None) -> _Source:
    definition = await MetricDefsRepo(session).require(key)
    tz = await account_tz(session, account_id)
    today = local_today((clock or SystemClock()).now(), tz)
    live_from = week_start(today) if definition.grain == "week" else today
    return _Source(definition.grain, definition.formula_sql, tz, live_from)


def _periods(grain: str, date_from: date, date_to: date) -> list[date]:
    if grain == "week":
        first, step = week_start(date_from), 7
    else:
        first, step = date_from, 1
    out: list[date] = []
    current = first
    while current <= date_to:
        out.append(current)
        current += timedelta(days=step)
    return out


async def _rows(
    session: DbSession,
    account_id: UUID,
    key: str,
    src: _Source,
    date_from: date,
    date_to: date,
) -> list[MetricRow]:
    """Строки периодов диапазона: хранимые и (для незавершённых периодов) живые."""
    rows: list[MetricRow] = []
    stored_to = min(date_to, src.live_from - timedelta(days=1))
    if date_from <= stored_to:
        for r in await MetricValuesRepo(session, account_id).stored(
            key, src.grain, date_from, stored_to
        ):
            rows.append(MetricRow(r.period_start, dict(r.dims), r.value, r.sample_size))
    if date_to >= src.live_from:
        live = await _compute(
            session,
            src.sql,
            src.grain,
            key,
            account_id,
            src.tz,
            max(date_from, src.live_from),
            date_to,
        )
        rows.extend(live)
    return rows


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
    """Точка на каждый период диапазона (день или неделя); нет свёртки — value None."""
    src = await _source(session, account_id, key, clock)
    wanted = dims_hash(dims or {})
    by_period = {
        r.period_start: r
        for r in await _rows(session, account_id, key, src, date_from, date_to)
        if dims_hash(r.dims) == wanted
    }
    points: list[MetricPoint] = []
    for period in _periods(src.grain, date_from, date_to):
        row = by_period.get(period)
        points.append(
            MetricPoint(period, None, 0)
            if row is None
            else MetricPoint(period, row.value, row.sample_size)
        )
    return points


async def get_value(
    session: DbSession,
    account_id: UUID,
    key: str,
    period: date,
    dims: dict[str, Any] | None = None,
    *,
    clock: Clock | None = None,
) -> MetricPoint:
    """Значение за один период (для недельных — неделя, куда входит period)."""
    (point,) = await get_series(session, account_id, key, period, period, dims, clock=clock)
    return point


async def get_breakdown(
    session: DbSession,
    account_id: UUID,
    key: str,
    period: date,
    dimension: str,
    *,
    clock: Clock | None = None,
) -> list[BreakdownRow]:
    """Разрез периода по измерению: по строке на каждое значение."""
    src = await _source(session, account_id, key, clock)
    start = week_start(period) if src.grain == "week" else period
    rows = [
        r
        for r in await _rows(session, account_id, key, src, start, start)
        if dimension in r.dims and r.period_start == start
    ]
    return sorted(
        (BreakdownRow(str(r.dims[dimension]), r.value, r.sample_size) for r in rows),
        key=lambda b: b.label,
    )
