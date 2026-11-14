"""Фикстуры ретривала: база знаний на настоящей БД."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.retrieval_stand import Kb, ScriptedEmbedder


@pytest.fixture
def embedder() -> ScriptedEmbedder:
    return ScriptedEmbedder()


@pytest.fixture
def kb(session_factory: async_sessionmaker[AsyncSession], embedder: ScriptedEmbedder) -> Kb:
    return Kb(session_factory, embedder)
