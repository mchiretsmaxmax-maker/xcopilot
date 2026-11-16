"""Постоянное меню: /menu и «⚙️ Ещё». Остальные кнопки обрабатывают свои роутеры."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from xcopilot.bot import keyboards, texts
from xcopilot.bot.context import AccountCtx, Recorder


def build_router() -> Router:
    router = Router(name="reply_menu")

    @router.message(Command("menu"))
    async def cmd_menu(message: Message, acc: AccountCtx | None, rec: Recorder) -> None:
        await rec("menu")
        if acc is None:
            await message.answer(texts.NO_ACCOUNT)
            return
        await message.answer(texts.REPLY_MENU_HINT, reply_markup=keyboards.reply_menu())

    @router.message(F.text == keyboards.BTN_MORE)
    async def on_more(message: Message, acc: AccountCtx | None, rec: Recorder) -> None:
        await rec("menu.more")
        if acc is None:
            await message.answer(texts.NO_ACCOUNT)
            return
        await message.answer("Что ещё?", reply_markup=keyboards.more_menu())

    @router.callback_query(F.data == "more:help")
    async def on_help(query: CallbackQuery) -> None:
        await query.answer()
        if isinstance(query.message, Message):
            await query.message.answer(texts.HELP)

    return router
