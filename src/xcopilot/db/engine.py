"""Движок SQLAlchemy (asyncpg) и фабрика сессий."""

from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy.ext.asyncio import (
    create_async_engine as _create_async_engine,
)

from xcopilot.config import Settings

POOL_SIZE = 5
MAX_OVERFLOW = 5


def create_engine(settings: Settings | str) -> AsyncEngine:
    """Создать async-движок с пулом соединений (asyncpg)."""
    url = settings if isinstance(settings, str) else settings.database_url
    return _create_async_engine(
        url,
        pool_size=POOL_SIZE,
        max_overflow=MAX_OVERFLOW,
        pool_pre_ping=True,
        connect_args={"server_settings": {"timezone": "UTC", "application_name": "xcopilot"}},
    )


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Фабрика сессий; объекты не протухают после коммита."""
    return async_sessionmaker(engine, expire_on_commit=False)


async def ping(database_url: str) -> None:
    """Проверить, что БД отвечает на SELECT 1 (не дольше 3 секунд)."""
    engine = _create_async_engine(database_url)
    try:
        async with asyncio.timeout(3.0), engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    finally:
        await engine.dispose()
