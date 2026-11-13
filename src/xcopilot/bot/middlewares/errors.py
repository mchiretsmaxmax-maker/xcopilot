"""Единая обработка ошибок: понятный текст и код пользователю, детали — в лог."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User

from xcopilot import errors
from xcopilot.bot import texts
from xcopilot.bot.deps import BotDeps

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
            code = error_code(exc)
            log.error("bot_error", code=code, error_type=type(exc).__name__, exc_info=True)
            user: User | None = data.get("event_from_user")
            if user is not None:
                try:
                    await self._deps.bot.send_message(user.id, texts.error(code))
                except Exception:
                    log.error("bot_error_reply_failed", code=code, exc_info=True)
            return None
