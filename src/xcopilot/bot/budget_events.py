"""Уведомления о бюджете: события `budget.warning` / `budget.exceeded` → задача → сообщение.

Подписчик `emit` ставит задачу `bot.notify_budget` в той же транзакции, что и событие
(откат события откатывает и уведомление); отправляет сообщение обработчик задачи.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from xcopilot.bot import keyboards, texts
from xcopilot.bot.context import AccountCtx, Recorder
from xcopilot.bot.deps import BotDeps
from xcopilot.bot.notify import Notifier
from xcopilot.db.repos import BudgetsRepo
from xcopilot.db.repos.jobs import JobsRepo
from xcopilot.db.uow import DbSession
from xcopilot.jobs.queue import Job
from xcopilot.metrics.events import EventType, add_listener

BOT_NOTIFY_BUDGET_KIND = "bot.notify_budget"
_WATCHED = {EventType.BUDGET_WARNING.value, EventType.BUDGET_EXCEEDED.value}
_NOW = datetime(1970, 1, 1, tzinfo=UTC)  # «сразу»: воркер берёт задачи с run_at ≤ его времени


async def enqueue_budget_notification(
    session: DbSession, account_id: UUID, event_type: str, props: dict[str, Any]
) -> None:
    """Подписчик emit: для событий бюджета поставить задачу уведомления."""
    if event_type not in _WATCHED:
        return
    await JobsRepo(session).enqueue(
        account_id=account_id,
        kind=BOT_NOTIFY_BUDGET_KIND,
        payload={"event_type": event_type, "props": props},
        run_at=_NOW,
        max_attempts=5,
        idempotency_key=None,
    )


def install_budget_notifications() -> None:
    """Подписаться на события бюджета (вызывается один раз при сборке приложения)."""
    add_listener(enqueue_budget_notification)


async def handle_budget_notification(job: Job, *, deps: BotDeps) -> int:
    """Обработчик задачи `bot.notify_budget`: отправить владельцам сообщение с кнопками."""
    event_type = job.payload["event_type"]
    props = job.payload["props"]
    args = (props["scope"], props["period"], props["spent_micro_usd"], props["limit_micro_usd"])
    notifier = Notifier(deps.bot, deps.uow_factory)
    if event_type == EventType.BUDGET_EXCEEDED.value:
        return await notifier.notify(
            job.account_id, texts.budget_exceeded(*args), keyboards.budget_actions()
        )
    return await notifier.notify(job.account_id, texts.budget_warning(*args))


def build_router() -> Router:
    """Новый роутер (экземпляр нельзя подключить к двум диспетчерам)."""
    router = Router(name="budget")

    @router.callback_query(F.data.in_({"budget:raise", "budget:wait"}))
    async def on_budget_button(
        query: CallbackQuery, deps: BotDeps, acc: AccountCtx | None, rec: Recorder
    ) -> None:
        await query.answer()
        message = query.message
        if not isinstance(message, Message):
            return
        if acc is None:
            await message.answer(texts.NO_ACCOUNT)
            return
        if query.data == "budget:wait":
            await rec("budget.wait")
            await message.answer(texts.BUDGET_WAIT)
            return
        async with deps.uow_factory() as uow:
            repo = BudgetsRepo(uow.session, acc.id)
            row = await repo.get_limit("llm", "day")
            if row is None:
                base = round(deps.settings.llm_daily_limit_usd * 1_000_000)
                new = base * 2
                await repo.upsert("llm", "day", limit_micro_usd=new)
            else:
                new = row.limit_micro_usd * 2
                await repo.upsert("llm", "day", limit_micro_usd=new, hard=row.hard)
        await rec("budget.raise")
        await message.answer(texts.budget_raised("модели", new))

    return router
