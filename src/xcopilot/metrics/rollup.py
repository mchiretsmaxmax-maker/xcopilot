"""Свёртки: исполнение формул в READ ONLY и запись в metric_values.

Формуле доступны параметры (передаются только те, что есть в тексте):
`:account_id`, `:tz` (пояс аккаунта), `:from` / `:to` (timestamptz, полуинтервал [from, to)
по локальным дням аккаунта), `:day_from` / `:day_to` (те же границы датами, `:day_to` не включён).
Недельные метрики всегда считаются за целые недели пн–вс.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from xcopilot.clock import Clock, SystemClock
from xcopilot.db.repos import AccountsRepo, MetricDefsRepo, MetricValuesRepo
from xcopilot.db.uow import DbSession
from xcopilot.errors import NotFound
from xcopilot.metrics.catalog import REQUIRED_COLUMNS
from xcopilot.metrics.errors import FormulaExecutionError

_BIND = re.compile(r"(?<![:\w\\]):(\w+)(?!:)")


@dataclass(frozen=True, slots=True)
class MetricRow:
    period_start: date
    dims: dict[str, Any]
    value: Decimal | None
    sample_size: int


def dims_hash(dims: dict[str, Any]) -> str:
    """Стабильный хэш измерений (ключ строки свёртки)."""
    return hashlib.sha256(json.dumps(dims, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def week_start(day: date) -> date:
    """Понедельник недели, в которую входит day."""
    return day - timedelta(days=day.weekday())


def local_today(now: datetime, tz: str) -> date:
    return now.astimezone(ZoneInfo(tz)).date()


def _params(sql: str, account_id: UUID, tz: str, date_from: date, date_to: date) -> dict[str, Any]:
    zone = ZoneInfo(tz)
    end = date_to + timedelta(days=1)
    available: dict[str, Any] = {
        "account_id": account_id,
        "tz": tz,
        "from": datetime.combine(date_from, time.min, zone),
        "to": datetime.combine(end, time.min, zone),
        "day_from": date_from,
        "day_to": end,
    }
    return {name: available[name] for name in set(_BIND.findall(sql)) if name in available}


def _to_row(raw: dict[str, Any], key: str) -> MetricRow:
    missing = [c for c in REQUIRED_COLUMNS if c not in raw]
    if missing:
        raise FormulaExecutionError(f"{key}: формула не вернула колонки {', '.join(missing)}")
    period = raw["period_start"]
    value = raw["value"]
    return MetricRow(
        period_start=period.date() if isinstance(period, datetime) else period,
        dims=dict(raw["dims"] or {}),
        value=None if value is None else Decimal(str(value)),
        sample_size=int(raw["sample_size"] or 0),
    )


async def account_tz(session: DbSession, account_id: UUID) -> str:
    account = await AccountsRepo(session).get(account_id)
    if account is None:
        raise NotFound(f"аккаунт {account_id} не найден")
    return str(account.tz)


async def _compute(
    session: DbSession,
    sql: str,
    grain: str,
    key: str,
    account_id: UUID,
    tz: str,
    date_from: date,
    date_to: date,
) -> list[MetricRow]:
    if grain == "week":
        date_from, date_to = week_start(date_from), week_start(date_to) + timedelta(days=6)
    raw = await MetricValuesRepo(session, account_id).run_formula(
        sql, _params(sql, account_id, tz, date_from, date_to)
    )
    rows = [_to_row(r, key) for r in raw]
    rows.sort(key=lambda r: (r.period_start, dims_hash(r.dims)))
    return rows


async def compute(
    session: DbSession, key: str, account_id: UUID, date_from: date, date_to: date
) -> list[MetricRow]:
    """Посчитать метрику за локальные дни date_from..date_to включительно, ничего не записывая."""
    definition = await MetricDefsRepo(session).require(key)
    tz = await account_tz(session, account_id)
    return await _compute(
        session, definition.formula_sql, definition.grain, key, account_id, tz, date_from, date_to
    )


async def _rollup(
    session: DbSession,
    account_id: UUID,
    date_from: date,
    date_to: date,
    clock: Clock | None,
) -> int:
    now = (clock or SystemClock()).now().astimezone(UTC)
    tz = await account_tz(session, account_id)
    values = MetricValuesRepo(session, account_id)
    written = 0
    for definition in await MetricDefsRepo(session).active():
        d_from, d_to = date_from, date_to
        if definition.grain == "week":
            d_from, d_to = week_start(d_from), week_start(d_to) + timedelta(days=6)
        rows = await _compute(
            session,
            definition.formula_sql,
            definition.grain,
            definition.key,
            account_id,
            tz,
            d_from,
            d_to,
        )
        await values.upsert(
            [
                {
                    "metric_key": definition.key,
                    "grain": definition.grain,
                    "period_start": r.period_start,
                    "dims": r.dims,
                    "dims_hash": dims_hash(r.dims),
                    "value": r.value,
                    "sample_size": r.sample_size,
                    "definition_version": definition.version,
                    "computed_at": now,
                }
                for r in rows
            ]
        )
        await values.delete_missing(
            definition.key,
            definition.grain,
            d_from,
            d_to,
            {(r.period_start, dims_hash(r.dims)) for r in rows},
        )
        written += len(rows)
    return written


async def run_rollups(
    session: DbSession, account_id: UUID, day: date, *, clock: Clock | None = None
) -> int:
    """Посчитать и записать все активные метрики за локальный день (недельные — за его неделю).

    Повторный запуск идемпотентен: совпадающие строки не меняются. Возвращает число строк.
    """
    return await _rollup(session, account_id, day, day, clock)


async def backfill(
    session: DbSession,
    account_id: UUID,
    date_from: date,
    date_to: date,
    *,
    clock: Clock | None = None,
) -> int:
    """Пересчитать все активные метрики за диапазон дней (устаревшие строки диапазона уходят)."""
    if date_to < date_from:
        raise ValueError("date_to раньше date_from")
    return await _rollup(session, account_id, date_from, date_to, clock)
