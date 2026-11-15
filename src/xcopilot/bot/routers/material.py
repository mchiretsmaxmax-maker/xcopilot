"""Кнопки карточки разбора (ТЗ v3.1, раздел 5.2; этап 7A): исход, цифры, отношение.

`mo:<source>:hit|normal|flop` — оценка владельца; `mo:<source>:nums` — ждём строку цифр;
`ms:<source>:own|anti_example` — «Это моё» / «Антипример». «🗑 Убрать» — `irm:` из learn.
"""

from __future__ import annotations

from uuid import UUID

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from xcopilot.bot import texts
from xcopilot.bot.context import AccountCtx, Recorder
from xcopilot.bot.deps import BotDeps
from xcopilot.bot.states import MaterialFlow
from xcopilot.brain import outcomes, signals
from xcopilot.db.repos import SourcesRepo

STANCES = ("own", "anti_example")


def _parse(data: str | None, prefix: str) -> tuple[UUID, str] | None:
    parts = (data or "").split(":")
    if len(parts) != 3 or parts[0] != prefix:
        return None
    try:
        return UUID(parts[1]), parts[2]
    except ValueError:
        return None


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
            await message.answer(texts.MATERIAL_ASK_NUMBERS)
            return
        if choice not in signals.OWNER_LABELS:
            return
        now = deps.clock.now()
        async with deps.uow_factory() as uow:
            await signals.label(uow.session, acc.id, source_id, choice, user_id, observed_at=now)
            await outcomes.compute_outcome(uow.session, acc.id, source_id, now=now)
        await rec(f"material.label.{choice}")
        await message.answer(texts.material_label_saved(choice))

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
        await rec(f"material.stance.{stance}")
        await message.answer(texts.MATERIAL_STANCE_SET[stance])

    return router
