"""Unit of Work: коммит при успехе, откат при исключении."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from xcopilot.db.repos import AccountsRepo
from xcopilot.db.uow import UnitOfWork


async def test_uow_commits_on_success_and_rolls_back_on_error(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with UnitOfWork(session_factory) as uow:
        kept = await AccountsRepo(uow.session).create(handle="kept")
    with pytest.raises(RuntimeError):
        async with UnitOfWork(session_factory) as uow:
            await AccountsRepo(uow.session).create(handle="lost")
            raise RuntimeError("boom")
    async with UnitOfWork(session_factory) as uow:
        repo = AccountsRepo(uow.session)
        found = await repo.get_by_handle("kept")
        assert found is not None and found.id == kept.id
        assert await repo.get_by_handle("lost") is None


async def test_uow_explicit_rollback(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with UnitOfWork(session_factory) as uow:
        await AccountsRepo(uow.session).create(handle="rolled")
        await uow.rollback()
    async with UnitOfWork(session_factory) as uow:
        assert await AccountsRepo(uow.session).get_by_handle("rolled") is None
