"""Главное меню: кнопки разделов."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from xcopilot.bot import keyboards, texts
from xcopilot.bot.context import AccountCtx, Recorder


def build_router() -> Router:
    """Новый роутер (экземпляр нельзя подключить к двум диспетчерам)."""
    router = Router(name="menu")

    SECTIONS = {"write", "topics", "learn", "trend", "analytics", "style", "settings"}

    @router.callback_query(F.data.startswith("menu:"))
    async def on_menu(query: CallbackQuery, acc: AccountCtx | None, rec: Recorder) -> None:
        section = (query.data or "").removeprefix("menu:")
        await query.answer()
        message = query.message
        if section not in SECTIONS or not isinstance(message, Message):
            return
        await rec(f"menu.{section}")
        if acc is None:
            await message.answer(texts.NO_ACCOUNT)
        elif section == "settings":
            await message.answer(
                texts.settings_summary(acc.tz, acc.lang, acc.x_read_mode),
                reply_markup=keyboards.settings_menu(),
            )
        elif section == "learn":
            await message.answer(texts.LEARN_HINT)
        else:
            await message.answer(texts.COMING_SOON)

    return router
