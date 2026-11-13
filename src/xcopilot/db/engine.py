"""Движок SQLAlchemy (asyncpg) и фабрика сессий."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from xcopilot.config import Settings


def create_engine(settings: Settings | str) -> AsyncEngine:
    """Создать async-движок с пулом соединений."""
    raise NotImplementedError


def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Фабрика сессий (expire_on_commit=False)."""
    raise NotImplementedError


async def ping(database_url: str) -> None:
    """Проверить, что БД отвечает на SELECT 1."""
    raise NotImplementedError
