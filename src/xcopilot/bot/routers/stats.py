"""/stats и «📊 Аналитика» (этап 14, задачи 9–11): цифры из `metric_values`, выводы аналитика.

Режим X `off`: охватов нет — показываются только продуктовые метрики.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from xcopilot.analytics.analyst import render_claims
from xcopilot.analytics.patterns import MIN_SAMPLE
from xcopilot.bot import keyboards, texts
from xcopilot.bot.context import AccountCtx, Recorder
from xcopilot.bot.deps import BotDeps
from xcopilot.db.repos import AnalyticsRepo
from xcopilot.metrics.rollup import local_today

WINDOW_DAYS = 7
CB_WORKED, CB_IMPROVE, CB_NUMBERS = "an:worked", "an:improve", "an:numbers"

SUM, WEIGHTED = "sum", "weighted"
# (метрика, подпись, способ свёртки, формат, нужны_охваты)
LINES: tuple[tuple[str, str, str, str, bool], ...] = (
    ("posts_published_day", "Постов опубликовано", SUM, "int", False),
    ("approval_rate", "Доля одобренных", WEIGHTED, "pct", False),
    ("zero_edit_rate", "Одобрено без правок", WEIGHTED, "pct", False),
    ("llm_cost_day", "Расход моделей", SUM, "usd", False),
    ("x_api_cost_day", "Расход на X", SUM, "usd", False),
    ("impressions_24h_median", "Показы на 24 ч (медиана)", WEIGHTED, "int", True),
    ("engagement_rate_24h", "Вовлечённость на 24 ч", WEIGHTED, "pct", True),
    ("followers_delta_day", "Подписчики за период", SUM, "int", True),
)


def _fmt(value: Decimal, kind: str) -> str:
    if kind == "pct":
        return f"{round(float(value) * 100)}%"
    if kind == "usd":
        return f"${float(value) / 1_000_000:.2f}"
    return f"{round(float(value)):,}".replace(",", " ")


def keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Что зашло", callback_data=CB_WORKED),
                InlineKeyboardButton(text="Что улучшить", callback_data=CB_IMPROVE),
                InlineKeyboardButton(text="Цифры", callback_data=CB_NUMBERS),
            ]
        ]
    )


async def stats_text(deps: BotDeps, acc: AccountCtx) -> str:
    """Свёртка последних 7 дней из `metric_values` (по поясу аккаунта)."""
    today = local_today(deps.clock.now(), acc.tz)
    hidden = acc.x_read_mode == "off"
    async with deps.uow_factory() as uow:
        rows = await AnalyticsRepo(uow.session, acc.id).daily_values(
            today - timedelta(days=WINDOW_DAYS), today
        )
    by_key: dict[str, list[Any]] = {}
    for r in rows:
        by_key.setdefault(r["metric_key"], []).append(r)
    lines: list[str] = []
    posts = 0
    for key, label, how, kind, needs_reach in LINES:
        if (needs_reach and hidden) or key not in by_key:
            continue
        items = by_key[key]
        total: Decimal
        if how == SUM:
            total = sum((Decimal(r["value"]) for r in items), Decimal(0))
        else:
            weight = sum(max(int(r["sample_size"]), 1) for r in items)
            total = sum(
                (Decimal(r["value"]) * max(int(r["sample_size"]), 1) for r in items), Decimal(0)
            ) / Decimal(weight)
        if key == "posts_published_day":
            posts = int(total)
        lines.append(f"{label}: {_fmt(total, kind)}")
    if not lines:
        return texts.STATS_EMPTY
    out = [texts.STATS_TITLE, *lines]
    if hidden:
        out.append(texts.STATS_OFF_NOTE)
    elif posts < MIN_SAMPLE:
        out.append(texts.STATS_SMALL_SAMPLE)
    return "\n".join(out)


def build_router() -> Router:
    """Новый роутер (экземпляр нельзя подключить к двум диспетчерам)."""
    router = Router(name="stats")

    async def show(target: Message, deps: BotDeps, acc: AccountCtx | None) -> None:
        if acc is None:
            await target.answer(texts.NO_ACCOUNT)
            return
        await target.answer(await stats_text(deps, acc), reply_markup=keyboard())

    @router.message(F.text == keyboards.BTN_STATS)
    @router.message(Command("stats"))
    async def cmd_stats(
        message: Message, deps: BotDeps, acc: AccountCtx | None, rec: Recorder
    ) -> None:
        await rec("stats")
        await show(message, deps, acc)

    @router.callback_query(F.data == "menu:analytics")
    async def on_menu(
        query: CallbackQuery, deps: BotDeps, acc: AccountCtx | None, rec: Recorder
    ) -> None:
        await query.answer()
        await rec("menu.analytics")
        if isinstance(query.message, Message):
            await show(query.message, deps, acc)

    @router.callback_query(F.data.in_({CB_WORKED, CB_IMPROVE, CB_NUMBERS}))
    async def on_button(
        query: CallbackQuery, deps: BotDeps, acc: AccountCtx | None, rec: Recorder
    ) -> None:
        await query.answer()
        message = query.message
        if not isinstance(message, Message):
            return
        if acc is None:
            await message.answer(texts.NO_ACCOUNT)
            return
        if query.data == CB_NUMBERS:
            await show(message, deps, acc)
            return
        if acc.x_read_mode == "off":
            await message.answer(texts.ANALYST_OFF)
            return
        if deps.analytics is None:
            await message.answer(texts.ANALYST_UNAVAILABLE)
            return
        await rec("stats.analyst")
        report = await deps.analytics.analyst.build_report(acc.id)
        worked = query.data == CB_WORKED
        claims = report.worked if worked else report.improve
        title = texts.ANALYST_WORKED_TITLE if worked else texts.ANALYST_IMPROVE_TITLE
        body = render_claims(title, claims) if claims else texts.ANALYST_NOTHING
        if report.warning:
            body = f"{report.warning}\n\n{body}"
        await message.answer(body)

    return router
