"""Онбординг: ниша и язык → примеры стиля → конкуренты → подтверждение."""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from xcopilot.bot import keyboards, texts
from xcopilot.bot.context import AccountCtx, Recorder
from xcopilot.bot.deps import BotDeps
from xcopilot.bot.parsing import classify_sample, parse_handles
from xcopilot.bot.states import Onboarding
from xcopilot.db.repos import AccountsRepo, SourcesRepo
from xcopilot.db.repos.jobs import JobsRepo


def build_router() -> Router:
    """Новый роутер (экземпляр нельзя подключить к двум диспетчерам)."""
    router = Router(name="onboarding")

    NICHES = {"crypto": "Крипта", "ai": "ИИ", "finance": "Финансы"}
    MIN_SAMPLES = 5
    MAX_SAMPLES = 10
    MAX_COMPETITORS = 10
    INGEST_KIND = "ingest.process"

    def _hash(*parts: str) -> str:
        return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()

    async def _ask_lang(message: Message, state: FSMContext) -> None:
        await state.set_state(Onboarding.lang)
        await message.answer(texts.ASK_LANG, reply_markup=keyboards.lang_choice("lang"))

    async def _go_competitors(message: Message, state: FSMContext) -> None:
        await state.set_state(Onboarding.competitors)
        await message.answer(
            texts.ASK_COMPETITORS, reply_markup=keyboards.single("Пропустить", "comp:skip")
        )

    async def _go_confirm(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        await state.set_state(Onboarding.confirm)
        await message.answer(
            texts.confirm_summary(
                data.get("niche", "—"),
                data.get("lang", "ru"),
                len(data.get("samples", [])),
                data.get("competitors", []),
            ),
            reply_markup=keyboards.yes_no("confirm", "Всё верно", "Изменить"),
        )

    def _msg(query: CallbackQuery) -> Message | None:
        return query.message if isinstance(query.message, Message) else None

    # --- шаг 1: ниша и язык ---------------------------------------------------------------

    @router.callback_query(Onboarding.niche, F.data.startswith("niche:"))
    async def on_niche_button(query: CallbackQuery, state: FSMContext, rec: Recorder) -> None:
        await query.answer()
        message = _msg(query)
        key = (query.data or "").removeprefix("niche:")
        if message is None:
            return
        if key == "custom":
            await rec("onboarding.niche_custom")
            await message.answer(texts.ASK_NICHE_CUSTOM)
            return
        if key not in NICHES:
            return
        await rec("onboarding.niche")
        await state.update_data(niche=NICHES[key])
        await _ask_lang(message, state)

    @router.message(Onboarding.niche, F.text, ~F.text.startswith("/"))
    async def on_niche_text(message: Message, state: FSMContext, rec: Recorder) -> None:
        niche = (message.text or "").strip()[:100]
        if not niche:
            return
        await rec("onboarding.niche")
        await state.update_data(niche=niche)
        await _ask_lang(message, state)

    @router.callback_query(Onboarding.lang, F.data.startswith("lang:"))
    async def on_lang(query: CallbackQuery, state: FSMContext, rec: Recorder) -> None:
        await query.answer()
        message = _msg(query)
        lang = (query.data or "").removeprefix("lang:")
        if message is None or lang not in {"en", "ru"}:
            return
        await rec("onboarding.lang")
        await state.update_data(lang=lang, samples=[], competitors=[])
        await state.set_state(Onboarding.style)
        await message.answer(texts.ASK_STYLE)

    # --- шаг 2: примеры стиля -------------------------------------------------------------

    @router.message(Onboarding.style, F.text, ~F.text.startswith("/"))
    async def on_sample(message: Message, state: FSMContext, rec: Recorder) -> None:
        data = await state.get_data()
        samples: list[dict[str, str]] = list(data.get("samples", []))
        if len(samples) >= MAX_SAMPLES:
            await message.answer(
                texts.STYLE_FULL, reply_markup=keyboards.single("Дальше", "style:next")
            )
            return
        kind, value = classify_sample(message.text or "")
        if not value:
            return
        samples.append({"kind": kind, "value": value})
        await state.update_data(samples=samples)
        await rec("onboarding.sample")
        await message.answer(
            texts.style_counter(len(samples)), reply_markup=keyboards.single("Дальше", "style:next")
        )

    @router.callback_query(Onboarding.style, F.data == "style:next")
    async def on_style_next(query: CallbackQuery, state: FSMContext, rec: Recorder) -> None:
        await query.answer()
        message = _msg(query)
        if message is None:
            return
        count = len((await state.get_data()).get("samples", []))
        await rec("onboarding.style_next")
        if count == 0:
            await message.answer(texts.STYLE_NEED_ONE)
        elif count < MIN_SAMPLES:
            await message.answer(texts.STYLE_LOW, reply_markup=keyboards.yes_no("style"))
        else:
            await _go_competitors(message, state)

    @router.callback_query(Onboarding.style, F.data == "style:yes")
    async def on_style_continue(query: CallbackQuery, state: FSMContext, rec: Recorder) -> None:
        await query.answer()
        message = _msg(query)
        if message is None:
            return
        await rec("onboarding.style_continue")
        await _go_competitors(message, state)

    @router.callback_query(Onboarding.style, F.data == "style:no")
    async def on_style_more(query: CallbackQuery, rec: Recorder) -> None:
        await query.answer()
        message = _msg(query)
        if message is None:
            return
        await rec("onboarding.style_more")
        await message.answer(texts.STYLE_MORE)

    # --- шаг 3: конкуренты ----------------------------------------------------------------

    @router.message(Onboarding.competitors, F.text, ~F.text.startswith("/"))
    async def on_competitors_text(message: Message, state: FSMContext, rec: Recorder) -> None:
        valid, garbage = parse_handles(message.text or "")
        if not valid:
            await message.answer(texts.COMPETITOR_HINT)
            return
        data = await state.get_data()
        known: list[str] = list(data.get("competitors", []))
        for handle in valid:
            if handle not in known and len(known) < MAX_COMPETITORS:
                known.append(handle)
        await state.update_data(competitors=known)
        await rec("onboarding.competitor")
        await message.answer(
            texts.competitors_added(valid, garbage),
            reply_markup=keyboards.single("Дальше", "comp:next"),
        )

    @router.callback_query(Onboarding.competitors, F.data.in_({"comp:next", "comp:skip"}))
    async def on_competitors_done(query: CallbackQuery, state: FSMContext, rec: Recorder) -> None:
        await query.answer()
        message = _msg(query)
        if message is None:
            return
        await rec(
            "onboarding.competitors_skip"
            if query.data == "comp:skip"
            else "onboarding.competitors_next"
        )
        await _go_confirm(message, state)

    # --- подтверждение --------------------------------------------------------------------

    @router.callback_query(Onboarding.confirm, F.data == "confirm:no")
    async def on_confirm_edit(query: CallbackQuery, state: FSMContext, rec: Recorder) -> None:
        await query.answer()
        message = _msg(query)
        if message is None:
            return
        await rec("onboarding.confirm_edit")
        await state.set_data({})
        await state.set_state(Onboarding.niche)
        await message.answer(texts.ASK_NICHE, reply_markup=keyboards.niche_presets())

    @router.callback_query(Onboarding.confirm, F.data == "confirm:yes")
    async def on_confirm(
        query: CallbackQuery,
        state: FSMContext,
        deps: BotDeps,
        acc: AccountCtx | None,
        user_id: UUID,
        rec: Recorder,
    ) -> None:
        await query.answer()
        message = _msg(query)
        if message is None or query.from_user is None:
            return
        if acc is not None:  # повторное нажатие после создания
            await state.clear()
            await message.answer(texts.MENU, reply_markup=keyboards.main_menu())
            return
        data = await state.get_data()
        account_id = await create_account(deps, user_id, query.from_user.id, data)
        await rec.for_account(account_id)("onboarding.confirm")
        await state.clear()
        await message.answer(texts.STUDYING, reply_markup=keyboards.main_menu())

    async def create_account(
        deps: BotDeps, user_id: UUID, tg_user_id: int, data: dict[str, Any]
    ) -> UUID:
        """Аккаунт, роль owner, источники (new) и задачи ingest.process — одной транзакцией."""
        samples: list[dict[str, str]] = list(data.get("samples", []))
        competitors: list[str] = list(data.get("competitors", []))
        own_handle = next((s["value"] for s in samples if s["kind"] == "handle"), None)
        async with deps.uow_factory() as uow:
            accounts = AccountsRepo(uow.session)
            handle = own_handle or f"user{tg_user_id}"
            if await accounts.get_by_handle(handle) is not None:
                handle = f"user{tg_user_id}"
            account = await accounts.create(
                handle=handle,
                niche=data.get("niche"),
                lang=data.get("lang", "ru"),
                tz=deps.settings.default_tz,
            )
            await accounts.add_member(account.id, user_id, role="owner")
            sources = SourcesRepo(uow.session, account.id)
            created: list[UUID] = []
            for sample in samples:
                kind, value = sample["kind"], sample["value"]
                if kind == "text":
                    source, new = await sources.add_or_get(
                        kind="own_post", origin="manual", content_hash=_hash("own_text", value)
                    )
                    if new:
                        await sources.add_document(source.id, text=value)
                elif kind == "url":
                    source, new = await sources.add_or_get(
                        kind="own_post",
                        origin="oembed",
                        content_hash=_hash("own_url", value),
                        url=value,
                    )
                else:
                    source, new = await sources.add_or_get(
                        kind="own_post",
                        origin="x_api",
                        content_hash=_hash("own_handle", value),
                        author_handle=value,
                    )
                if new:
                    created.append(source.id)
            for comp in competitors:
                source, new = await sources.add_or_get(
                    kind="competitor_post",
                    origin="x_api",
                    content_hash=_hash("competitor", comp),
                    author_handle=comp,
                )
                if new:
                    created.append(source.id)
            jobs = JobsRepo(uow.session)
            now = deps.clock.now()
            for source_id in created:
                await jobs.enqueue(
                    account_id=account.id,
                    kind=INGEST_KIND,
                    payload={"source_id": str(source_id)},
                    run_at=now,
                    max_attempts=deps.queue.max_attempts,
                    idempotency_key=f"ingest:{source_id}",
                )
            return account.id

    return router
