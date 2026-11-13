"""Служебные операции с БД для тестов (через asyncpg напрямую)."""

import asyncio
from typing import Any

import asyncpg
from sqlalchemy.engine import make_url


def with_database(url: str, name: str) -> str:
    """Тот же сервер, другая БД."""
    return make_url(url).set(database=name).render_as_string(hide_password=False)


def asyncpg_dsn(url: str) -> str:
    """URL SQLAlchemy (postgresql+asyncpg://) -> DSN asyncpg (postgresql://)."""
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


async def _fetch(url: str, sql: str, *args: Any) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(asyncpg_dsn(url))
    try:
        return list(await conn.fetch(sql, *args))
    finally:
        await conn.close()


def fetch(url: str, sql: str, *args: Any) -> list[asyncpg.Record]:
    """Выполнить запрос в отдельном соединении (для синхронных тестов и фикстур)."""
    return asyncio.run(_fetch(url, sql, *args))


def create_database(server_url: str, name: str, template: str | None = None) -> str:
    """Создать БД (из шаблона, если задан) и вернуть её URL."""
    suffix = f" TEMPLATE {template}" if template else ""
    fetch(server_url, f'CREATE DATABASE "{name}"{suffix}')
    return with_database(server_url, name)


def drop_database(server_url: str, name: str) -> None:
    fetch(server_url, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def table_names(url: str) -> set[str]:
    """Таблицы схемы public (без дочерних партиций)."""
    rows = fetch(
        url,
        """SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND NOT c.relispartition""",
    )
    return {r["relname"] for r in rows}


def view_names(url: str) -> set[str]:
    rows = fetch(url, "SELECT viewname FROM pg_views WHERE schemaname = 'public'")
    return {r["viewname"] for r in rows}
