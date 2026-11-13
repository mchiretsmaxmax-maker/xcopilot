"""Настройки: часовой пояс, язык постов, дневные лимиты. «Доступ к X» — роутер x_connect."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from xcopilot.bot import keyboards, texts
from xcopilot.bot.context import AccountCtx, Recorder
from xcopilot.bot.deps import BotDeps
from xcopilot.bot.parsing import parse_timezone, parse_usd_to_micro
from xcopilot.bot.states import SettingsFlow
from xcopilot.db.repos import AccountsRepo, BudgetsRepo


def build_router() -> Router:
    """Новый роутер (экземпляр нельзя подключить к двум диспетчерам)."""
    router = Router(name="settings")

    _LIMITS = {"llm": ("llm", "моделей"), "x": ("x_api", "X")}

    async def show_settings(message: Message, acc: AccountCtx) -> None:
        await message.answer(
            texts.settings_summary(acc.tz, acc.lang, acc.x_read_mode),
            reply_markup=keyboards.settings_menu(),
        )

    async def _update_account(deps: BotDeps, acc: AccountCtx, **fields: str) -> None:
        async with deps.uow_factory() as uow:
            row = await AccountsRepo(uow.session).get(acc.id)
            if row is None:
                return
            for name, value in fields.items():
                setattr(row, name, value)

    @router.message(Command("settings"))
    async def cmd_settings(
        message: Message, state: FSMContext, acc: AccountCtx | None, rec: Recorder
    ) -> None:
        await rec("settings")
        await state.clear()
        if acc is None:
            await message.answer(texts.NO_ACCOUNT)
            return
        await show_settings(message, acc)

    @router.callback_query(F.data.startswith(("set:", "setlang:", "setlim:")))
    async def on_settings_button(
        query: CallbackQuery,
        state: FSMContext,
        deps: BotDeps,
        acc: AccountCtx | None,
        rec: Recorder,
    ) -> None:
        await query.answer()
        message = query.message
        data = query.data or ""
        if not isinstance(message, Message):
            return
        if acc is None:
            await message.answer(texts.NO_ACCOUNT)
            return
        if data == "set:tz":
            await rec("settings.tz_open")
            await state.set_state(SettingsFlow.tz)
            await message.answer(texts.ASK_TZ)
        elif data == "set:lang":
            await rec("settings.lang_open")
            await message.answer(texts.CHOOSE_LANG, reply_markup=keyboards.lang_choice("setlang"))
        elif data == "set:limits":
            await rec("settings.limits_open")
            await message.answer(texts.CHOOSE_LIMIT, reply_markup=keyboards.limits_menu())
        elif data.startswith("setlang:") and data[8:] in {"en", "ru"}:
            await _update_account(deps, acc, lang=data[8:])
            await rec("settings.lang")
            await message.answer(texts.lang_saved(data[8:]))
        elif data.startswith("setlim:") and data[7:] in _LIMITS:
            await rec("settings.limit_open")
            await state.set_state(
                SettingsFlow.limit_llm if data[7:] == "llm" else SettingsFlow.limit_x
            )
            await message.answer(texts.ASK_LIMIT)

    @router.message(SettingsFlow.tz, F.text, ~F.text.startswith("/"))
    async def on_timezone(
        message: Message,
        state: FSMContext,
        deps: BotDeps,
        acc: AccountCtx | None,
        rec: Recorder,
    ) -> None:
        if acc is None:
            await message.answer(texts.NO_ACCOUNT)
            return
        tz = parse_timezone(message.text or "")
        if tz is None:
            await message.answer(texts.BAD_TZ)
            return
        await _update_account(deps, acc, tz=tz)
        await rec("settings.tz")
        await state.clear()
        await message.answer(texts.tz_saved(tz))

    @router.message(SettingsFlow.limit_llm, F.text, ~F.text.startswith("/"))
    @router.message(SettingsFlow.limit_x, F.text, ~F.text.startswith("/"))
    async def on_limit(
        message: Message,
        state: FSMContext,
        deps: BotDeps,
        acc: AccountCtx | None,
        rec: Recorder,
    ) -> None:
        if acc is None:
            await message.answer(texts.NO_ACCOUNT)
            return
        micro = parse_usd_to_micro(message.text or "")
        if micro is None:
            await message.answer(texts.BAD_LIMIT)
            return
        key = "llm" if await state.get_state() == SettingsFlow.limit_llm.state else "x"
        scope, what = _LIMITS[key]
        async with deps.uow_factory() as uow:
            repo = BudgetsRepo(uow.session, acc.id)
            existing = await repo.get_limit(scope, "day")
            await repo.upsert(
                scope,
                "day",
                limit_micro_usd=micro,
                hard=existing.hard if existing is not None else True,
            )
        await rec("settings.limit")
        await state.clear()
        await message.answer(texts.limit_saved(what, micro))

    return router
