"""Этап 15: /health — состояние сервиса, только для администратора."""

from __future__ import annotations

from datetime import datetime, timedelta

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
        "codex_cli": None,
    }

    editor = Client(dp, telegram, EDITOR)
    [reply] = await editor.say("/health")
    assert reply.text == texts.ADMIN_ONLY

    [report] = await owner.say("/health")
    body = report.text
    assert body.startswith("🩺 Состояние сервиса")
    for part in ("БД: ✅", "deepseek", "codex_cli", "Очередь", "Бэкап", "Очередь разбора",
                 "Снимок базы знаний"):  # fmt: skip
        assert part in body, part
    assert "🔴 deepseek" in body and "🟢 codex_cli" in body


async def test_health_shows_heartbeat_state(
    owner: Client, dp: Dispatcher, telegram: FakeTelegram, deps: BotDeps, session_factory: SF
) -> None:
    """Аудит I: /health показывает состояние внешнего сторожа."""
    from xcopilot.ops.heartbeat import Heartbeat

    await make_onboarded(session_factory)
    [off] = await owner.say("/health")
    assert "Сторож: выключен" in off.text

    class Loop:
        last_tick_at: datetime | None = deps.clock.now()
        busy_since: datetime | None = None

    hb = Heartbeat(
        None,  # type: ignore[arg-type]
        deps.clock,
        "https://hc.example/x",
        scheduler=Loop(),
        workers=[Loop()],
    )
    hb.last_ok_at = deps.clock.now() - timedelta(minutes=3)
    deps.heartbeat = hb
    [on] = await owner.say("/health")
    assert "Сторож: ✅ включён, последний пульс 3 мин назад" in on.text
