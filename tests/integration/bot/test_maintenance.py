"""AUD-014: недоступная БД — владелец видит «техработы», а не «Что-то пошло не так»."""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator

import asyncpg
import pytest
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeout

from tests.fakes import FakeTelegram
from tests.fakes.telegram import message_update
from tests.integration.bot.conftest import OWNER
from xcopilot.bot import texts
from xcopilot.bot.app import build_dispatcher
from xcopilot.bot.deps import BotDeps
from xcopilot.bot.middlewares.errors import error_code
from xcopilot.db.engine import create_engine, is_db_unavailable, session_factory
from xcopilot.db.uow import UnitOfWork
from xcopilot.errors import NotFound

DEAD_URL = "postgresql+asyncpg://x:x@127.0.0.1:1/x"  # порт 1: соединение отклоняется


def test_is_db_unavailable_recognises_connection_failures() -> None:
    assert is_db_unavailable(OperationalError("SELECT 1", {}, Exception("down")))
    assert is_db_unavailable(InterfaceError("SELECT 1", {}, Exception("closed")))
    assert is_db_unavailable(ConnectionRefusedError())
    assert is_db_unavailable(asyncpg.CannotConnectNowError())
    assert is_db_unavailable(PoolTimeout("pool exhausted"))


def test_is_db_unavailable_false_for_other_errors() -> None:
    assert not is_db_unavailable(NotFound("нет"))
    assert not is_db_unavailable(ValueError("x"))


def test_error_code_503_for_unavailable_db() -> None:
    assert error_code(ConnectionRefusedError()) == "E503"


def test_maintenance_text() -> None:
    assert texts.MAINTENANCE == "Идут техработы, попробуй через пару минут."


@pytest.fixture
async def dead_deps(deps: BotDeps) -> AsyncIterator[BotDeps]:
    engine = create_engine(DEAD_URL)
    sf = session_factory(engine)
    yield dataclasses.replace(deps, uow_factory=lambda: UnitOfWork(sf))
    await engine.dispose()


async def test_user_gets_maintenance_text_when_db_is_down(
    dead_deps: BotDeps, telegram: FakeTelegram
) -> None:
    dp = build_dispatcher(dead_deps)
    await dp.feed_update(telegram.bot, message_update(1, OWNER, "/start"))
    assert [m.text for m in telegram.sent] == [texts.MAINTENANCE]
