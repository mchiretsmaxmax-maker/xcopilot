"""Фикстуры этапа 6: аккаунт, UoW-фабрика, ключ шифрования, часы."""

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeClock
from xcopilot.db.repos import AccountsRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.security.crypto import SecretBox

T0 = datetime(2026, 11, 13, 12, 0, 0, tzinfo=UTC)
OWN_USER_ID = "1500000000000000000"


@pytest.fixture
def uow_factory(
    session_factory: async_sessionmaker[AsyncSession],
) -> Callable[[], UnitOfWork]:
    return lambda: UnitOfWork(session_factory)


@pytest.fixture
async def account_id(uow_factory: Callable[[], UnitOfWork]) -> UUID:
    async with uow_factory() as uow:
        acc = await AccountsRepo(uow.session).create(handle="me", x_user_id=OWN_USER_ID)
        return acc.id


@pytest.fixture
def box() -> SecretBox:
    return SecretBox({1: Fernet.generate_key().decode()})


@pytest.fixture
def xclock() -> FakeClock:
    return FakeClock(T0)
