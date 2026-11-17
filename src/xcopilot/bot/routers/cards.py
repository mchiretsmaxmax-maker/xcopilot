"""Карточки черновиков (ТЗ, этап 12): кнопки c1:…, правка, ещё вариант, отказ, публикация.

Любое нажатие сначала проверяет, что черновик из аккаунта нажавшего (иначе — отказ), затем
срок карточки (48 часов). Ответ на нажатие — один: либо всплывающее пояснение, либо пустой.
"""

from __future__ import annotations

import html
from uuid import UUID

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from xcopilot.bot import keyboards, texts
from xcopilot.bot.context import AccountCtx, Recorder
from xcopilot.bot.deps import BotDeps
from xcopilot.bot.notify import DeliveryFailed
from xcopilot.bot.states import CardFlow
from xcopilot.db.repos import DraftsRepo
from xcopilot.db.repos.drafts import DraftStatus
from xcopilot.errors import InvalidTransition, NotFound
from xcopilot.review import callbacks as cb
from xcopilot.review.callbacks import CardCb, card_cb, from_short
from xcopilot.review.decisions import CardExpired
from xcopilot.review.kit import ReviewKit
from xcopilot.review.publish import ForeignLinkError, LinkError, PublishView
from xcopilot.x.models import PostRef

HTML = "HTML"


def _kb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _btn(text: str, draft_id: UUID, action: str, arg: str = "") -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=card_cb(draft_id, action, arg))


def publish_keyboard(view: PublishView) -> InlineKeyboardMarkup:
    row: list[InlineKeyboardButton] = []
    if view.intent_url is not None:
        row.append(InlineKeyboardButton(text="🚀 Открыть в X", url=view.intent_url))
    row.append(_btn("📋 Скопировать", view.draft_id, cb.COPY))
    row.append(_btn("✔️ Опубликовал", view.draft_id, cb.PUBLISHED))
    return _kb([row])


def more_keyboard(draft_id: UUID) -> InlineKeyboardMarkup:
    hints = [_btn(label, draft_id, cb.REGEN, code) for code, label in cb.REGEN_HINTS.items()]
    return _kb([hints, [_btn("Свой текст", draft_id, cb.REGEN, cb.REGEN_OWN)]])


def reject_keyboard(draft_id: UUID) -> InlineKeyboardMarkup:
    reasons = [_btn(t, draft_id, cb.REJECT_REASON, c) for c, t in cb.REJECT_REASONS.items()]
    return _kb([reasons[:3], reasons[3:]])


async def send_publish_block(
    message: Message, kit: ReviewKit, acc: AccountCtx, draft: UUID
) -> None:
    view = await kit.publisher.view(acc.id, draft)
    lines = [texts.READY_TO_PUBLISH]
    if view.mode == "copy":
        lines.append(texts.COPY_MODE)
    lines += ["", html.escape(view.text, quote=False)]
    await message.answer("\n".join(lines), reply_markup=publish_keyboard(view), parse_mode=HTML)


async def _status(deps: BotDeps, account_id: UUID, draft_id: UUID) -> str | None:
    async with deps.uow_factory() as uow:
        row = await DraftsRepo(uow.session, account_id).get(draft_id)
        return row.status if row is not None else None


def build_router() -> Router:
    """Новый роутер (экземпляр нельзя подключить к двум диспетчерам)."""
    router = Router(name="cards")

    @router.callback_query(CardCb.filter())
    async def on_card(
        query: CallbackQuery,
        callback_data: CardCb,
        state: FSMContext,
        deps: BotDeps,
        acc: AccountCtx | None,
        user_id: UUID,
        rec: Recorder,
    ) -> None:
        kit = deps.review
        message = query.message if isinstance(query.message, Message) else None
        if kit is None or message is None:
            await query.answer()
            return
        try:
            draft_id = from_short(callback_data.draft)
        except ValueError:
            await query.answer(texts.CARD_GONE, show_alert=True)
            return
        status = await _status(deps, acc.id, draft_id) if acc is not None else None
        if acc is None or status is None:
            await query.answer(texts.CARD_DENIED, show_alert=True)
            return
        action, arg = callback_data.action, callback_data.arg
        await rec(f"card.{action}")

        async def expired() -> None:
            await query.answer(texts.CARD_EXPIRED, show_alert=True)
            await kit.presenter.disable(message.chat.id, message.message_id)

        # кнопки самой карточки не работают после срока
        if action in (cb.NAV, cb.EDIT, cb.MORE, cb.REJECT, cb.WHY) and (
            await kit.presenter.is_expired(acc.id, draft_id)
        ):
            await expired()
            return
        try:
            if action == cb.NAV:
                if not await kit.presenter.navigate(
                    acc.id, draft_id, int(arg), message.chat.id, message.message_id
                ):
                    await expired()
                    return
                await query.answer()
            elif action == cb.APPROVE:
                result = await kit.decisions.approve(acc.id, draft_id, user_id)
                if result.repeated:
                    await query.answer(texts.CARD_ALREADY)
                    return
                await query.answer()
                await send_publish_block(message, kit, acc, draft_id)
            elif action == cb.EDIT:
                if status not in (DraftStatus.SHOWN, DraftStatus.EDITED):
                    await query.answer(texts.CARD_ALREADY)
                    return
                await query.answer()
                await state.set_state(CardFlow.edit)
                await state.update_data(card_draft=str(draft_id))
                await message.answer(texts.EDIT_ASK, reply_markup=keyboards.cancel())
            elif action == cb.MORE:
                await query.answer()
                await message.answer(texts.MORE_ASK, reply_markup=more_keyboard(draft_id))
            elif action == cb.REGEN:
                if arg == cb.REGEN_OWN:
                    await query.answer()
                    await state.set_state(CardFlow.regen_hint)
                    await state.update_data(card_draft=str(draft_id))
                    await message.answer(texts.REGEN_OWN_ASK, reply_markup=keyboards.cancel())
                    return
                await kit.decisions.regenerate(acc.id, draft_id, user_id, arg or None)
                await query.answer()
                await message.answer(texts.REGEN_STARTED)
            elif action == cb.REJECT:
                if status != DraftStatus.SHOWN:
                    await query.answer(texts.CARD_ALREADY)
                    return
                await query.answer()
                await message.answer(texts.REJECT_ASK, reply_markup=reject_keyboard(draft_id))
            elif action == cb.REJECT_REASON:
                result = await kit.decisions.reject(acc.id, draft_id, user_id, arg)
                if result.repeated:
                    await query.answer(texts.CARD_ALREADY)
                    return
                await query.answer()
                await message.answer(texts.REJECTED)
            elif action == cb.WHY:
                await query.answer()
                why = await kit.presenter.why(acc.id, draft_id)
                await message.answer(why, parse_mode=HTML)
            elif action == cb.COPY:
                await query.answer()
                view = await kit.publisher.view(acc.id, draft_id)
                for block in view.copy_blocks:
                    await message.answer(
                        f"<pre>{html.escape(block, quote=False)}</pre>", parse_mode=HTML
                    )
            elif action == cb.PUBLISHED:
                await query.answer()
                await state.set_state(CardFlow.link)
                await state.update_data(card_draft=str(draft_id))
                await message.answer(
                    texts.ASK_LINK,
                    reply_markup=keyboards.with_cancel(
                        _kb([[_btn("Найди сам", draft_id, cb.FIND)]])
                    ),
                )
            elif action == cb.FIND:
                await query.answer()
                await state.clear()
                await kit.publisher.confirm_published(acc.id, draft_id, None)
                await message.answer(texts.FIND_STARTED)
            elif action == cb.MATCH_YES:
                await kit.publisher.confirm_published(
                    acc.id, draft_id, PostRef(arg, acc.handle).url, matched_by="manual"
                )
                await query.answer()
                await message.answer(texts.PUBLISHED_OK)
            elif action == cb.MATCH_NO:
                await query.answer()
                await message.answer(texts.MATCH_NO_OK)
            elif action == cb.SHOW:
                async with deps.uow_factory() as uow:
                    run_id = (await DraftsRepo(uow.session, acc.id).require(draft_id)).run_id
                try:
                    await kit.presenter.show_run(
                        acc.id, run_id, chat_id=message.chat.id, force=True, strict=True
                    )
                except DeliveryFailed:
                    await query.answer(texts.CARD_SEND_FAILED, show_alert=True)
                    return
                await query.answer()
            else:
                await query.answer()
        except CardExpired:
            await expired()
        except (LinkError, InvalidTransition):
            await query.answer(texts.CARD_ALREADY, show_alert=True)

    @router.message(StateFilter(CardFlow.edit), F.text)
    async def on_edit_text(
        message: Message,
        state: FSMContext,
        deps: BotDeps,
        acc: AccountCtx | None,
        user_id: UUID,
    ) -> None:
        draft_id = await _draft_from_state(state)
        kit = deps.review
        if kit is None or acc is None or draft_id is None:
            await state.clear()
            return
        try:
            result = await kit.decisions.edit(acc.id, draft_id, user_id, message.text or "")
        except ValueError:
            await message.answer(texts.EDIT_EMPTY)
            return
        except CardExpired:
            await state.clear()
            await message.answer(texts.CARD_EXPIRED)
            return
        except NotFound:
            await state.clear()
            await message.answer(texts.CARD_GONE)
            return
        await state.clear()
        if result.repeated:
            await message.answer(texts.CARD_ALREADY)
            return
        if result.warnings:
            items = "\n".join(f"• {html.escape(w, quote=False)}" for w in result.warnings)
            await message.answer(f"{texts.LINT_WARNINGS}\n{items}", parse_mode=HTML)
        await send_publish_block(message, kit, acc, draft_id)
        if result.translation:  # перевод обновлённого оригинала, только для чтения
            ru = "\n\n".join(result.translation)
            await message.answer(
                f"{texts.EDIT_TRANSLATION}\n<blockquote expandable>"
                f"{html.escape(ru, quote=False)}</blockquote>",
                parse_mode=HTML,
            )

    @router.message(StateFilter(CardFlow.regen_hint), F.text)
    async def on_regen_hint(
        message: Message,
        state: FSMContext,
        deps: BotDeps,
        acc: AccountCtx | None,
        user_id: UUID,
    ) -> None:
        draft_id = await _draft_from_state(state)
        await state.clear()
        kit = deps.review
        if kit is None or acc is None or draft_id is None:
            return
        try:
            await kit.decisions.regenerate(acc.id, draft_id, user_id, message.text or None)
        except CardExpired:
            await message.answer(texts.CARD_EXPIRED)
            return
        await message.answer(texts.REGEN_STARTED)

    @router.message(StateFilter(CardFlow.link), F.text)
    async def on_link(
        message: Message, state: FSMContext, deps: BotDeps, acc: AccountCtx | None
    ) -> None:
        draft_id = await _draft_from_state(state)
        kit = deps.review
        if kit is None or acc is None or draft_id is None:
            await state.clear()
            return
        try:
            await kit.publisher.confirm_published(acc.id, draft_id, message.text or "")
        except ForeignLinkError:
            await message.answer(texts.LINK_FOREIGN)
            return
        except LinkError:
            await message.answer(texts.LINK_BAD)
            return
        except InvalidTransition:
            await state.clear()
            await message.answer(texts.CARD_ALREADY)
            return
        await state.clear()
        await message.answer(texts.PUBLISHED_OK)

    return router


async def _draft_from_state(state: FSMContext) -> UUID | None:
    raw = (await state.get_data()).get("card_draft")
    try:
        return UUID(str(raw)) if raw else None
    except ValueError:
        return None
