"""Общие фикстуры: настоящий PostgreSQL в контейнере (pgvector/pgvector:pg18)."""

from collections.abc import Iterator

import pytest
from testcontainers.postgres import PostgresContainer

from tests.fakes import FakeClock, FakeTelegram

PG_IMAGE = "pgvector/pgvector:pg18"


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    """URL (asyncpg) контейнера PostgreSQL; один на всю сессию тестов."""
    with PostgresContainer(PG_IMAGE, driver="asyncpg") as pg:
        yield pg.get_connection_url()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def telegram() -> FakeTelegram:
    return FakeTelegram()
