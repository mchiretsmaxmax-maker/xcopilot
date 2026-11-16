"""fix/bot-ux-progress: постоянное меню (ReplyKeyboard) и команды бота."""

from __future__ import annotations

from aiogram.methods import SetMyCommands
from aiogram.types import ReplyKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeTelegram
from tests.integration.bot.conftest import Client, count, make_onboarded
from xcopilot.bot.commands import set_commands

SF = async_sessionmaker[AsyncSession]
LABELS = ["✍️ Пост", "💡 Темы", "🧠 Мозг", "📊 Аналитика", "🗂 Очередь", "⚙️ Ещё"]


def _labels(markup: object) -> list[str]:
    assert isinstance(markup, ReplyKeyboardMarkup)
    return [b.text for row in markup.keyboard for b in row]


async def test_menu_command_shows_persistent_keyboard(owner: Client, session_factory: SF) -> None:
    await make_onboarded(session_factory)
    sent = await owner.say("/menu")
    assert _labels(sent[-1].reply_markup) == LABELS
    markup = sent[-1].reply_markup
    assert isinstance(markup, ReplyKeyboardMarkup) and markup.is_persistent


async def test_start_for_onboarded_user_also_shows_it(owner: Client, session_factory: SF) -> None:
    await make_onboarded(session_factory)
    sent = await owner.say("/start")
    assert any(
        isinstance(m.reply_markup, ReplyKeyboardMarkup) and _labels(m.reply_markup) == LABELS
        for m in sent
    )


async def test_buttons_lead_to_existing_handlers(owner: Client, session_factory: SF) -> None:
    await make_onboarded(session_factory)
    sent = await owner.say("✍️ Пост")
    assert sent and "пишем" in sent[-1].text.lower()
    sent = await owner.say("🗂 Очередь")
    assert sent and sent[-1].text
    sent = await owner.say("⚙️ Ещё")
    assert sent and sent[-1].buttons  # помощь, настройки, расход, статус
    assert await count(session_factory, "jobs") == 0
    await owner.say("💡 Темы")
    assert await count(session_factory, "jobs", "kind = 'writer.topics'") == 1


async def test_set_commands_registers_status_and_menu(telegram: FakeTelegram) -> None:
    await set_commands(telegram.bot)
    call = next(c for c in telegram.calls if isinstance(c, SetMyCommands))
    names = {c.command for c in call.commands}
    assert {"menu", "status", "help", "cost", "brain"} <= names
