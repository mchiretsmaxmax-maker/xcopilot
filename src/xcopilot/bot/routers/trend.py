"""«🎯 Хвост тренда»: пост (ссылка или текст) → «Почему зашёл (гипотеза)» → три варианта.

Разбор берётся из `material_analyses` (этап 7A) сразу, генерация — задачей `writer.generate`.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from xcopilot.bot import texts
from xcopilot.bot.context import AccountCtx, Recorder
from xcopilot.bot.deps import BotDeps
from xcopilot.bot.routers.write import find_post
from xcopilot.bot.states import TrendFlow
from xcopilot.writer.jobs import WRITER_GENERATE_KIND, tail_payload
from xcopilot.writer.trend_tail import PostNotFound, analyze, resolve_post, why_summary


def build_router() -> Router:
    router = Router(name="trend")

    @router.callback_query(F.data == "menu:trend")
    async def on_open(
        query: CallbackQuery, state: FSMContext, acc: AccountCtx | None, rec: Recorder
    ) -> None:
        await query.answer()
        await rec("menu.trend")
        if not isinstance(query.message, Message):
            return
        if acc is None:
            await query.message.answer(texts.NO_ACCOUNT)
            return
        await state.set_state(TrendFlow.post)
        await query.message.answer(texts.TREND_ASK_POST)

    @router.message(TrendFlow.post, F.text)
    async def on_post(
        message: Message,
        state: FSMContext,
        deps: BotDeps,
        acc: AccountCtx | None,
        rec: Recorder,
    ) -> None:
        if acc is None:
            return
        await rec("trend.post")
        try:
            ref, _ = await find_post(deps, acc.id, message.text or "")
        except PostNotFound:
            await message.answer(texts.WRITE_TARGET_NOT_FOUND)
            return
        async with deps.uow_factory() as uow:
            why = await analyze(uow.session, acc.id, await resolve_post(uow.session, acc.id, ref))
        summary = why_summary(why) if why.available else ""
        await message.answer(texts.trend_why(summary) if summary else texts.TREND_NO_ANALYSIS)
        await deps.queue.enqueue(WRITER_GENERATE_KIND, tail_payload(ref), account_id=acc.id)
        await state.clear()
        await message.answer(texts.WRITING)

    return router
