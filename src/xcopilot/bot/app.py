"""Сборка диспетчера aiogram и запуск long polling."""

from __future__ import annotations

import asyncio
import contextlib

import structlog
from aiogram import Dispatcher

from xcopilot.bot import budget_events
from xcopilot.bot.commands import set_commands
from xcopilot.bot.deps import BotDeps
from xcopilot.bot.middlewares.account import AccountMiddleware
from xcopilot.bot.middlewares.auth import AuthMiddleware
from xcopilot.bot.middlewares.dedup import DedupMiddleware
from xcopilot.bot.middlewares.errors import ErrorMiddleware
from xcopilot.bot.middlewares.flood import FloodMiddleware
from xcopilot.bot.middlewares.menu_reset import MenuResetMiddleware
from xcopilot.bot.routers import (
    brain,
    cards,
    cost,
    health,
    help,
    learn,
    material,
    menu,
    onboarding,
    queue,
    reply_menu,
    restart,
    settings,
    start,
    stats,
    status,
    style,
    topics,
    trend,
    why,
    write,
    x_connect,
)
from xcopilot.bot.storage import PgStorage

log = structlog.get_logger()


class BotPollingError(RuntimeError):
    """Опрос Telegram прекратился с ошибкой (например, Telegram отклонил BOT_TOKEN)."""


def build_dispatcher(deps: BotDeps) -> Dispatcher:
    """Диспетчер: дедуп → ошибки → флуд → доступ → аккаунт → роутеры. FSM — в PostgreSQL."""
    dp = Dispatcher(storage=PgStorage(deps.uow_factory), deps=deps)
    dp.update.outer_middleware(DedupMiddleware())
    dp.update.outer_middleware(ErrorMiddleware(deps))
    dp.update.outer_middleware(FloodMiddleware(deps))
    dp.message.outer_middleware(MenuResetMiddleware())
    for observer in (dp.message, dp.callback_query):
        observer.outer_middleware(AuthMiddleware(deps))
        observer.outer_middleware(AccountMiddleware(deps))
    # команды раньше состояний: /start, /cancel, /help работают на любом шаге
    dp.include_routers(
        start.build_router(),
        reply_menu.build_router(),
        status.build_router(),
        help.build_router(),
        cost.build_router(),
        health.build_router(),  # этап 15
        restart.build_router(),  # fix/bot-control: /restart, «🔄 Перезапустить бота»
        settings.build_router(),
        x_connect.build_router(),
        style.build_router(),  # раньше menu: перехватывает menu:style
        *(r.build_router() for r in (write, topics, trend)),  # этап 10, раньше menu
        *(r.build_router() for r in (cards, queue)),  # этап 12, раньше menu и learn
        stats.build_router(),  # этап 14, раньше menu: перехватывает menu:analytics
        menu.build_router(),
        budget_events.build_router(),
        onboarding.build_router(),
        brain.build_router(),  # 9C; до learn: кнопка «🧠 Мозг» не должна стать материалом
        learn.build_router(),
        material.build_router(),
        why.build_router(),
    )
    return dp


async def run_polling(deps: BotDeps, stop: asyncio.Event) -> None:
    """Опрашивать Telegram, пока не сработает stop; затем остановиться корректно."""
    budget_events.install_budget_notifications()
    try:
        await set_commands(deps.bot)
    except Exception:
        log.warning("set_commands_failed", exc_info=True)
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
    if polling.done() and not polling.cancelled() and polling.exception() is not None:
        raise BotPollingError(str(polling.exception())) from polling.exception()
