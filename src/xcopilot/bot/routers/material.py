"""Кнопки карточки разбора (ТЗ v3.1, раздел 5.2; этап 7A): исход, цифры, отношение.

`mo:<source>:hit|normal|flop` — оценка владельца; `mo:<source>:nums` — ждём строку цифр;
`ms:<source>:own|anti_example` — «Это моё» / «Антипример». «🗑 Убрать» — `irm:` из learn.
`moe:<source>` — «✏️ Поправить оценку» автоисхода; `mbs:<пачка>:show:<с какого>|own|
inspiration|rate` — кнопки сводки пачки (ADR 0033).
"""

from __future__ import annotations

from uuid import UUID

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from xcopilot.bot import keyboards, texts
from xcopilot.bot.context import AccountCtx, Recorder
from xcopilot.bot.deps import BotDeps
from xcopilot.bot.states import MaterialFlow
from xcopilot.brain import batch, outcomes, signals
from xcopilot.brain.analyze import card_view
from xcopilot.brain.card import render_card
from xcopilot.db.repos import SourcesRepo
from xcopilot.db.repos.materials import MaterialOutcomesRepo
from xcopilot.style.auto import request_auto_rebuild

STANCES = ("own", "anti_example")


def _parse(data: str | None, prefix: str) -> tuple[UUID, str] | None:
    parts = (data or "").split(":")
    if len(parts) != 3 or parts[0] != prefix:
        return None
    try:
        return UUID(parts[1]), parts[2]
    except ValueError:
        return None


async def _send_cards(deps: BotDeps, account_id: UUID, message: Message, ids: list[UUID]) -> None:
    """Карточки разбора по одной; у материала с исходом вопроса «Как он зашёл?» нет."""
    for sid in ids:
        async with deps.uow_factory() as uow:
            view = await card_view(uow.session, account_id, sid)
        if view is None:
            continue
        text, keyboard = render_card(view)
        await message.answer(text, reply_markup=keyboard)


def build_router() -> Router:
    router = Router(name="material")

    @router.callback_query(F.data.startswith("mo:"))
    async def on_outcome(
        query: CallbackQuery,
        deps: BotDeps,
        acc: AccountCtx | None,
        user_id: UUID,
        rec: Recorder,
        state: FSMContext,
    ) -> None:
        await query.answer()
        message = query.message if isinstance(query.message, Message) else None
        parsed = _parse(query.data, "mo")
        if message is None or acc is None or parsed is None:
            return
        source_id, choice = parsed
        if choice == "nums":
            await state.set_state(MaterialFlow.numbers)
            await state.update_data(source_id=str(source_id))
            await message.answer(texts.MATERIAL_ASK_NUMBERS, reply_markup=keyboards.cancel())
            return
        if choice not in signals.OWNER_LABELS:
            return
        now = deps.clock.now()
        async with deps.uow_factory() as uow:
            session = uow.session
            current = await MaterialOutcomesRepo(session, acc.id).current(source_id)
            was_unrated = batch.bucket(current) == "unknown"
            await signals.label(session, acc.id, source_id, choice, user_id, observed_at=now)
            await outcomes.compute_outcome(session, acc.id, source_id, now=now)
            group = await batch.multi_batch(session, acc.id, source_id) if was_unrated else None
            left = await batch.unrated_ids(session, acc.id, group) if group else []
        await rec(f"material.label.{choice}")
        await message.answer(texts.material_label_saved(choice))
        if group is not None:  # оценка без цифр по одному: следующий материал пачки
            if left:
                await _send_cards(deps, acc.id, message, left[:1])
            else:
                await message.answer(texts.BATCH_ALL_RATED)

    @router.callback_query(F.data.startswith("moe:"))
    async def on_fix_outcome(query: CallbackQuery, acc: AccountCtx | None, rec: Recorder) -> None:
        await query.answer()
        message = query.message if isinstance(query.message, Message) else None
        _, _, raw = (query.data or "").partition(":")
        if message is None or acc is None:
            return
        try:
            source_id = UUID(raw)
        except ValueError:
            return
        await rec("material.fix_outcome")
        await message.answer(
            texts.MATERIAL_FIX_ASK, reply_markup=keyboards.material_outcome(str(source_id))
        )

    @router.callback_query(F.data.startswith("mbs:"))
    async def on_batch(
        query: CallbackQuery, deps: BotDeps, acc: AccountCtx | None, rec: Recorder
    ) -> None:
        await query.answer()
        message = query.message if isinstance(query.message, Message) else None
        parts = (query.data or "").split(":")
        if message is None or acc is None or len(parts) not in (3, 4):
            return
        try:
            group = str(UUID(parts[1]))
        except ValueError:
            return
        action = parts[2]
        if action == "show":
            try:
                offset = max(0, int(parts[3])) if len(parts) == 4 else 0
            except ValueError:
                return
            async with deps.uow_factory() as uow:
                ids = await batch.analyzed_ids(uow.session, acc.id, group)
            await rec("material.batch.show")
            if not ids:
                await message.answer(texts.BATCH_EMPTY)
                return
            page = ids[offset : offset + batch.PAGE]
            await _send_cards(deps, acc.id, message, page)
            left = len(ids) - offset - len(page)
            if left > 0:
                await message.answer(
                    texts.batch_more(left),
                    reply_markup=keyboards.batch_more(group, offset + batch.PAGE, left),
                )
        elif action == "rate":
            async with deps.uow_factory() as uow:
                left_ids = await batch.unrated_ids(uow.session, acc.id, group)
            await rec("material.batch.rate")
            if left_ids:
                await _send_cards(deps, acc.id, message, left_ids[:1])
            else:
                await message.answer(texts.BATCH_ALL_RATED)
        elif action in batch.BATCH_STANCES:
            async with deps.uow_factory() as uow:
                n = await batch.set_stance(uow.session, acc.id, group, action, now=deps.clock.now())
            await rec(f"material.batch.{action}")
            await message.answer(texts.BATCH_STANCE_SET[action].format(n=n))

    @router.message(StateFilter(MaterialFlow.numbers), F.text)
    async def on_numbers(
        message: Message,
        deps: BotDeps,
        acc: AccountCtx | None,
        user_id: UUID,
        rec: Recorder,
        state: FSMContext,
    ) -> None:
        if acc is None:
            return
        sig = signals.parse_numbers(message.text or "")
        if sig is None:
            await message.answer(texts.MATERIAL_NUMBERS_NOT_FOUND)
            return
        raw_id = (await state.get_data()).get("source_id")
        await state.clear()
        if not isinstance(raw_id, str):
            return
        source_id = UUID(raw_id)
        now = deps.clock.now()
        async with deps.uow_factory() as uow:
            await signals.add_signal(
                uow.session, acc.id, source_id, sig, user_id=user_id, observed_at=now
            )
            await outcomes.compute_outcome(uow.session, acc.id, source_id, now=now)
        await rec("material.numbers")
        await message.answer(texts.material_numbers_saved(sig.views, sig.likes, sig.replies))

    @router.callback_query(F.data.startswith("ms:"))
    async def on_stance(
        query: CallbackQuery, deps: BotDeps, acc: AccountCtx | None, rec: Recorder
    ) -> None:
        await query.answer()
        message = query.message if isinstance(query.message, Message) else None
        parsed = _parse(query.data, "ms")
        if message is None or acc is None or parsed is None or parsed[1] not in STANCES:
            return
        source_id, stance = parsed
        async with deps.uow_factory() as uow:
            await SourcesRepo(uow.session, acc.id).update(source_id, stance=stance)
            if stance == "own":  # «это моё» → проверка автопересборки паспорта
                await request_auto_rebuild(uow.session, acc.id, now=deps.clock.now())
        await rec(f"material.stance.{stance}")
        await message.answer(texts.MATERIAL_STANCE_SET[stance])

    return router
