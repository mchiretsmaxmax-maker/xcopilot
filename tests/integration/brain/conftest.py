"""Фикстуры «Мозга»: стенд на настоящей БД с подделками модели и X."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.brain_stand import BrainStand, make_brain_stand
from tests.fakes import FakeClock


@pytest.fixture
async def brain(session_factory: async_sessionmaker[AsyncSession], clock: FakeClock) -> BrainStand:
    return await make_brain_stand(session_factory, clock)
