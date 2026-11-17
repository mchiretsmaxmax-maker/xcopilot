"""«💬 Ответы»: режим ответов на посты по ссылке (ADR 0032).

Кнопка меню → состояние `ReplyFlow.link`. Ссылка на пост X читается без ключа
(`BotDeps.post_reader`, ADR 0031) и уходит писателю (`writer.generate`, формат reply) с
`view=reply`: после ворот качества варианты приходят лёгкими карточками с intent и
копированием. Чтение не удалось — следующий текст принимается как текст поста
(`ReplyFlow.manual`). Режим держится до «✖️ Выйти», /cancel или кнопки постоянного меню.
"""

from __future__ import annotations

from typing import Any

import structlog
from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from xcopilot.bot import keyboards, texts
from xcopilot.bot.context import AccountCtx, Recorder
from xcopilot.bot.deps import BotDeps
from xcopilot.bot.routers.write import enqueue_generate
from xcopilot.bot.states import ReplyFlow
from xcopilot.quality.jobs import QUALITY_GATE_KIND
from xcopilot.writer.jobs import WRITER_GENERATE_KIND
from xcopilot.writer.schemas import TargetPost
from xcopilot.x.errors import XError
from xcopilot.x.models import XPost
from xcopilot.x.urls import parse_post_url

log = structlog.get_logger()

IN_MODE = StateFilter(ReplyFlow.link, ReplyFlow.manual)
OTHER_MENU = set(keyboards.REPLY_LABELS) - {keyboards.BTN_REPLIES}
TOPIC_CHARS = 200
REPLY_JOB_KINDS = (WRITER_GENERATE_KIND, QUALITY_GATE_KIND)


def reply_view(post_id: str | None) -> dict[str, Any]:
    return {"kind": "reply", "post_id": post_id}


async def _read(deps: BotDeps, raw: str) -> XPost | None:
    ref = parse_post_url(raw)
    if ref is None or deps.post_reader is None:
        return None
    try:
        return await deps.post_reader.fetch(ref)
    except XError as exc:
        log.info("reply_mode.read_failed", post_id=ref.id, error=type(exc).__name__)
        return None


async def _busy(deps: BotDeps, acc: AccountCtx) -> bool:
    """Прошлые варианты режима ещё готовятся (писатель или ворота): новую ссылку не берём."""
    return await deps.queue.has_active_view(acc.id, REPLY_JOB_KINDS, "reply")


async def _generate(
    deps: BotDeps, acc: AccountCtx, state: FSMContext, target: TargetPost, post_id: str | None
) -> None:
    topic = f"Ответ на пост: {target.text[:TOPIC_CHARS]}"
    await enqueue_generate(deps, acc, "reply", topic, target=target, view=reply_view(post_id))
    await state.set_state(ReplyFlow.link)
    await state.set_data({"target": target.model_dump(mode="json"), "post_id": post_id})


async def _enter(message: Message, state: FSMContext, acc: AccountCtx | None) -> None:
    if acc is None:
        await message.answer(texts.NO_ACCOUNT)
        return
    await state.set_state(ReplyFlow.link)
    await state.set_data({})
    await message.answer(texts.REPLY_MODE_ENTER, reply_markup=keyboards.cancel())


def build_router() -> Router:
    router = Router(name="replies")

    @router.message(F.text == keyboards.BTN_REPLIES)
    async def on_enter(
        message: Message, state: FSMContext, acc: AccountCtx | None, rec: Recorder
    ) -> None:
        await rec("menu.replies")
        await _enter(message, state, acc)

    @router.callback_query(F.data == "menu:replies")
    async def on_enter_inline(
        query: CallbackQuery, state: FSMContext, acc: AccountCtx | None, rec: Recorder
    ) -> None:
        await query.answer()
        if not isinstance(query.message, Message):
            return
        await rec("menu.replies")
        await _enter(query.message, state, acc)

    @router.message(IN_MODE, F.text.in_(OTHER_MENU))
    async def on_menu(message: Message, state: FSMContext) -> None:
        await state.clear()  # кнопка меню выводит из режима и делает своё
        raise SkipHandler

    @router.message(IN_MODE, F.text)
    async def on_text(
        message: Message,
        state: FSMContext,
        deps: BotDeps,
        acc: AccountCtx | None,
        rec: Recorder,
    ) -> None:
        raw = (message.text or "").strip()
        if raw.startswith("/"):
            raise SkipHandler  # команды работают как обычно
        if acc is None:
            return
        ref = parse_post_url(raw)
        manual = await state.get_state() == ReplyFlow.manual.state
        if (ref is not None or manual) and await _busy(deps, acc):
            await message.answer(texts.REPLY_BUSY)
            return
        if ref is not None:
            await rec("replies.link")
            post = await _read(deps, raw)
            if post is None:
                await state.set_state(ReplyFlow.manual)
                await state.update_data(pending={"url": ref.url, "post_id": ref.id})
                await message.answer(texts.REPLY_READ_FAILED, reply_markup=keyboards.cancel())
                return
            target = TargetPost(text=post.text, url=ref.url, author=post.author_handle)
            await _generate(deps, acc, state, target, ref.id)
        elif manual and raw:
            await rec("replies.manual")
            pending = (await state.get_data()).get("pending") or {}
            target = TargetPost(text=raw, url=pending.get("url"))
            await _generate(deps, acc, state, target, pending.get("post_id"))
        else:
            await message.answer(texts.REPLY_NEED_LINK, reply_markup=keyboards.cancel())
            return
        if deps.progress is None:
            await message.answer(texts.REPLY_WRITING)

    @router.callback_query(F.data == "rpl:more")
    async def on_more(
        query: CallbackQuery,
        state: FSMContext,
        deps: BotDeps,
        acc: AccountCtx | None,
        rec: Recorder,
    ) -> None:
        await query.answer()
        if acc is None or not isinstance(query.message, Message):
            return
        await rec("replies.more")
        data = await state.get_data()
        if not data.get("target"):
            await query.message.answer(texts.REPLY_NO_TARGET)
            return
        if await _busy(deps, acc):
            await query.message.answer(texts.REPLY_BUSY)
            return
        target = TargetPost.model_validate(data["target"])
        await _generate(deps, acc, state, target, data.get("post_id"))
        if deps.progress is None:
            await query.message.answer(texts.REPLY_WRITING)

    @router.callback_query(F.data == "rpl:exit")
    async def on_exit(query: CallbackQuery, state: FSMContext, rec: Recorder) -> None:
        await query.answer()
        await rec("replies.exit")
        await state.clear()
        if isinstance(query.message, Message):
            await query.message.answer(texts.REPLY_MODE_EXIT)

    return router
