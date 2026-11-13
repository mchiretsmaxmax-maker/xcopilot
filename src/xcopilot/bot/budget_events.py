"""Уведомления о бюджете: события `budget.warning` / `budget.exceeded` → задача → сообщение."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from xcopilot.bot.deps import BotDeps
from xcopilot.db.uow import DbSession
from xcopilot.jobs.queue import Job

BOT_NOTIFY_BUDGET_KIND = "bot.notify_budget"


async def enqueue_budget_notification(
    session: DbSession, account_id: UUID, event_type: str, props: dict[str, Any]
) -> None:
    raise NotImplementedError


def install_budget_notifications() -> None:
    """Подписаться на события бюджета (вызывается один раз при сборке приложения)."""
    raise NotImplementedError


async def handle_budget_notification(job: Job, *, deps: BotDeps) -> int:
    """Обработчик задачи `bot.notify_budget`: отправить владельцам сообщение с кнопками."""
    raise NotImplementedError
