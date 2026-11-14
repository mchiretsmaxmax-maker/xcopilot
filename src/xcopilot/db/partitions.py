"""Месячные партиции журналов и партиция DEFAULT.

Журналы (`events`, `post_metric_snapshots`, `llm_calls`, `x_api_calls`) разбиты по месяцам
по колонке `ts` (UTC), снимки знаний `knowledge_snapshots` — по `snapshot_date`.
Миграция создаёт только `<таблица>_default`; месячные партиции
`<таблица>_pYYYYMM` создаёт `ensure_partitions` — при старте и ежедневной задачей
`db.ensure_partitions` (идемпотентно). Строки, попавшие в DEFAULT, переносятся в новую
партицию при её создании; `default_partition_rowcount` — сигнал, что партиций не хватает.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

PARTITIONED_TABLES: tuple[str, ...] = (
    "events",
    "post_metric_snapshots",
    "llm_calls",
    "x_api_calls",
    "knowledge_snapshots",
)
# Колонка-ключ партиционирования; по умолчанию ts (журналы), у снимков знаний — дата снимка.
PARTITION_KEYS: dict[str, str] = {"knowledge_snapshots": "snapshot_date"}
PARTITIONS_JOB_KIND = "db.ensure_partitions"
_CHILD = re.compile(rf"^({'|'.join(PARTITIONED_TABLES)})_(p\d{{6}}|default)$")


def partition_name(table: str, year: int, month: int) -> str:
    return f"{table}_p{year:04d}{month:02d}"


def default_partition_name(table: str) -> str:
    return f"{table}_default"


def is_partition_name(name: str) -> bool:
    """Имя дочерней партиции журнала (не сравнивается с моделями в автогенерации)."""
    return bool(_CHILD.match(name))


def _add_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def _utc(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def _check(table: str) -> None:
    if table not in PARTITIONED_TABLES:
        raise ValueError(f"таблица {table!r} не партиционирована")


async def ensure_partitions(
    conn: AsyncConnection | AsyncSession, table: str, months_ahead: int = 2, *, today: date
) -> list[str]:
    """Создать партиции текущего и следующих months_ahead месяцев; вернуть созданные."""
    _check(table)
    default = default_partition_name(table)
    created: list[str] = []
    for offset in range(months_ahead + 1):
        lo = _add_months(today, offset)
        hi = _add_months(lo, 1)
        name = partition_name(table, lo.year, lo.month)
        exists = (await conn.execute(text("SELECT to_regclass(:n)"), {"n": name})).scalar()
        if exists is not None:
            continue
        bounds = {"lo": _utc(lo), "hi": _utc(hi)}
        key = PARTITION_KEYS.get(table, "ts")
        where = f"{key} >= :lo AND {key} < :hi"
        stray = (
            await conn.execute(text(f"SELECT count(*) FROM {default} WHERE {where}"), bounds)
        ).scalar_one()
        buffer = f"_xc_move_{name}"
        if stray:
            await conn.execute(
                text(
                    f"CREATE TEMP TABLE {buffer} ON COMMIT DROP AS"
                    f" SELECT * FROM {default} WHERE {where}"
                ),
                bounds,
            )
            await conn.execute(text(f"DELETE FROM {default} WHERE {where}"), bounds)
        await conn.execute(
            text(
                f"CREATE TABLE {name} PARTITION OF {table}"
                f" FOR VALUES FROM ('{lo.isoformat()} 00:00:00+00')"
                f" TO ('{hi.isoformat()} 00:00:00+00')"
            )
        )
        await conn.execute(
            text(f"COMMENT ON TABLE {name} IS 'Партиция {table} за {lo:%Y-%m} (UTC)'")
        )
        if stray:
            await conn.execute(text(f"INSERT INTO {table} SELECT * FROM {buffer}"))
            await conn.execute(text(f"DROP TABLE {buffer}"))
        created.append(name)
    return created


async def ensure_all_partitions(
    conn: AsyncConnection | AsyncSession, months_ahead: int = 2, *, today: date
) -> dict[str, list[str]]:
    """ensure_partitions для всех партиционированных таблиц."""
    return {
        table: await ensure_partitions(conn, table, months_ahead, today=today)
        for table in PARTITIONED_TABLES
    }


async def default_partition_rowcount(conn: AsyncConnection | AsyncSession, table: str) -> int:
    """Сколько строк попало в партицию DEFAULT (больше нуля — не хватает партиций)."""
    _check(table)
    result = await conn.execute(text(f"SELECT count(*) FROM {default_partition_name(table)}"))
    return int(result.scalar_one())
