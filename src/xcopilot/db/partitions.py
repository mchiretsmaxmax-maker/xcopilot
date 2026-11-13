"""Месячные партиции журналов и партиция DEFAULT."""

from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

PARTITIONED_TABLES: tuple[str, ...] = ()


async def ensure_partitions(
    conn: AsyncConnection | AsyncSession, table: str, months_ahead: int = 2, *, today: date
) -> list[str]:
    """Создать партиции текущего и следующих months_ahead месяцев; вернуть созданные."""
    raise NotImplementedError


async def ensure_all_partitions(
    conn: AsyncConnection | AsyncSession, months_ahead: int = 2, *, today: date
) -> dict[str, list[str]]:
    """ensure_partitions для всех партиционированных таблиц."""
    raise NotImplementedError


async def default_partition_rowcount(conn: AsyncConnection | AsyncSession, table: str) -> int:
    """Сколько строк попало в партицию DEFAULT."""
    raise NotImplementedError
