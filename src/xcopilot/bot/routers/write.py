"""«✍️ Написать»: формат → (целевой пост для ответа и цитаты) → тема → «Пишу 3 варианта…».

Генерация идёт задачей очереди `writer.generate`; карточки присылает обработчик задачи.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

import structlog
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from xcopilot.bot import keyboards, texts
from xcopilot.bot.context import AccountCtx, Recorder
from xcopilot.bot.deps import BotDeps
from xcopilot.bot.states import WriteFlow
from xcopilot.brain.harvest import RepliesReader, harvest_payload
from xcopilot.brain.jobs import enqueue_harvest
from xcopilot.db.repos import SourcesRepo, TopicsRepo
from xcopilot.retrieval.context_pack import TaskSpec
from xcopilot.writer.formats import FORMATS
from xcopilot.writer.jobs import WRITER_GENERATE_KIND, generate_payload
from xcopilot.writer.schemas import TargetPost
from xcopilot.writer.trend_tail import PostNotFound, PostRef, resolve_post
from xcopilot.x.errors import XError
from xcopilot.x.models import PostRef as XPostRef
from xcopilot.x.models import XPost
from xcopilot.x.public_read import read_target
from xcopilot.x.urls import parse_post_url

log = structlog.get_logger()

TOPIC_CHOICES = 5


async def _read_public(deps: BotDeps, ref: XPostRef) -> XPost | None:
    if deps.post_reader is None:
        return None
    try:
        return await read_target(deps.post_reader, ref)  # тред — целиком (FxEmbed v2)
    except XError as exc:
        log.info("write.target_read_failed", post_id=ref.id, error=type(exc).__name__)
        return None


def post_lang(post: XPost | None, default: str) -> str:
    """Язык задачи ответа: язык поста по X (FxEmbed), иначе язык аккаунта (ADR 0034, п. 7)."""
    lang = (post.lang or "").lower() if post is not None else ""
    return lang if re.fullmatch(r"[a-z]{2}", lang) else default


async def schedule_harvest(deps: BotDeps, account_id: UUID, post: XPost, url: str | None) -> None:
    """Сбор ответов под постом (ADR 0034): только читатель с FxEmbed и включённый reply_learning.

    Сбой постановки не мешает ответу владельцу: сбор — фон, без сообщений.
    """
    if not isinstance(deps.post_reader, RepliesReader):
        return
    try:
        async with deps.uow_factory() as uow:
            await enqueue_harvest(
                uow.session, account_id, harvest_payload(post, url), now=deps.clock.now()
            )
    except Exception:
        log.warning("harvest.enqueue_failed", post_id=post.id, exc_info=True)


async def find_post(deps: BotDeps, account_id: UUID, raw: str) -> tuple[PostRef, TargetPost]:
    """Пост из сообщения: ссылка (база, иначе чтение без ключа, ADR 0031) или сам текст.

    Ссылку не удалось прочитать — PostNotFound (бот попросит текст).
    """
    url = parse_post_url(raw)
    async with deps.uow_factory() as uow:
        if url is not None:
            source = await SourcesRepo(uow.session, account_id).by_external_id(url.id)
            if source is None:
                public = await _read_public(deps, url)
                if public is None:
                    raise PostNotFound(raw)
                link = raw.strip().split("?", 1)[0]
                target = TargetPost(text=public.text, url=link, author=public.author_handle)
                await schedule_harvest(deps, account_id, public, link)
                return PostRef(text=public.text, url=link), target
            ref = PostRef(source_id=source.id)
            post = await resolve_post(uow.session, account_id, ref)
            return ref, TargetPost(text=post.text, url=source.url, author=source.author_handle)
        ref = PostRef(text=raw)
        post = await resolve_post(uow.session, account_id, ref)
    return ref, TargetPost(text=post.text)


async def enqueue_generate(
    deps: BotDeps,
    acc: AccountCtx,
    fmt: str,
    topic: str,
    *,
    topic_id: UUID | None = None,
    target: TargetPost | None = None,
    view: dict[str, Any] | None = None,
    lang: str | None = None,
    hint: str | None = None,
) -> UUID:
    task = TaskSpec(format=fmt, lang=lang or acc.lang, niche=acc.niche, topic=topic[:500])
    payload = generate_payload(task, hint=hint, topic_id=topic_id, target=target, view=view)
    if deps.progress is not None:
        key = await deps.progress.begin(acc.id, "post")
        if key:
            payload["progress"] = key
    return await deps.queue.enqueue(WRITER_GENERATE_KIND, payload, account_id=acc.id)


async def _topic_choices(deps: BotDeps, account_id: UUID) -> list[tuple[Any, str]]:
    async with deps.uow_factory() as uow:
        rows = await TopicsRepo(uow.session, account_id).list_by_status("new")
    return [(t.id, t.title) for t in rows[-TOPIC_CHOICES:]]


async def ask_topic(message: Message, deps: BotDeps, account_id: UUID) -> None:
    choices = await _topic_choices(deps, account_id)
    if choices:
        await message.answer(
            texts.WRITE_ASK_TOPIC,
            reply_markup=keyboards.with_cancel(keyboards.topic_choices(choices)),
        )
    else:
        await message.answer(texts.WRITE_ASK_TOPIC_FREE, reply_markup=keyboards.cancel())


def build_router() -> Router:
    router = Router(name="write")

    @router.message(F.text == keyboards.BTN_POST)
    async def on_button(message: Message, acc: AccountCtx | None, rec: Recorder) -> None:
        await rec("menu.write")
        if acc is None:
            await message.answer(texts.NO_ACCOUNT)
            return
        await message.answer(texts.WRITE_CHOOSE_FORMAT, reply_markup=keyboards.write_formats())

    @router.callback_query(F.data == "menu:write")
    async def on_open(query: CallbackQuery, acc: AccountCtx | None, rec: Recorder) -> None:
        await query.answer()
        await rec("menu.write")
        if isinstance(query.message, Message):
            if acc is None:
                await query.message.answer(texts.NO_ACCOUNT)
                return
            await query.message.answer(
                texts.WRITE_CHOOSE_FORMAT, reply_markup=keyboards.write_formats()
            )

    @router.callback_query(F.data.startswith("wfmt:"))
    async def on_format(
        query: CallbackQuery,
        state: FSMContext,
        deps: BotDeps,
        acc: AccountCtx | None,
        rec: Recorder,
    ) -> None:
        await query.answer()
        fmt = (query.data or "").removeprefix("wfmt:")
        if acc is None or fmt not in FORMATS or not isinstance(query.message, Message):
            return
        await rec(f"write.format.{fmt}")
        await state.set_data({"fmt": fmt})
        if FORMATS[fmt].needs_target:
            await state.set_state(WriteFlow.target)
            await query.message.answer(texts.WRITE_ASK_TARGET, reply_markup=keyboards.cancel())
            return
        await state.set_state(WriteFlow.topic)
        await ask_topic(query.message, deps, acc.id)

    @router.message(WriteFlow.target, F.text)
    async def on_target(
        message: Message,
        state: FSMContext,
        deps: BotDeps,
        acc: AccountCtx | None,
        rec: Recorder,
    ) -> None:
        if acc is None:
            return
        await rec("write.target")
        try:
            _, target = await find_post(deps, acc.id, message.text or "")
        except PostNotFound:
            await message.answer(texts.WRITE_TARGET_NOT_FOUND)
            return
        await state.update_data(target=target.model_dump(mode="json"))
        await state.set_state(WriteFlow.topic)
        await message.answer(texts.WRITE_ASK_REPLY_POINT, reply_markup=keyboards.cancel())

    @router.message(WriteFlow.topic, F.text)
    async def on_topic(
        message: Message,
        state: FSMContext,
        deps: BotDeps,
        acc: AccountCtx | None,
        rec: Recorder,
    ) -> None:
        if acc is None:
            return
        data = await state.get_data()
        fmt = str(data.get("fmt", "post"))
        raw = (message.text or "").strip()
        target = TargetPost.model_validate(data["target"]) if data.get("target") else None
        if raw == "-" and target is not None:
            raw = target.text[:200]
        if len(raw) < 3:
            await message.answer(texts.WRITE_TOPIC_TOO_SHORT)
            return
        await rec("write.topic")
        topic_id = UUID(data["topic_id"]) if data.get("topic_id") else None
        await enqueue_generate(deps, acc, fmt, raw, topic_id=topic_id, target=target)
        await state.clear()
        if deps.progress is None:
            await message.answer(texts.WRITING)

    @router.callback_query(F.data.startswith("wpick:"))
    async def on_pick(
        query: CallbackQuery,
        state: FSMContext,
        deps: BotDeps,
        acc: AccountCtx | None,
        rec: Recorder,
    ) -> None:
        await query.answer()
        if acc is None or not isinstance(query.message, Message):
            return
        await rec("write.pick_topic")
        fmt = str((await state.get_data()).get("fmt", "post"))
        await start_from_topic(query.message, state, deps, acc, (query.data or "")[6:], fmt)

    return router


async def start_from_topic(
    message: Message, state: FSMContext, deps: BotDeps, acc: AccountCtx, raw_id: str, fmt: str
) -> None:
    """Писать по теме из списка: ответ и цитата сначала спрашивают целевой пост."""
    try:
        topic_id = UUID(raw_id)
    except ValueError:
        await message.answer(texts.TOPIC_NOT_FOUND)
        return
    async with deps.uow_factory() as uow:
        repo = TopicsRepo(uow.session, acc.id)
        topic = await repo.get(topic_id)
        if topic is not None:
            await repo.set_status(topic_id, "in_progress")
    if topic is None:
        await message.answer(texts.TOPIC_NOT_FOUND)
        return
    fmt = fmt if fmt in FORMATS else "post"
    if FORMATS[fmt].needs_target:
        await state.set_data({"fmt": fmt, "topic_id": str(topic_id)})
        await state.set_state(WriteFlow.target)
        await message.answer(texts.WRITE_ASK_TARGET, reply_markup=keyboards.cancel())
        return
    title = f"{topic.title}. {topic.angle}" if topic.angle else topic.title
    await enqueue_generate(deps, acc, fmt, title, topic_id=topic_id)
    await state.clear()
    if deps.progress is None:
        await message.answer(texts.WRITING)
