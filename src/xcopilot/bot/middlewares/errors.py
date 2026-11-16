"""Единая обработка ошибок: понятный текст и код пользователю, детали — в лог."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware
from aiogram.types import ErrorEvent, TelegramObject, User

from xcopilot import errors
from xcopilot.bot import texts
from xcopilot.bot.deps import BotDeps
from xcopilot.db.engine import is_db_unavailable

log = structlog.get_logger()

_CODES: tuple[tuple[type[Exception], str], ...] = (
    (errors.NotFound, "E404"),
    (errors.Forbidden, "E403"),
    (errors.BudgetExceeded, "E402"),
    (errors.InvalidTransition, "E409"),
    (errors.ExternalServiceError, "E502"),
    (errors.ConfigError, "E501"),
    (errors.AppError, "E400"),
)


def error_code(exc: Exception) -> str:
    """Код ошибки для пользователя; неизвестное исключение — E500."""
    if is_db_unavailable(exc):
        return "E503"
    for cls, code in _CODES:
        if isinstance(exc, cls):
            return code
    return "E500"


class ErrorMiddleware(BaseMiddleware):
    def __init__(self, deps: BotDeps) -> None:
        self._deps = deps

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        except Exception as exc:
            await report_error(self._deps, data.get("event_from_user"), exc)
            return None


async def report_error(deps: BotDeps, user: User | None, exc: Exception) -> None:
    """Код в лог, пользователю — понятный текст (при недоступной БД — «техработы»)."""
    code = error_code(exc)
    log.error("bot_error", code=code, error_type=type(exc).__name__, exc_info=exc)
    if user is None:
        return
    reply = texts.MAINTENANCE if code == "E503" else texts.error(code)
    try:
        await deps.bot.send_message(user.id, reply)
    except Exception:
        log.error("bot_error_reply_failed", code=code, exc_info=True)


def build_errors_handler(
    deps: BotDeps,
) -> Callable[..., Awaitable[bool]]:
    """Обработчик `dp.errors`: ошибки, случившиеся до ErrorMiddleware (FSM-хранилище в БД)."""

    async def handler(event: ErrorEvent, event_from_user: User | None = None) -> bool:
        exc = event.exception
        if isinstance(exc, Exception):
            await report_error(deps, event_from_user, exc)
        return True

    return handler
