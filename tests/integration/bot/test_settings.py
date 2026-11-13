"""Настройки: часовой пояс, язык постов, дневные лимиты, режим X."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.integration.bot.conftest import Client, make_onboarded

SessionFactory = async_sessionmaker[AsyncSession]


async def _acc(sf: SessionFactory) -> dict[str, object]:
    async with sf() as s:
        row = await s.execute(
            text("SELECT tz, lang, x_read_mode FROM accounts WHERE handle = 'acc'")
        )
        return dict(row.one()._mapping)


async def _limit(sf: SessionFactory, scope: str) -> int | None:
    async with sf() as s:
        row = await s.execute(
            text("SELECT limit_micro_usd FROM budgets WHERE scope = :s AND period = 'day'"),
            {"s": scope},
        )
        return row.scalar_one_or_none()


async def test_settings_change_timezone_language_limits(
    owner: Client, session_factory: SessionFactory
) -> None:
    await make_onboarded(session_factory)
    sent = await owner.say("/settings")
    assert sent[-1].buttons == ["Часовой пояс", "Язык постов", "Дневные лимиты", "Режим X"]

    await owner.press("Часовой пояс")
    sent = await owner.say("Mars/Base")
    assert "Не знаю такой пояс" in sent[-1].text
    assert (await _acc(session_factory))["tz"] != "Mars/Base"
    await owner.say("Europe/Berlin")
    assert (await _acc(session_factory))["tz"] == "Europe/Berlin"
    assert await owner.state() is None

    await owner.say("/settings")
    await owner.press("Язык постов")
    await owner.press("English")
    assert (await _acc(session_factory))["lang"] == "en"

    await owner.say("/settings")
    await owner.press("Дневные лимиты")
    await owner.press("Лимит моделей")
    await owner.say("7.5")
    assert await _limit(session_factory, "llm") == 7_500_000
    await owner.say("/settings")
    await owner.press("Дневные лимиты")
    await owner.press("Лимит X")
    sent = await owner.say("не число")
    assert "Нужно число" in sent[-1].text
    await owner.say("2")
    assert await _limit(session_factory, "x_api") == 2_000_000

    await owner.say("/settings")
    await owner.press("Режим X")
    await owner.press("Выключен")
    assert (await _acc(session_factory))["x_read_mode"] == "off"


async def test_settings_without_account_asks_to_start(owner: Client) -> None:
    sent = await owner.say("/settings")
    assert "/start" in sent[-1].text
