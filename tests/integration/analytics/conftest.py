"""Фикстуры этапа 14."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.analytics_stand import AStand, make_analytics_stand
from tests.fakes import FakeClock

NOW = datetime(2026, 11, 14, 12, tzinfo=UTC)


@pytest.fixture
async def st(session_factory: async_sessionmaker[AsyncSession]) -> AStand:
    return await make_analytics_stand(session_factory, FakeClock(NOW))
