"""fix/bot-control: перезапуск бота из меню и /restart (только администратор), метка и сообщение."""

from __future__ import annotations

from aiogram import Dispatcher
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeTelegram
from tests.integration.bot.conftest import EDITOR, OWNER, Client, make_onboarded
from xcopilot.bot import keyboards, texts
from xcopilot.bot.deps import BotDeps
from xcopilot.ops.restart import announce_restart, mark_restart

SF = async_sessionmaker[AsyncSession]


async def _marks(sf: SF) -> int:
    async with sf() as s:
        return int(
            (
                await s.execute(
                    text("SELECT count(*) FROM ops_marks WHERE key = 'restart.requested'")
                )
            ).scalar_one()
        )


def _spy(deps: BotDeps) -> list[int]:
    calls: list[int] = []
    deps.request_restart = lambda: calls.append(1)
    return calls


def test_more_menu_has_restart_button() -> None:
    callbacks = [b.callback_data for row in keyboards.more_menu().inline_keyboard for b in row]
    assert "more:restart" in callbacks


async def test_restart_needs_confirmation(
    owner: Client, deps: BotDeps, session_factory: SF
) -> None:
    await make_onboarded(session_factory)
    calls = _spy(deps)
    [ask] = await owner.say("/restart")
    assert "restart:yes" in ask.callbacks and "restart:no" in ask.callbacks
    assert calls == []
    await owner.press_data("restart:no")
    assert calls == [] and await _marks(session_factory) == 0


async def test_restart_confirmed_requests_stop_and_leaves_mark(
    owner: Client, deps: BotDeps, session_factory: SF
) -> None:
    await make_onboarded(session_factory)
    calls = _spy(deps)
    await owner.say("/restart")
    await owner.press_data("restart:yes")
    assert calls == [1]
    assert await _marks(session_factory) == 1


async def test_restart_from_more_menu(owner: Client, deps: BotDeps, session_factory: SF) -> None:
    await make_onboarded(session_factory)
    calls = _spy(deps)
    await owner.press_data("more:restart")
    assert "restart:yes" in owner.last.callbacks
    await owner.press_data("restart:yes")
    assert calls == [1]


async def test_restart_is_admin_only(
    owner: Client, dp: Dispatcher, telegram: FakeTelegram, deps: BotDeps, session_factory: SF
) -> None:
    await make_onboarded(session_factory)
    await make_onboarded(session_factory, tg_id=EDITOR, role="editor")
    calls = _spy(deps)
    editor = Client(dp, telegram, EDITOR)
    [reply] = await editor.say("/restart")
    assert reply.text == texts.ADMIN_ONLY
    await editor.press_data("more:restart")
    await editor.press_data("restart:yes")  # даже с подделанной кнопкой
    assert calls == [] and await _marks(session_factory) == 0


async def test_restart_without_hook_says_unavailable(
    owner: Client, deps: BotDeps, session_factory: SF
) -> None:
    await make_onboarded(session_factory)
    deps.request_restart = None
    await owner.say("/restart")
    await owner.press_data("restart:yes")
    assert texts.RESTART_UNAVAILABLE in owner.tg.texts_to(OWNER)
    assert await _marks(session_factory) == 0


async def test_announce_after_start_once(
    deps: BotDeps, telegram: FakeTelegram, session_factory: SF
) -> None:
    await mark_restart(deps.uow_factory, deps.clock, OWNER)
    assert await announce_restart(telegram.bot, deps.uow_factory) == 1
    assert telegram.last_to(OWNER).text == texts.RESTARTED == "✅ Бот перезапущен"
    assert await announce_restart(telegram.bot, deps.uow_factory) == 0  # метка снята
    assert await _marks(session_factory) == 0
    assert len(telegram.texts_to(OWNER)) == 1
