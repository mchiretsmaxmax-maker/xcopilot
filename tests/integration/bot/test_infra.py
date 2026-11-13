"""Инфраструктура бота: FSM в PostgreSQL, дедуп, ошибки, флуд, остановка."""

import asyncio
from collections.abc import Callable

import structlog
from aiogram import Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Message

from tests.fakes import FakeClock, FakeTelegram
from tests.fakes.telegram import message_update
from tests.integration.bot.conftest import OWNER, Client
from xcopilot.bot.app import run_polling
from xcopilot.bot.deps import BotDeps
from xcopilot.errors import NotFound


async def test_fsm_state_survives_restart(
    owner: Client, make_dp: Callable[[], Dispatcher], telegram: FakeTelegram
) -> None:
    await owner.say("/start")
    await owner.press("Крипта")
    await owner.press("Русский")
    await owner.say("Первый пример")
    await owner.say("Второй пример")

    restarted = Client(make_dp(), telegram, OWNER)  # новый процесс, то же хранилище
    assert await restarted.state() == "Onboarding:style"
    sent = await restarted.say("Третий пример")
    assert "Принято 3 из 5" in sent[-1].text


async def test_duplicate_update_processed_once(owner: Client) -> None:
    update = message_update(777, OWNER, "/start")
    first = await owner.feed(update)
    second = await owner.feed(update)
    assert len(first) == 1
    assert second == []


async def test_error_middleware_replies_with_code_and_logs(dp: Dispatcher, owner: Client) -> None:
    router = Router()

    @router.message(Command("boom"))
    async def boom(message: Message) -> None:
        raise RuntimeError("секрет sk-123 в тексте ошибки")

    @router.message(Command("missing"))
    async def missing(message: Message) -> None:
        raise NotFound("аккаунт не найден")

    dp.include_router(router)
    with structlog.testing.capture_logs() as logs:
        sent = await owner.say("/boom")
        sent_nf = await owner.say("/missing")
    assert "E500" in sent[-1].text
    assert "sk-123" not in sent[-1].text
    assert "E404" in sent_nf[-1].text
    errors = [r for r in logs if r["event"] == "bot_error"]
    assert [r["code"] for r in errors] == ["E500", "E404"]
    assert errors[0]["log_level"] == "error"


async def test_flood_limit_blocks_burst(deps: BotDeps, owner: Client, clock: FakeClock) -> None:
    deps.flood_limit = 5
    deps.flood_window_seconds = 10
    replies = []
    for _ in range(10):
        replies += await owner.say("/help")
    helps = [m for m in replies if "/settings" in m.text]
    floods = [m for m in replies if m.text == "Слишком часто. Подожди немного."]
    assert len(helps) == 5
    assert len(floods) == 1  # предупреждаем один раз, дальше молчим
    clock.advance(11)
    sent = await owner.say("/help")
    assert "/settings" in sent[-1].text


async def test_run_polling_stops_gracefully(deps: BotDeps, telegram: FakeTelegram) -> None:
    stop = asyncio.Event()
    task = asyncio.create_task(run_polling(deps, stop))
    await asyncio.sleep(0.1)
    assert not task.done()
    stop.set()
    await asyncio.wait_for(task, timeout=5)
