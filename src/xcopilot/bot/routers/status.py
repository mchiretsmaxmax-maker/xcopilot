"""/status — что сейчас делает бот: в работе, очередь, сбои за сутки, последние действия, расход."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from xcopilot.bot import texts
from xcopilot.bot.access import can_manage
from xcopilot.bot.context import AccountCtx, Recorder
from xcopilot.bot.deps import BotDeps
from xcopilot.bot.failures import reason_from_error
from xcopilot.bot.progress import CANCEL_PREFIX, CANCELLED_LINE, progress_key_of
from xcopilot.db.repos import OpsRepo, UsageRepo

log = structlog.get_logger()

JOB_CANCEL_PREFIX = "jcancel:"
CANCEL_ALL = f"{JOB_CANCEL_PREFIX}all"
MAX_CANCEL_BUTTONS = 8

KIND_NAMES = {
    "writer.generate": "Пишу посты",
    "writer.topics": "Темы",
    "ingest.process": "Разбор материалов",
    "brain.analyze": "Разбор материалов",
    "brain.history_import": "Загрузка истории",
    "brain.detect": "Поиск знаний",
    "brain.extract": "Поиск знаний",
    "quality.gate": "Проверка качества",
    "cards.expire": "Карточки",
    "publications.match": "Сверка публикаций",
    "analytics.sync": "Аналитика",
    "analytics.snapshot": "Аналитика",
    "reports.daily": "Отчёт дня",
    "learning.propose": "Обучение на решениях",
}
OTHER = "Служебные задачи"


def kind_name(kind: str) -> str:
    return KIND_NAMES.get(kind, OTHER)


def _count(rows: list[dict[str, object]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        name = kind_name(str(r["kind"]))
        out[name] = out.get(name, 0) + int(str(r.get("n", 1)))
    return out


def render_status(
    snapshot: dict[str, list[dict[str, object]]],
    spend: dict[str, int],
    tz: ZoneInfo,
) -> str:
    running = _count(snapshot["running"])
    queued = _count(snapshot["queued"])
    lines = ["📡 Что делает бот", ""]
    lines.append("В работе: " + (", ".join(running) if running else "ничего"))
    if queued:
        lines.append("В очереди: " + ", ".join(f"{k}: {n}" for k, n in queued.items()))
    else:
        lines.append("В очереди: пусто")
    lines += ["", "Сорвалось за сутки:"]
    if snapshot["dead"]:
        for r in snapshot["dead"]:
            lines.append(
                f"• {kind_name(str(r['kind']))}: {reason_from_error(str(r['last_error']))}"
            )
    else:
        lines.append("• ничего")
    lines += ["", "Последние действия:"]
    if snapshot["recent"]:
        for r in snapshot["recent"]:
            at = r["finished_at"]
            when = at.astimezone(tz).strftime("%H:%M") if hasattr(at, "astimezone") else "—"
            lines.append(f"• {when} {kind_name(str(r['kind']))}")
    else:
        lines.append("• пока ничего")
    lines.append("")
    if spend:
        lines.append(
            "Расход сегодня: "
            + ", ".join(f"{r} ${v / 1_000_000:.2f}" for r, v in sorted(spend.items()))
        )
    else:
        lines.append("Расход сегодня: $0.00")
    return "\n".join(lines)


def cancel_keyboard(active: list[dict[str, Any]]) -> InlineKeyboardMarkup | None:
    """«❌ Отменить» у каждой задачи (понятных владельцу видов) и «Отменить все»."""
    rows = [r for r in active if str(r["kind"]) in KIND_NAMES]
    if not rows:
        return None
    buttons = [
        [
            InlineKeyboardButton(
                text=f"❌ Отменить: {kind_name(str(r['kind']))}"
                + (" (идёт)" if r["status"] == "running" else ""),
                callback_data=f"{JOB_CANCEL_PREFIX}{r['id']}",
            )
        ]
        for r in rows[:MAX_CANCEL_BUTTONS]
    ]
    buttons.append([InlineKeyboardButton(text="❌ Отменить все", callback_data=CANCEL_ALL)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def status_view(deps: BotDeps, acc: AccountCtx) -> tuple[str, InlineKeyboardMarkup | None]:
    """Текст /status и кнопки отмены задач."""
    tz = ZoneInfo(acc.tz)
    now = deps.clock.now()
    day = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    async with deps.uow_factory() as uow:
        snapshot = await OpsRepo(uow.session).status_snapshot(acc.id, now - timedelta(days=1))
        spend = await UsageRepo(uow.session, acc.id).llm_spend_by_role(day, day + timedelta(days=1))
    return render_status(snapshot, spend, tz), cancel_keyboard(snapshot.get("active", []))


async def status_text(deps: BotDeps, acc: AccountCtx) -> str:
    return (await status_view(deps, acc))[0]


async def _tell_progress(deps: BotDeps, job_ids: list[UUID]) -> None:
    """Статус-сообщения отменённых задач → «⛔ Отменено» (идемпотентно)."""
    if deps.progress is None:
        return
    for job_id in job_ids:
        job = await deps.queue.get(job_id)
        key = progress_key_of(job.payload) if job is not None else None
        if key:
            await deps.progress.cancelled(key)


def build_router() -> Router:
    router = Router(name="status")

    @router.message(Command("status"))
    async def cmd_status(
        message: Message, deps: BotDeps, acc: AccountCtx | None, rec: Recorder
    ) -> None:
        await rec("status")
        if acc is None:
            await message.answer(texts.NO_ACCOUNT)
            return
        text, keyboard = await status_view(deps, acc)
        await message.answer(text, reply_markup=keyboard)

    @router.callback_query(F.data == "more:status")
    async def on_more(query: CallbackQuery, deps: BotDeps, acc: AccountCtx | None) -> None:
        await query.answer()
        if isinstance(query.message, Message):
            if acc is None:
                await query.message.answer(texts.NO_ACCOUNT)
            else:
                text, keyboard = await status_view(deps, acc)
                await query.message.answer(text, reply_markup=keyboard)

    @router.callback_query(F.data.startswith(JOB_CANCEL_PREFIX))
    async def on_cancel_job(
        query: CallbackQuery, deps: BotDeps, acc: AccountCtx | None, user_id: UUID, rec: Recorder
    ) -> None:
        """«❌ Отменить» в /status: одна задача или все; потом статус перерисовывается."""
        if acc is None:
            await query.answer(texts.NO_ACCOUNT, show_alert=True)
            return
        if not await can_manage(deps, user_id, acc.id):
            await query.answer(texts.CANCEL_FORBIDDEN, show_alert=True)
            return
        target = (query.data or "").removeprefix(JOB_CANCEL_PREFIX)
        await rec("job.cancel")
        cancelled: list[UUID] = []
        if target == "all":
            cancelled = await deps.queue.cancel_all(acc.id, kinds=tuple(KIND_NAMES))
        else:
            try:
                job_id = UUID(target)
            except ValueError:
                await query.answer()
                return
            job = await deps.queue.get(job_id)
            mine = job is not None and job.account_id == acc.id
            if mine and await deps.queue.cancel(job_id, account_id=acc.id) == "cancelled":
                cancelled = [job_id]
        await _tell_progress(deps, cancelled)
        await query.answer(texts.CANCELLED_TOAST if cancelled else texts.NOTHING_TO_CANCEL)
        if isinstance(query.message, Message):
            text, keyboard = await status_view(deps, acc)
            try:
                await query.message.edit_text(text, reply_markup=keyboard)
            except TelegramBadRequest:
                log.debug("status_refresh_skipped", exc_info=True)

    @router.callback_query(F.data.startswith(CANCEL_PREFIX))
    async def on_cancel_progress(
        query: CallbackQuery, deps: BotDeps, acc: AccountCtx | None, user_id: UUID, rec: Recorder
    ) -> None:
        """«❌ Отменить» под статус-сообщением: все задачи этого потока."""
        if acc is None:
            await query.answer(texts.NO_ACCOUNT, show_alert=True)
            return
        if not await can_manage(deps, user_id, acc.id):
            await query.answer(texts.CANCEL_FORBIDDEN, show_alert=True)
            return
        key = (query.data or "").removeprefix(CANCEL_PREFIX)
        await rec("job.cancel")
        ids = await deps.queue.cancel_progress(acc.id, key)
        shown = await deps.progress.cancelled(key) if deps.progress is not None else False
        if not shown and isinstance(query.message, Message) and query.message.text:
            # бот перезапускался: статус забыт — дописать отметку прямо в сообщении
            try:
                await query.message.edit_text(f"{query.message.text}\n{CANCELLED_LINE}")
            except TelegramBadRequest:
                log.debug("progress_cancel_edit_skipped", exc_info=True)
        await query.answer(texts.CANCELLED_TOAST if ids else texts.NOTHING_TO_CANCEL)

    return router
