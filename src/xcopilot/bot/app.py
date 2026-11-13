"""Сборка диспетчера aiogram и запуск long polling."""

from __future__ import annotations

import asyncio

from aiogram import Dispatcher

from xcopilot.bot.deps import BotDeps


def build_dispatcher(deps: BotDeps) -> Dispatcher:
    raise NotImplementedError


async def run_polling(deps: BotDeps, stop: asyncio.Event) -> None:
    """Опрашивать Telegram, пока не сработает stop; затем остановиться корректно."""
    raise NotImplementedError
