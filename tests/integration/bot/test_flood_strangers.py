"""AUD-021: предупреждение о флуде — только владельцам; память не копит чужих."""

from __future__ import annotations

from typing import Any

from aiogram.types import User

from tests.fakes import FakeClock, FakeTelegram
from tests.integration.bot.conftest import OWNER, STRANGER
from xcopilot.bot import texts
from xcopilot.bot.deps import BotDeps
from xcopilot.bot.middlewares.flood import FloodMiddleware


async def hit(mw: FloodMiddleware, tg_id: int, calls: list[int]) -> Any:
    async def handler(_event: Any, _data: dict[str, Any]) -> str:
        calls.append(tg_id)
        return "ok"

    user = User(id=tg_id, is_bot=False, first_name="x")
    return await mw(handler, object(), {"event_from_user": user})  # type: ignore[arg-type]


async def test_stranger_flood_is_dropped_silently(deps: BotDeps, telegram: FakeTelegram) -> None:
    deps.flood_limit = 5
    mw = FloodMiddleware(deps)
    calls: list[int] = []
    for _ in range(100):
        await hit(mw, STRANGER, calls)
    assert len(calls) == 5  # лишнее отброшено
    assert telegram.sent == []  # чужому ничего не отправили


async def test_owner_flood_still_gets_warning_once(deps: BotDeps, telegram: FakeTelegram) -> None:
    deps.flood_limit = 5
    mw = FloodMiddleware(deps)
    calls: list[int] = []
    for _ in range(20):
        await hit(mw, OWNER, calls)
    assert [m.text for m in telegram.sent] == [texts.FLOOD]


async def test_stale_hits_are_dropped(deps: BotDeps, clock: FakeClock) -> None:
    mw = FloodMiddleware(deps)
    calls: list[int] = []
    for uid in range(1000, 1100):  # сто разных чужих id по разу
        await hit(mw, uid, calls)
    clock.advance(deps.flood_window_seconds + 1)
    await hit(mw, OWNER, calls)
    assert len(mw._hits) <= 2
