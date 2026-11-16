"""/status — что сейчас делает бот: в работе, очередь, сбои за сутки, последние действия, расход."""

from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from xcopilot.bot import texts
from xcopilot.bot.context import AccountCtx, Recorder
from xcopilot.bot.deps import BotDeps
from xcopilot.bot.failures import reason_from_error
from xcopilot.db.repos import OpsRepo, UsageRepo

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


async def status_text(deps: BotDeps, acc: AccountCtx) -> str:
    tz = ZoneInfo(acc.tz)
    now = deps.clock.now()
    day = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    async with deps.uow_factory() as uow:
        snapshot = await OpsRepo(uow.session).status_snapshot(acc.id, now - timedelta(days=1))
        spend = await UsageRepo(uow.session, acc.id).llm_spend_by_role(day, day + timedelta(days=1))
    return render_status(snapshot, spend, tz)


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
        await message.answer(await status_text(deps, acc))

    @router.callback_query(F.data == "more:status")
    async def on_more(query: CallbackQuery, deps: BotDeps, acc: AccountCtx | None) -> None:
        await query.answer()
        if isinstance(query.message, Message):
            if acc is None:
                await query.message.answer(texts.NO_ACCOUNT)
            else:
                await query.message.answer(await status_text(deps, acc))

    return router
