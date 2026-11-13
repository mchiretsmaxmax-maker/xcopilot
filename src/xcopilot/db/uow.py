"""Unit of Work: одна транзакция на блок кода."""

from __future__ import annotations

from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class UnitOfWork:
    """Контекстный менеджер: коммит при успехе, откат при исключении."""

    session: AsyncSession

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        raise NotImplementedError

    async def __aenter__(self) -> Self:
        raise NotImplementedError

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        raise NotImplementedError
