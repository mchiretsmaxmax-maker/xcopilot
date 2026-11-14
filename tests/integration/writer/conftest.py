"""Фикстуры писателя: стенд на настоящей БД."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.writer_stand import WriterStand, make_writer_stand


@pytest.fixture
async def ws(session_factory: async_sessionmaker[AsyncSession]) -> WriterStand:
    return await make_writer_stand(session_factory)
