"""Уведомления: роли получателей и сообщения о бюджете."""

from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeTelegram
from tests.integration.bot.conftest import EDITOR, OWNER, Client, make_onboarded
from xcopilot.bot.budget_events import (
    BOT_NOTIFY_BUDGET_KIND,
    handle_budget_notification,
    install_budget_notifications,
)
from xcopilot.bot.deps import BotDeps
from xcopilot.bot.notify import Notifier
from xcopilot.db.uow import UnitOfWork
from xcopilot.metrics.events import EventType, clear_listeners, emit

SessionFactory = async_sessionmaker[AsyncSession]


@pytest.fixture
def listeners() -> Iterator[None]:
    install_budget_notifications()
    yield
    clear_listeners()


async def test_notify_goes_to_roles_only(
    deps: BotDeps, telegram: FakeTelegram, session_factory: SessionFactory
) -> None:
    acc = await make_onboarded(session_factory)
    await make_onboarded(session_factory, tg_id=EDITOR, role="editor")
    notifier = Notifier(telegram.bot, deps.uow_factory)
    assert await notifier.notify(acc, "для владельца") == 1
    assert telegram.texts_to(OWNER) == ["для владельца"]
    assert telegram.texts_to(EDITOR) == []
    assert await notifier.notify(acc, "всем", roles=("owner", "editor")) == 2
    assert telegram.texts_to(EDITOR) == ["всем"]


async def _emit_and_handle(
    deps: BotDeps,
    session_factory: SessionFactory,
    acc: UUID,
    kind: EventType,
    props: dict[str, Any],
) -> None:
    async with UnitOfWork(session_factory) as uow:
        await emit(uow.session, account_id=acc, type=kind, props=props)
    (job,) = await deps.queue.claim("w1", 5)
    assert job.kind == BOT_NOTIFY_BUDGET_KIND
    await handle_budget_notification(job, deps=deps)
    await deps.queue.complete(job.id)


@pytest.mark.usefixtures("listeners")
async def test_budget_exceeded_notification_has_buttons(
    deps: BotDeps, telegram: FakeTelegram, session_factory: SessionFactory, owner: Client
) -> None:
    acc = await make_onboarded(session_factory)
    await make_onboarded(session_factory, tg_id=EDITOR, role="editor")
    props = {
        "scope": "llm",
        "period": "day",
        "spent_micro_usd": 5_000_000,
        "limit_micro_usd": 5_000_000,
    }
    await _emit_and_handle(deps, session_factory, acc, EventType.BUDGET_EXCEEDED, props)
    msg = telegram.last_to(OWNER)
    assert msg.text == "Дневной лимит на модели исчерпан ($5.00 из $5.00)."
    assert msg.buttons == ["Поднять на сегодня", "Ждать до завтра"]
    assert telegram.texts_to(EDITOR) == []

    await owner.press("Ждать до завтра")
    async with session_factory() as s:
        n = (await s.execute(text("SELECT count(*) FROM budgets"))).scalar_one()
    assert n == 0

    await owner.press("Поднять на сегодня")
    async with session_factory() as s:
        limit = (
            await s.execute(
                text("SELECT limit_micro_usd FROM budgets WHERE scope='llm' AND period='day'")
            )
        ).scalar_one()
    assert limit == 10_000_000


@pytest.mark.usefixtures("listeners")
async def test_budget_warning_notification_without_buttons(
    deps: BotDeps, telegram: FakeTelegram, session_factory: SessionFactory
) -> None:
    acc = await make_onboarded(session_factory)
    props = {
        "scope": "x_api",
        "period": "day",
        "spent_micro_usd": 800_000,
        "limit_micro_usd": 1_000_000,
    }
    await _emit_and_handle(deps, session_factory, acc, EventType.BUDGET_WARNING, props)
    msg = telegram.last_to(OWNER)
    assert msg.text == "Расход на X подходит к дневному лимиту ($0.80 из $1.00)."
    assert msg.buttons == []
