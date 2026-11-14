"""Настройки → «Доступ к X»: режимы off / owned / lookup / paid и подключение X по OAuth 2.0 PKCE.

Шаги подключения: Client ID → Client Secret (сообщение удаляется, секрет в FSM только
зашифрованным) → ссылка авторизации → пользователь присылает адрес возврата → обмен кода
(`x/auth.py`) → `GET /2/users/me` за x_user_id. Перед платным чтением — оценка расходов.
"""

from __future__ import annotations

import contextlib
import re

import httpx
from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from xcopilot.bot import keyboards, texts
from xcopilot.bot.context import AccountCtx, Recorder
from xcopilot.bot.deps import BotDeps
from xcopilot.bot.notify import Notifier
from xcopilot.bot.states import XConnect
from xcopilot.db.repos import AccountsRepo, BudgetsRepo, SecretsRepo
from xcopilot.errors import BudgetExceeded
from xcopilot.llm.factory import build_budget_guard
from xcopilot.security.crypto import SecretBox
from xcopilot.x import cost
from xcopilot.x.api_v2 import XApiV2
from xcopilot.x.auth import (
    SECRET_TOKENS,
    XTokens,
    build_auth_url,
    exchange_code,
    parse_callback,
)
from xcopilot.x.client import CostMeter
from xcopilot.x.cost import DailyDedup
from xcopilot.x.errors import XAuthError, XError, XStateMismatch, XUnavailable

_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-:.~]{8,256}")
_HTTP_TIMEOUT = 20.0


def _box(deps: BotDeps) -> SecretBox | None:
    if not deps.settings.secrets_key:
        return None
    try:
        return SecretBox.from_string(deps.settings.secrets_key)
    except ValueError:
        return None


async def _connected(deps: BotDeps, acc: AccountCtx) -> bool:
    async with deps.uow_factory() as uow:
        return await SecretsRepo(uow.session, acc.id).get_by_kind(SECRET_TOKENS) is not None


async def _set_account(deps: BotDeps, acc: AccountCtx, **fields: str | None) -> None:
    async with deps.uow_factory() as uow:
        row = await AccountsRepo(uow.session).get(acc.id)
        if row is None:
            return
        for name, value in fields.items():
            setattr(row, name, value)


async def _x_day_limit(deps: BotDeps, acc: AccountCtx, scope: str = "x_api") -> int:
    async with deps.uow_factory() as uow:
        row = await BudgetsRepo(uow.session, acc.id).get_limit(scope, "day")
    if row is not None:
        return int(row.limit_micro_usd)
    usd = (
        deps.settings.x_lookup_daily_limit_usd
        if scope == "x_lookup"
        else (deps.settings.x_daily_limit_usd)
    )
    return round(usd * 1_000_000)


def build_router() -> Router:
    """Новый роутер (экземпляр нельзя подключить к двум диспетчерам)."""
    router = Router(name="x_connect")

    async def start_connect(message: Message, state: FSMContext, mode: str) -> None:
        await state.set_state(XConnect.client_id)
        await state.set_data({"x_mode": mode})
        await message.answer(texts.X_ASK_CLIENT_ID)

    async def set_mode(
        message: Message, state: FSMContext, deps: BotDeps, acc: AccountCtx, mode: str
    ) -> None:
        await _set_account(deps, acc, x_read_mode=mode)
        await state.clear()
        await message.answer(texts.xmode_saved(mode))

    @router.callback_query(F.data.startswith("xc:"))
    async def on_x_button(
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
        if data == "xc:open":
            await rec("x.access_open")
            await message.answer(
                texts.x_access_summary(acc.x_read_mode, await _connected(deps, acc)),
                reply_markup=keyboards.x_access_menu(),
            )
        elif data == "xc:off":
            await rec("x.mode_off")
            await set_mode(message, state, deps, acc, "off")
        elif data == "xc:owned":
            await rec("x.mode_owned")
            if await _connected(deps, acc):
                await set_mode(message, state, deps, acc, "owned")
            else:
                await start_connect(message, state, "owned")
        elif data == "xc:lookup":
            await rec("x.lookup_estimate")
            limit = await _x_day_limit(deps, acc, "x_lookup")
            await message.answer(
                texts.x_lookup_estimate(cost.PRICES[cost.POST], limit),
                reply_markup=keyboards.x_lookup_confirm(),
            )
        elif data == "xc:lookup_yes":
            await rec("x.mode_lookup")
            if await _connected(deps, acc):
                await set_mode(message, state, deps, acc, "lookup")
            else:
                await start_connect(message, state, "lookup")
        elif data == "xc:paid":
            await rec("x.paid_estimate")
            limit = await _x_day_limit(deps, acc)
            await message.answer(
                texts.x_paid_estimate(cost.PRICES[cost.POST], cost.PRICES[cost.USER], limit),
                reply_markup=keyboards.x_paid_confirm(),
            )
        elif data == "xc:paid_yes":
            await rec("x.mode_paid")
            if await _connected(deps, acc):
                await set_mode(message, state, deps, acc, "paid")
            else:
                await start_connect(message, state, "paid")
        elif data == "xc:cancel":
            await rec("x.cancel")
            await state.clear()
            await message.answer(texts.X_CANCELLED)

    @router.message(XConnect.client_id, F.text, ~F.text.startswith("/"))
    async def on_client_id(message: Message, state: FSMContext, rec: Recorder) -> None:
        client_id = (message.text or "").strip()
        if not _TOKEN_RE.fullmatch(client_id):
            await message.answer(texts.X_BAD_CLIENT_ID)
            return
        await rec("x.client_id")
        await state.update_data(x_client_id=client_id)
        await state.set_state(XConnect.client_secret)
        await message.answer(texts.X_ASK_CLIENT_SECRET)

    @router.message(XConnect.client_secret, F.text, ~F.text.startswith("/"))
    async def on_client_secret(
        message: Message, state: FSMContext, deps: BotDeps, rec: Recorder
    ) -> None:
        secret = (message.text or "").strip()
        with contextlib.suppress(TelegramAPIError):
            await message.delete()
        if not _TOKEN_RE.fullmatch(secret):
            await message.answer(texts.X_BAD_CLIENT_SECRET)
            return
        box = _box(deps)
        if box is None:
            await state.clear()
            await message.answer(texts.X_NO_SECRETS_KEY)
            return
        data = await state.get_data()
        redirect_uri = deps.settings.x_redirect_uri
        url, oauth_state, verifier = build_auth_url(str(data["x_client_id"]), redirect_uri)
        await rec("x.auth_link")
        await state.update_data(
            x_secret=box.encrypt(secret).decode(),
            x_verifier=box.encrypt(verifier).decode(),
            x_state=oauth_state,
        )
        await state.set_state(XConnect.callback)
        await message.answer(texts.x_auth_link(url, redirect_uri))

    @router.message(XConnect.callback, F.text, ~F.text.startswith("/"))
    async def on_callback_url(
        message: Message,
        state: FSMContext,
        deps: BotDeps,
        acc: AccountCtx | None,
        rec: Recorder,
    ) -> None:
        if acc is None:
            await message.answer(texts.NO_ACCOUNT)
            return
        box = _box(deps)
        if box is None:
            await state.clear()
            await message.answer(texts.X_NO_SECRETS_KEY)
            return
        data = await state.get_data()
        try:
            code = parse_callback(message.text or "", str(data["x_state"]))
        except XStateMismatch as exc:
            await rec("x.callback_rejected")
            await message.answer(str(exc))
            return
        except XAuthError:
            await message.answer(texts.X_BAD_CALLBACK)
            return
        mode = str(data.get("x_mode", "owned"))
        client_id = str(data["x_client_id"])
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as http:
            try:
                await exchange_code(
                    http,
                    deps.uow_factory,
                    box,
                    deps.clock,
                    account_id=acc.id,
                    client_id=client_id,
                    client_secret=box.decrypt(str(data["x_secret"]).encode()),
                    code=code,
                    verifier=box.decrypt(str(data["x_verifier"]).encode()),
                    redirect_uri=deps.settings.x_redirect_uri,
                )
            except XUnavailable:
                await message.answer(texts.X_UNAVAILABLE)
                return
            except XAuthError as exc:
                await rec("x.connect_failed")
                await state.clear()
                await message.answer(texts.x_auth_failed(str(exc)))
                return
            await rec("x.connected")
            await state.clear()
            me_handle, me_id = await _fetch_me(http, deps, acc, box)
        await _set_account(deps, acc, x_read_mode=mode, **({"x_user_id": me_id} if me_id else {}))
        if me_id:
            await message.answer(texts.x_connected(mode, me_handle))
        else:
            await message.answer(texts.x_connected_without_id(mode))

    return router


async def _fetch_me(
    http: httpx.AsyncClient, deps: BotDeps, acc: AccountCtx, box: SecretBox
) -> tuple[str | None, str | None]:
    """(хэндл, id) своего аккаунта X; (None, None), если X не ответил."""
    tokens = XTokens(http, deps.uow_factory, box, deps.clock, Notifier(deps.bot, deps.uow_factory))
    api = XApiV2(
        http,
        lambda: tokens.get_access_token(acc.id),
        clock=deps.clock,
        sleep=_no_wait,
        max_rate_limit_waits=0,
    )
    meter = CostMeter(
        account_id=acc.id,
        kind=cost.OWNED_USER,
        # расход x_api страж считает по x_api_calls, которые пишет CostMeter: record не нужен
        budget=build_budget_guard(deps.settings, deps.uow_factory, deps.clock),
        uow_factory=deps.uow_factory,
        clock=deps.clock,
        dedup=DailyDedup(),
    )
    try:
        me = await api.get_me(meter=meter)
    except (XError, BudgetExceeded):
        return None, None  # подключение не срывается: id получим при первом синке
    return me.handle, me.id


async def _no_wait(seconds: float) -> None:
    return None
