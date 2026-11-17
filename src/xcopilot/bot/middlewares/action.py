"""Одно действие за раз: новое действие закрывает прошлое; кнопки закрытого — «Это уже закрыто».

Заменяет молчаливый MenuResetMiddleware. Новое действие — кнопка нижней клавиатуры, команда
(кроме /cancel: её хэндлер закрывает сам), inline-кнопка меню (`menu:*`, `more:*`).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, TelegramObject

from xcopilot.bot import texts
from xcopilot.bot.keyboards import REPLY_LABELS
from xcopilot.bot.one_action import (
    CANCEL_CB,
    NAV_PREFIXES,
    UNTRACKED_PREFIXES,
    ActionTracker,
    label_for,
    strip,
)

# кнопки, которые работают и на закрытом сообщении: навигация, отмена, отмена задач
ALWAYS_LIVE = (*NAV_PREFIXES, *UNTRACKED_PREFIXES, CANCEL_CB, "jcancel:")


def trigger_of(event: TelegramObject) -> str | None:
    """Что начинает новое действие (подпись кнопки, команда, callback меню) или None."""
    if isinstance(event, Message):
        text = event.text or ""
        if text in REPLY_LABELS:
            return text
        if text.startswith("/") and not text.startswith("/cancel"):
            return text.split()[0]
    if isinstance(event, CallbackQuery) and (event.data or "").startswith(NAV_PREFIXES):
        return event.data
    return None


class ActionMiddleware(BaseMiddleware):
    def __init__(self, tracker: ActionTracker) -> None:
        self.tracker = tracker

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        state: FSMContext | None = data.get("state")
        if state is None:
            return await handler(event, data)
        trigger = trigger_of(event)
        if trigger is not None:
            bot = data["bot"]
            closed = await self.tracker.close(bot, state.key, label_for(trigger))
            data["raw_state"] = None
            note = closed.note()
            if note is not None:
                await bot.send_message(state.key.chat_id, note)
            return await handler(event, data)
        if isinstance(event, CallbackQuery) and await self._stale(event, state, data):
            return None
        return await handler(event, data)

    async def _stale(self, query: CallbackQuery, state: FSMContext, data: dict[str, Any]) -> bool:
        """Кнопка закрытого действия: ответить «Это уже закрыто» и снять её клавиатуру."""
        message = query.message
        if message is None or (query.data or "").startswith(ALWAYS_LIVE):
            return False
        if not await self.tracker.is_closed(state.key, message.message_id):
            return False
        await query.answer(texts.ALREADY_CLOSED, show_alert=False)
        await strip(data["bot"], message.chat.id, message.message_id)
        return True
