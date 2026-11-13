"""Фикстуры ингеста: стенд на настоящей БД с подделками модели, X и эмбеддера."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeClock
from tests.ingest_stand import Stand, make_stand


@pytest.fixture
async def stand(session_factory: async_sessionmaker[AsyncSession], clock: FakeClock) -> Stand:
    return await make_stand(session_factory, clock)
