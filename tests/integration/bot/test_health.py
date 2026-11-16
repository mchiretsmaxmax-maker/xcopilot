"""Этап 15: /health — состояние сервиса, только для администратора."""

from __future__ import annotations

from datetime import timedelta

from aiogram import Dispatcher
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeTelegram
from tests.integration.bot.conftest import EDITOR, Client, make_onboarded
from xcopilot.bot import texts
from xcopilot.bot.deps import BotDeps

SF = async_sessionmaker[AsyncSession]


async def test_health_command_admin_only(
    owner: Client, dp: Dispatcher, telegram: FakeTelegram, deps: BotDeps, session_factory: SF
) -> None:
    await make_onboarded(session_factory)  # владелец из OWNER_TG_IDS — администратор
    await make_onboarded(session_factory, tg_id=EDITOR, role="editor")
    deps.open_since = lambda: {
        "deepseek": deps.clock.now() - timedelta(minutes=20),
        "claude_cli": None,
    }

    editor = Client(dp, telegram, EDITOR)
    [reply] = await editor.say("/health")
    assert reply.text == texts.ADMIN_ONLY

    [report] = await owner.say("/health")
    body = report.text
    assert body.startswith("🩺 Состояние сервиса")
    for part in ("БД: ✅", "deepseek", "claude_cli", "Очередь", "Бэкап", "Очередь разбора",
                 "Снимок базы знаний"):  # fmt: skip
        assert part in body, part
    assert "🔴 deepseek" in body and "🟢 claude_cli" in body
