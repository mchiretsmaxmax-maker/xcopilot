"""«💬 Ответы»: режим ответов на посты по ссылке (ADR 0032).

Кнопка меню → состояние `ReplyFlow.link`. Ссылка на пост X читается без ключа
(`BotDeps.post_reader`, ADR 0031) и уходит писателю (`writer.generate`, формат reply) с
`view=reply`: после ворот качества варианты приходят лёгкими карточками с intent и
копированием. Чтение не удалось — следующий текст принимается как текст поста
(`ReplyFlow.manual`). Режим держится до «✖️ Выйти», /cancel или кнопки постоянного меню.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from aiogram import Bot, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from xcopilot.bot import keyboards, texts
from xcopilot.bot.context import AccountCtx, Recorder
from xcopilot.bot.deps import BotDeps
from xcopilot.bot.routers.write import (
    enqueue_generate,
    expand_link,
    post_lang,
    schedule_harvest,
)
from xcopilot.bot.states import ReplyFlow
from xcopilot.db.repos import DraftsRepo
from xcopilot.db.repos.drafts import DraftStatus
from xcopilot.db.repos.metrics import EventsRepo
from xcopilot.quality.jobs import QUALITY_GATE_KIND
from xcopilot.review.decisions import CardExpired
from xcopilot.writer.jobs import WRITER_GENERATE_KIND
from xcopilot.writer.schemas import TargetPost
from xcopilot.x.errors import XError
from xcopilot.x.models import XPost
from xcopilot.x.public_read import read_target
from xcopilot.x.shortlinks import only_short_links
from xcopilot.x.urls import parse_post_url

log = structlog.get_logger()

IN_MODE = StateFilter(ReplyFlow.link, ReplyFlow.manual)
OTHER_MENU = set(keyboards.REPLY_LABELS) - {keyboards.BTN_REPLIES}
TOPIC_CHARS = 200
NOPE_LINE_CHARS = 60
NOPE_REASON = "off_topic"
NOPE_COMMENT = "reply_nope"
REPLY_JOB_KINDS = (WRITER_GENERATE_KIND, QUALITY_GATE_KIND)


def nope_hint(first_lines: list[str]) -> str:
    """Подсказка писателю после «👎 Не то»: прошлые варианты не подошли, вот их начало."""
    quoted = "; ".join(f"«{line}»" for line in first_lines)
    return (
        f"Прошлые 3 варианта не подошли — напиши другие, по-другому. Они начинались так: {quoted}"
    )


DASH = "—"


async def _drop_message(bot: Bot, chat_id: int, message_id: int) -> None:
    """Убрать сообщение варианта: удалить, а если нельзя (старше 48 ч) — заменить на «—»."""
    try:
        await bot.delete_message(chat_id, message_id)
        return
    except Exception:
        log.info("reply_pick.delete_failed", message_id=message_id)
    try:
        await bot.edit_message_text(DASH, chat_id=chat_id, message_id=message_id, reply_markup=None)
    except Exception:
        log.info("reply_pick.dash_failed", message_id=message_id)


async def _set_markup(bot: Bot, chat_id: int, message_id: int, markup: Any) -> None:
    try:
        await bot.edit_message_reply_markup(
            chat_id=chat_id, message_id=message_id, reply_markup=markup
        )
    except Exception:  # «не изменено» при повторном нажатии и сбои сети не мешают выбору
        log.info("reply_pick.markup_failed", message_id=message_id)


def reply_view(post_id: str | None) -> dict[str, Any]:
    return {"kind": "reply", "post_id": post_id}


async def _read(deps: BotDeps, raw: str) -> XPost | None:
    ref = parse_post_url(raw)
    if ref is None or deps.post_reader is None:
        return None
    try:
        return await read_target(deps.post_reader, ref)  # тред — целиком (FxEmbed v2)
    except XError as exc:
        log.info("reply_mode.read_failed", post_id=ref.id, error=type(exc).__name__)
        return None


async def _busy(deps: BotDeps, acc: AccountCtx) -> bool:
    """Прошлые варианты режима ещё готовятся (писатель или ворота): новую ссылку не берём."""
    return await deps.queue.has_active_view(acc.id, REPLY_JOB_KINDS, "reply")


async def _generate(
    deps: BotDeps,
    acc: AccountCtx,
    state: FSMContext,
    target: TargetPost,
    post_id: str | None,
    lang: str | None = None,
    hint: str | None = None,
) -> None:
    topic = f"Ответ на пост: {target.text[:TOPIC_CHARS]}"
    await enqueue_generate(
        deps, acc, "reply", topic, target=target, view=reply_view(post_id), lang=lang, hint=hint
    )
    await state.set_state(ReplyFlow.link)
    data = {"target": target.model_dump(mode="json"), "post_id": post_id, "lang": lang}
    await state.set_data(data)


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
        manual = await state.get_state() == ReplyFlow.manual.state
        if not manual:
            raw, short_failed = await expand_link(deps, raw)
            if short_failed:
                await message.answer(texts.REPLY_SHORT_LINK_FAILED, reply_markup=keyboards.cancel())
                return
        ref = parse_post_url(raw)
        if (ref is not None or manual) and await _busy(deps, acc):
            await message.answer(texts.REPLY_BUSY)
            return
        if ref is not None:
            await rec("replies.link")
            post = await _read(deps, raw)
            if post is None or only_short_links(post.text):
                await state.set_state(ReplyFlow.manual)
                await state.update_data(pending={"url": ref.url, "post_id": ref.id})
                failed = texts.REPLY_READ_FAILED if post is None else texts.REPLY_MEDIA_ONLY
                await message.answer(failed, reply_markup=keyboards.cancel())
                return
            target = TargetPost(text=post.text, url=ref.url, author=post.author_handle)
            lang = post_lang(post, acc.lang)
            await _generate(deps, acc, state, target, ref.id, lang)
            await schedule_harvest(deps, acc.id, post, ref.url)  # тихий сбор (ADR 0034)
        elif manual and raw:
            await rec("replies.manual")
            pending = (await state.get_data()).get("pending") or {}
            target = TargetPost(text=raw, url=pending.get("url"))
            await _generate(deps, acc, state, target, pending.get("post_id"))
            if pending.get("post_id"):  # пост не прочитали, но id есть — сбор попробует сам
                manual_post = XPost(
                    id=str(pending["post_id"]),
                    text=raw,
                    author_handle=None,
                    created_at=None,
                    source="fxtwitter",
                )
                await schedule_harvest(deps, acc.id, manual_post, pending.get("url"))
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
        await _generate(deps, acc, state, target, data.get("post_id"), data.get("lang"))
        if deps.progress is None:
            await query.message.answer(texts.REPLY_WRITING)

    @router.callback_query(F.data.startswith("rpl:nope:"))
    async def on_nope(
        query: CallbackQuery,
        state: FSMContext,
        deps: BotDeps,
        acc: AccountCtx | None,
        user_id: UUID,
        rec: Recorder,
    ) -> None:
        """«👎 Не то»: все 3 варианта мимо → отказ off_topic и новая генерация с подсказкой."""
        await query.answer()
        kit = deps.review
        if acc is None or kit is None or not isinstance(query.message, Message):
            return
        try:
            run_id = UUID((query.data or "").rsplit(":", 1)[1])
        except (ValueError, IndexError):
            return
        await rec("replies.nope")
        async with deps.uow_factory() as uow:
            rows = await DraftsRepo(uow.session, acc.id).list_by_run(run_id)
        finals = {d.variant_no: d for d in rows}.values()  # последняя ревизия каждого варианта
        waiting = [d for d in finals if d.status == DraftStatus.SHOWN.value]
        if not waiting:
            await query.message.answer(texts.REPLY_NOPE_DONE)  # уже учтено
            return
        data = await state.get_data()
        if not data.get("target"):
            await query.message.answer(texts.REPLY_NO_TARGET)
            return
        if await _busy(deps, acc):
            await query.message.answer(texts.REPLY_BUSY)
            return
        lines: list[str] = []
        for d in waiting:
            try:
                await kit.decisions.reject(acc.id, d.id, user_id, NOPE_REASON, comment=NOPE_COMMENT)
            except CardExpired:
                continue
            first = (d.text.strip().splitlines() or [""])[0]
            lines.append(first[:NOPE_LINE_CHARS])
        target = TargetPost.model_validate(data["target"])
        await _generate(
            deps, acc, state, target, data.get("post_id"), data.get("lang"), nope_hint(lines)
        )
        if deps.progress is None:
            await query.message.answer(texts.REPLY_NOPE_WRITING)

    @router.callback_query(F.data.startswith("rpl:pick:"))
    async def on_pick(
        query: CallbackQuery,
        state: FSMContext,
        deps: BotDeps,
        acc: AccountCtx | None,
        user_id: UUID,
        rec: Recorder,
    ) -> None:
        """«✅ Выбрать»: approve выбранного, два других сообщения убираются, у выбранного —
        «↩️ Ответить в X» и «📋 Скопировать» (R5, ADR 0034 п. 8). Повтор безопасен."""
        kit = deps.review
        if acc is None or kit is None or not isinstance(query.message, Message):
            await query.answer()
            return
        try:
            draft_id = UUID((query.data or "").rsplit(":", 1)[1])
        except (ValueError, IndexError):
            await query.answer()
            return
        bot = query.bot
        if bot is None:
            await query.answer()
            return
        chat_id = query.message.chat.id
        async with deps.uow_factory() as uow:
            repo = DraftsRepo(uow.session, acc.id)
            draft = await repo.get(draft_id)
            rows = await repo.list_by_run(draft.run_id) if draft is not None else []
            sent = (
                await EventsRepo(uow.session, acc.id).reply_messages(draft.run_id) if draft else {}
            )
        if draft is None:
            await query.answer()
            return
        await rec("replies.pick")
        finals = {d.variant_no: d for d in rows}.values()
        chosen = next((d for d in finals if d.id == draft_id), draft)
        taken = [
            d
            for d in finals
            if d.id != draft_id
            and d.status in (DraftStatus.APPROVED.value, DraftStatus.EDITED.value)
        ]
        if taken:  # в этом показе уже выбран другой вариант (его сообщение осталось)
            await query.answer(texts.REPLY_PICK_TAKEN)
            return
        try:
            await kit.decisions.approve(acc.id, draft_id, user_id)
        except CardExpired:
            await query.answer(texts.REPLY_PICK_EXPIRED)
            return
        await query.answer(texts.REPLY_PICK_DONE)
        key = str(chat_id)
        for d in finals:
            if d.id == draft_id:
                continue
            mid = sent.get(str(d.id), {}).get(key)
            if mid is not None:
                await _drop_message(bot, chat_id, mid)
        post_id = (await state.get_data()).get("post_id")
        ref = parse_post_url(chosen.reply_to_url or "")
        post_id = ref.id if ref is not None else post_id
        markup = keyboards.reply_variant(chosen.text, str(post_id) if post_id else None)
        picked_mid = sent.get(str(draft_id), {}).get(key, query.message.message_id)
        await _set_markup(bot, chat_id, picked_mid, markup)
        footer = sent.get("footer", {}).get(key)
        if footer is not None:
            await _set_markup(bot, chat_id, footer, keyboards.reply_mode_after_pick())

    @router.callback_query(F.data == "rpl:exit")
    async def on_exit(query: CallbackQuery, state: FSMContext, rec: Recorder) -> None:
        await query.answer()
        await rec("replies.exit")
        await state.clear()
        if isinstance(query.message, Message):
            await query.message.answer(texts.REPLY_MODE_EXIT)

    return router
