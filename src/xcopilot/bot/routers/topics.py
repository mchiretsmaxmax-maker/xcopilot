"""«💡 Темы»: пять тем задачей `writer.topics`; у каждой [Писать] и [Не то]."""

from __future__ import annotations

from uuid import UUID

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from xcopilot.bot import texts
from xcopilot.bot.context import AccountCtx, Recorder
from xcopilot.bot.deps import BotDeps
from xcopilot.bot.routers.write import start_from_topic
from xcopilot.db.repos import TopicsRepo
from xcopilot.errors import NotFound
from xcopilot.writer.jobs import WRITER_TOPICS_KIND


def build_router() -> Router:
    router = Router(name="topics")

    @router.callback_query(F.data == "menu:topics")
    async def on_open(
        query: CallbackQuery, deps: BotDeps, acc: AccountCtx | None, rec: Recorder
    ) -> None:
        await query.answer()
        await rec("menu.topics")
        if not isinstance(query.message, Message):
            return
        if acc is None:
            await query.message.answer(texts.NO_ACCOUNT)
            return
        await deps.queue.enqueue(WRITER_TOPICS_KIND, {"n": 5}, account_id=acc.id)
        await query.message.answer(texts.TOPICS_SEARCHING)

    @router.callback_query(F.data.startswith("wtopic:"))
    async def on_write(
        query: CallbackQuery,
        state: FSMContext,
        deps: BotDeps,
        acc: AccountCtx | None,
        rec: Recorder,
    ) -> None:
        await query.answer()
        if acc is None or not isinstance(query.message, Message):
            return
        await rec("topics.write")
        _, raw_id, *rest = (query.data or "").split(":")
        await start_from_topic(query.message, state, deps, acc, raw_id, rest[0] if rest else "post")

    @router.callback_query(F.data.startswith("wdrop:"))
    async def on_drop(
        query: CallbackQuery, deps: BotDeps, acc: AccountCtx | None, rec: Recorder
    ) -> None:
        await query.answer()
        if acc is None or not isinstance(query.message, Message):
            return
        await rec("topics.drop")
        try:
            async with deps.uow_factory() as uow:
                await TopicsRepo(uow.session, acc.id).set_status(
                    UUID((query.data or "")[6:]), "dropped"
                )
        except (ValueError, NotFound):
            await query.message.answer(texts.TOPIC_NOT_FOUND)
            return
        await query.message.answer(texts.TOPIC_DROPPED)

    return router
