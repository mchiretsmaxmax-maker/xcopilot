"""Общие фикстуры: настоящий PostgreSQL в контейнере (pgvector/pgvector:pg18).

Схема накатывается один раз в шаблонную БД; каждый тест получает свежую копию
(CREATE DATABASE ... TEMPLATE), поэтому тесты не мешают друг другу.
"""

import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
from alembic import command
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from testcontainers.community.postgres import PostgresContainer

from tests.dbutil import create_database, drop_database
from tests.fakes import FakeClock, FakeTelegram
from xcopilot.db.engine import create_engine
from xcopilot.db.engine import session_factory as make_session_factory
from xcopilot.db.migrate import alembic_config

PG_IMAGE = "pgvector/pgvector:pg18"
TEMPLATE_DB = "xc_template"


@pytest.fixture(scope="session")
def pg_container() -> Iterator[PostgresContainer]:
    """Контейнер PostgreSQL; один на всю сессию тестов."""
    with PostgresContainer(PG_IMAGE, driver="asyncpg") as pg:
        yield pg


@pytest.fixture(scope="session")
def postgres_url(pg_container: PostgresContainer) -> str:
    """URL (asyncpg) служебной БД контейнера."""
    return pg_container.get_connection_url()


@pytest.fixture(scope="session")
def template_db_url(postgres_url: str) -> str:
    """Шаблонная БД со схемой head (без месячных партиций)."""
    url = create_database(postgres_url, TEMPLATE_DB)
    command.upgrade(alembic_config(url), "head")
    return url


@pytest.fixture
def db_url(postgres_url: str, template_db_url: str) -> Iterator[str]:
    """Свежая БД со схемой head; удаляется после теста."""
    name = f"xc_{uuid.uuid4().hex[:12]}"
    url = create_database(postgres_url, name, template=TEMPLATE_DB)
    yield url
    drop_database(postgres_url, name)


@pytest.fixture
def empty_db_url(postgres_url: str) -> Iterator[str]:
    """Пустая БД без схемы; удаляется после теста."""
    name = f"xe_{uuid.uuid4().hex[:12]}"
    url = create_database(postgres_url, name)
    yield url
    drop_database(postgres_url, name)


@pytest.fixture
async def engine(db_url: str) -> AsyncIterator[AsyncEngine]:
    eng = create_engine(db_url)
    yield eng
    await eng.dispose()


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return make_session_factory(engine)


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Сессия на тест; всё несохранённое откатывается."""
    async with session_factory() as s:
        yield s
        await s.rollback()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def telegram() -> FakeTelegram:
    return FakeTelegram()
