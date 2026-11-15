"""/queue: карточки, отложенные дневным пределом (ТЗ, этап 12, задача 11)."""

from __future__ import annotations

import html

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from xcopilot.bot import texts
from xcopilot.bot.context import AccountCtx, Recorder
from xcopilot.bot.deps import BotDeps
from xcopilot.review.callbacks import SHOW, card_cb


def build_router() -> Router:
    """Новый роутер (экземпляр нельзя подключить к двум диспетчерам)."""
    router = Router(name="queue")

    @router.message(Command("queue"))
    async def cmd_queue(
        message: Message, deps: BotDeps, acc: AccountCtx | None, rec: Recorder
    ) -> None:
        await rec("queue")
        if acc is None:
            await message.answer(texts.NO_ACCOUNT)
            return
        if deps.review is None:
            await message.answer(texts.QUEUE_EMPTY)
            return
        waiting = await deps.review.presenter.queued(acc.id)
        if not waiting:
            await message.answer(texts.QUEUE_EMPTY)
            return
        lines = [texts.queue_title(len(waiting))]
        lines += [f"{i}. {html.escape(q.title, quote=False)}" for i, q in enumerate(waiting, 1)]
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Показать", callback_data=card_cb(waiting[0].draft_id, SHOW)
                    )
                ]
            ]
        )
        await message.answer("\n".join(lines), reply_markup=keyboard, parse_mode="HTML")

    return router
