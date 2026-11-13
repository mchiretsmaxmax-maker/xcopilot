"""E2E: владелец присылает боту посты и файлы → ингест → итог с вопросом «Это что?»."""

from __future__ import annotations

import pytest
from aiogram import Dispatcher
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeClock, FakeTelegram
from tests.fakes.telegram import document_update
from tests.ingest_stand import Stand, make_stand
from tests.integration.bot.conftest import OWNER, Client, count
from tests.samples import unique_text
from xcopilot.bot.app import build_dispatcher
from xcopilot.bot.deps import BotDeps
from xcopilot.bot.notify import Notifier

SF = async_sessionmaker[AsyncSession]


@pytest.fixture
async def learn(
    session_factory: SF, clock: FakeClock, telegram: FakeTelegram, deps: BotDeps
) -> tuple[Stand, Client]:
    stand = await make_stand(
        session_factory, clock, notifier=Notifier(telegram.bot, deps.uow_factory), handle="acc"
    )
    deps.ingest = stand.service
    dp: Dispatcher = build_dispatcher(deps)
    return stand, Client(dp, telegram, OWNER)


async def test_user_drops_three_posts_and_gets_summary(
    learn: tuple[Stand, Client], session_factory: SF
) -> None:
    stand, owner = learn
    posts = "\n---\n".join(unique_text(i) for i in range(3))
    replies = await owner.say(posts)
    assert replies[-1].text == "Принял 3 поста. Изучаю…"
    await stand.run_jobs()
    summary = owner.last
    assert summary.text.startswith("Готово: +3 примера твоего стиля (всего 3).")
    assert "Темы: l2." in summary.text
    assert "Дублей: 0." in summary.text
    assert "Это что?" in summary.text
    assert "✓ Мой пост" in summary.buttons
    await owner.press("Конкурент")
    assert "Запомнил" in owner.last.text
    assert await count(session_factory, "sources", "kind = 'competitor_post'") == 3
    assert await count(session_factory, "events", "props->>'action' = 'ingest.accept'") == 1


async def test_bot_rejects_big_file_and_takes_small_one(
    learn: tuple[Stand, Client], telegram: FakeTelegram, session_factory: SF
) -> None:
    stand, owner = learn
    replies = await owner.feed(document_update(9001, OWNER, "big.pdf", 25 * 1024 * 1024))
    assert "20 МБ" in replies[-1].text
    telegram.files["f9002"] = "Заметка про L2 и блобы.".encode()
    replies = await owner.feed(document_update(9002, OWNER, "note.txt", 40))
    assert replies[-1].text == "Принял файл «note.txt». Изучаю…"
    await stand.run_jobs()
    assert await count(session_factory, "sources", "kind = 'file' AND status = 'indexed'") == 1


async def test_bot_foreign_handle_offers_options(learn: tuple[Stand, Client]) -> None:
    _, owner = learn
    await owner.say("@vitalik")
    assert "Включить платное чтение" in owner.last.buttons
    await owner.press("Пришлю текстом")
    assert "Пришли" in owner.last.text
