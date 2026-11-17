"""fix/full-menu: всё меню всегда под рукой — нижняя клавиатура + «⚙️ Ещё», без меню по /start."""

from __future__ import annotations

from aiogram import Dispatcher, Router
from aiogram.filters import Command
from aiogram.methods import SetMyCommands
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeTelegram
from tests.integration.bot.conftest import Client, make_onboarded
from xcopilot.bot import keyboards, texts
from xcopilot.bot.commands import set_commands

SF = async_sessionmaker[AsyncSession]

# Разделы нижней клавиатуры → их callback из старого встроенного меню.
IN_REPLY_KEYBOARD = {
    keyboards.BTN_POST: "menu:write",
    keyboards.BTN_REPLIES: "menu:replies",
    keyboards.BTN_TOPICS: "menu:topics",
    keyboards.BTN_STATS: "menu:analytics",
}
MORE_SECTIONS = {
    "menu:style",
    "menu:learn",
    "menu:trend",
    "more:settings",
    "more:status",
    "more:cost",
    "more:help",
    "more:restart",
}
# Старые menu:* в «Ещё» представлены своими more:* (то же действие).
SAME_AS = {"menu:settings": "more:settings"}


def _callbacks(markup: InlineKeyboardMarkup) -> set[str]:
    return {b.callback_data or "" for row in markup.inline_keyboard for b in row}


def test_more_menu_has_every_section_not_in_reply_keyboard() -> None:
    assert set(IN_REPLY_KEYBOARD) <= set(keyboards.REPLY_LABELS)
    assert _callbacks(keyboards.more_menu()) == MORE_SECTIONS
    legacy = {
        "menu:write",
        "menu:replies",
        "menu:topics",
        "menu:learn",
        "menu:trend",
        "menu:analytics",
        "menu:style",
        "menu:settings",
    }
    reachable = MORE_SECTIONS | set(IN_REPLY_KEYBOARD.values())
    assert {SAME_AS.get(c, c) for c in legacy} <= reachable


async def test_start_for_onboarded_sends_reply_keyboard_only(
    owner: Client, session_factory: SF
) -> None:
    await make_onboarded(session_factory)
    sent = await owner.say("/start")
    assert sent and isinstance(sent[-1].reply_markup, ReplyKeyboardMarkup)
    assert sent[-1].text == texts.REPLY_MENU_HINT
    assert not any(isinstance(m.reply_markup, InlineKeyboardMarkup) for m in sent)


async def test_old_menu_callbacks_still_work(owner: Client, session_factory: SF) -> None:
    await make_onboarded(session_factory)
    await owner.press_data("menu:settings")
    assert owner.last.callbacks  # меню настроек
    await owner.press_data("menu:learn")
    assert owner.last.text == texts.LEARN_HINT


def _command_names(router: Router) -> set[str]:
    names: set[str] = set()
    for handler in router.message.handlers:
        for flt in handler.filters or ():
            if isinstance(flt.callback, Command):
                names |= {c for c in flt.callback.commands if isinstance(c, str)}
    for sub in router.sub_routers:
        names |= _command_names(sub)
    return names


async def test_commands_cover_every_command_handler(dp: Dispatcher, telegram: FakeTelegram) -> None:
    await set_commands(telegram.bot)
    call = next(c for c in telegram.calls if isinstance(c, SetMyCommands))
    registered = {c.command for c in call.commands}
    handled = _command_names(dp) - {"start"}
    assert handled and handled <= registered
