"""Сборка диспетчера aiogram и запуск long polling."""

from __future__ import annotations

import asyncio
import contextlib

import structlog
from aiogram import Dispatcher

from xcopilot.bot import budget_events
from xcopilot.bot.deps import BotDeps
from xcopilot.bot.middlewares.account import AccountMiddleware
from xcopilot.bot.middlewares.auth import AuthMiddleware
from xcopilot.bot.middlewares.dedup import DedupMiddleware
from xcopilot.bot.middlewares.errors import ErrorMiddleware
from xcopilot.bot.middlewares.flood import FloodMiddleware
from xcopilot.bot.routers import (
    cost,
    help,
    learn,
    menu,
    onboarding,
    settings,
    start,
    style,
    x_connect,
)
from xcopilot.bot.storage import PgStorage

log = structlog.get_logger()


def build_dispatcher(deps: BotDeps) -> Dispatcher:
    """Диспетчер: дедуп → ошибки → флуд → доступ → аккаунт → роутеры. FSM — в PostgreSQL."""
    dp = Dispatcher(storage=PgStorage(deps.uow_factory), deps=deps)
    dp.update.outer_middleware(DedupMiddleware())
    dp.update.outer_middleware(ErrorMiddleware(deps))
    dp.update.outer_middleware(FloodMiddleware(deps))
    for observer in (dp.message, dp.callback_query):
        observer.outer_middleware(AuthMiddleware(deps))
        observer.outer_middleware(AccountMiddleware(deps))
    # команды раньше состояний: /start, /cancel, /help работают на любом шаге
    dp.include_routers(
        start.build_router(),
        help.build_router(),
        cost.build_router(),
        settings.build_router(),
        x_connect.build_router(),
        style.build_router(),  # раньше menu: перехватывает menu:style
        menu.build_router(),
        budget_events.build_router(),
        onboarding.build_router(),
        learn.build_router(),
    )
    return dp


async def run_polling(deps: BotDeps, stop: asyncio.Event) -> None:
    """Опрашивать Telegram, пока не сработает stop; затем остановиться корректно."""
    budget_events.install_budget_notifications()
    dp = build_dispatcher(deps)
    polling = asyncio.create_task(dp.start_polling(deps.bot, handle_signals=False))
    waiter = asyncio.create_task(stop.wait())
    try:
        await asyncio.wait({polling, waiter}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        if not polling.done():
            with contextlib.suppress(RuntimeError):
                await dp.stop_polling()
            await polling
        waiter.cancel()
        await deps.bot.session.close()
    log.info("bot_polling_stopped")
