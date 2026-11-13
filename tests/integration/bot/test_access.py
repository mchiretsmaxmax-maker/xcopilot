"""Доступ: белый список, бутстрап владельцев из OWNER_TG_IDS, контекст аккаунта."""

from aiogram import Dispatcher
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeTelegram
from tests.integration.bot.conftest import EDITOR, OWNER, Client, count, make_onboarded
from xcopilot.db.repos import UsersRepo
from xcopilot.db.uow import UnitOfWork

SessionFactory = async_sessionmaker[AsyncSession]


async def test_unknown_user_denied_and_nothing_written(
    stranger: Client, session_factory: SessionFactory
) -> None:
    sent = await stranger.say("/start")
    assert [m.text for m in sent] == ["Доступ закрыт. Попроси владельца добавить тебя."]
    for table in ("users", "account_members", "bot_sessions", "events", "sources", "jobs"):
        assert await count(session_factory, table) == 0, table
    # кроме системного global, аккаунтов нет
    assert await count(session_factory, "accounts", "kind = 'x'") == 0


async def test_unknown_user_denied_on_button_press(stranger: Client) -> None:
    sent = await stranger.press_data("menu:write")
    assert sent == [] or sent[-1].text == "Доступ закрыт. Попроси владельца добавить тебя."
    assert await stranger.state() is None


async def test_owner_from_env_bootstrapped_on_first_start(
    owner: Client, session_factory: SessionFactory
) -> None:
    sent = await owner.say("/start")
    assert sent
    assert "Доступ закрыт" not in sent[0].text
    async with UnitOfWork(session_factory) as uow:
        user = await UsersRepo(uow.session).get_by_tg_id(OWNER)
    assert user is not None
    assert user.is_admin is True


async def test_user_added_to_users_is_allowed(
    dp: Dispatcher,
    telegram: FakeTelegram,
    session_factory: SessionFactory,
) -> None:
    async with UnitOfWork(session_factory) as uow:
        await UsersRepo(uow.session).get_or_create(EDITOR)
    editor = Client(dp, telegram, EDITOR)
    sent = await editor.say("/help")
    assert sent
    assert "Доступ закрыт" not in sent[0].text


async def test_start_begins_onboarding_when_no_account(owner: Client) -> None:
    sent = await owner.say("/start")
    assert sent[-1].text == "В какой теме работаем?"
    assert sent[-1].buttons == ["Крипта", "ИИ", "Финансы", "Своё"]
    assert await owner.state() == "Onboarding:niche"


async def test_start_shows_menu_when_onboarded(
    owner: Client, session_factory: SessionFactory
) -> None:
    await make_onboarded(session_factory)
    sent = await owner.say("/start")
    assert sent[-1].buttons == [
        "✍️ Написать",
        "💡 Темы",
        "📥 Обучить",
        "🎯 Хвост тренда",
        "📊 Аналитика",
        "🎨 Стиль",
        "⚙️ Настройки",
    ]
    assert await owner.state() is None
