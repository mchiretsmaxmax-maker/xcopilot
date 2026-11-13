"""Каждое действие пользователя пишет событие user.action."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.integration.bot.conftest import Client, make_onboarded

SessionFactory = async_sessionmaker[AsyncSession]


async def _actions(sf: SessionFactory) -> list[str]:
    async with sf() as s:
        rows = await s.execute(
            text("SELECT props->>'action' FROM events WHERE type = 'user.action' ORDER BY ts, id")
        )
        return [r[0] for r in rows]


async def test_every_user_action_emits_event(
    owner: Client, session_factory: SessionFactory
) -> None:
    await owner.say("/start")
    await owner.press("Крипта")
    await owner.press("Русский")
    await owner.say("Один пример")
    await owner.press("Дальше")
    await owner.press("Да")
    await owner.press("Пропустить")
    await owner.press("Всё верно")
    await owner.say("/help")
    await owner.say("/cost")
    await owner.press("✍️ Написать")
    await owner.say("/settings")
    await owner.press("Часовой пояс")
    await owner.say("Europe/Berlin")
    await owner.say("/cancel")
    assert await _actions(session_factory) == [
        "start",
        "onboarding.niche",
        "onboarding.lang",
        "onboarding.sample",
        "onboarding.style_next",
        "onboarding.style_continue",
        "onboarding.competitors_skip",
        "onboarding.confirm",
        "help",
        "cost",
        "menu.write",
        "settings",
        "settings.tz_open",
        "settings.tz",
        "cancel",
    ]


async def test_events_are_written_to_account_after_onboarding(
    owner: Client, session_factory: SessionFactory
) -> None:
    acc = await make_onboarded(session_factory)
    await owner.say("/help")
    async with session_factory() as s:
        n = (
            await s.execute(
                text("SELECT count(*) FROM events WHERE type='user.action' AND account_id = :a"),
                {"a": acc},
            )
        ).scalar_one()
    assert n == 1


async def test_denied_user_emits_nothing(stranger: Client, session_factory: SessionFactory) -> None:
    await stranger.say("/start")
    assert await _actions(session_factory) == []
