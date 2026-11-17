"""/start и /cancel."""

from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from xcopilot.bot import keyboards, texts
from xcopilot.bot.context import AccountCtx, Recorder
from xcopilot.bot.one_action import CANCEL_CB, ActionTracker
from xcopilot.bot.states import Onboarding


def build_router() -> Router:
    """Новый роутер (экземпляр нельзя подключить к двум диспетчерам)."""
    router = Router(name="start")

    @router.message(CommandStart())
    async def cmd_start(
        message: Message, state: FSMContext, acc: AccountCtx | None, rec: Recorder
    ) -> None:
        await rec("start")
        await state.clear()
        if acc is None:
            await state.set_state(Onboarding.niche)
            await message.answer(texts.ASK_NICHE, reply_markup=keyboards.niche_presets())
        else:
            await message.answer(texts.REPLY_MENU_HINT, reply_markup=keyboards.reply_menu())

    async def cancel(bot: Bot, state: FSMContext, tracker: ActionTracker) -> str:
        """/cancel и «✖️ Отменить»: снять кнопки действия и очистить шаг."""
        closed = await tracker.close(bot, state.key)
        return texts.CANCELLED if closed.opened else texts.NOTHING_TO_CANCEL

    @router.message(Command("cancel"))
    async def cmd_cancel(
        message: Message, bot: Bot, state: FSMContext, tracker: ActionTracker, rec: Recorder
    ) -> None:
        await rec("cancel")
        await message.answer(await cancel(bot, state, tracker))

    @router.callback_query(F.data == CANCEL_CB)
    async def on_cancel(
        query: CallbackQuery, bot: Bot, state: FSMContext, tracker: ActionTracker, rec: Recorder
    ) -> None:
        await rec("cancel")
        await query.answer()
        await bot.send_message(state.key.chat_id, await cancel(bot, state, tracker))

    return router
