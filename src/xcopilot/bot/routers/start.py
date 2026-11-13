"""/start и /cancel."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from xcopilot.bot import keyboards, texts
from xcopilot.bot.context import AccountCtx, Recorder
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
            await message.answer(texts.MENU, reply_markup=keyboards.main_menu())

    @router.message(Command("cancel"))
    async def cmd_cancel(message: Message, state: FSMContext, rec: Recorder) -> None:
        await rec("cancel")
        if await state.get_state() is None:
            await message.answer(texts.NOTHING_TO_CANCEL)
            return
        await state.clear()
        await message.answer(texts.CANCELLED)

    return router
