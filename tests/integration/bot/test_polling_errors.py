"""Опрос Telegram, упавший с ошибкой, не заканчивается молча (fix/worker-app-wiring)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from aiogram import Dispatcher

from xcopilot.bot.app import BotPollingError, run_polling
from xcopilot.bot.deps import BotDeps


async def test_run_polling_raises_when_polling_fails(
    deps: BotDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(self: Dispatcher, *bots: Any, **kwargs: Any) -> None:
        raise RuntimeError("Telegram server says - Unauthorized")

    monkeypatch.setattr(Dispatcher, "start_polling", broken)
    with pytest.raises(BotPollingError, match="Unauthorized"):
        await asyncio.wait_for(run_polling(deps, asyncio.Event()), timeout=5)
