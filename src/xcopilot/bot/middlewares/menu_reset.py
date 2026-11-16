"""Нажатие кнопки постоянного меню отменяет текущий шаг (иначе текст кнопки попал бы в шаг)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, TelegramObject

from xcopilot.bot.keyboards import REPLY_LABELS


class MenuResetMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        state: FSMContext | None = data.get("state")
        if (
            isinstance(event, Message)
            and event.text in REPLY_LABELS
            and state is not None
            and data.get("raw_state") is not None
        ):
            await state.clear()
            data["raw_state"] = None
        return await handler(event, data)
