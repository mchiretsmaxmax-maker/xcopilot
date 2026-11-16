"""Задача очереди `learning.propose` (этап 13, ADR 0026): раз в сутки проверяет, пора ли учиться."""

from __future__ import annotations

from typing import TYPE_CHECKING

from xcopilot.brain import segments
from xcopilot.brain.digest import Notifier
from xcopilot.learning.from_reviews import LEARNING_PROPOSE_KIND, run
from xcopilot.learning.triggers import should_run

if TYPE_CHECKING:
    from xcopilot.brain.jobs import BrainDeps
    from xcopilot.jobs.queue import Job

__all__ = ["LEARNING_PROPOSE_KIND", "handle_learning_propose"]


async def handle_learning_propose(job: Job, *, deps: BrainDeps, notifier: Notifier) -> int:
    """Если набралось 5 новых ревью или прошла неделя — проход обучения; число принятых знаний.

    payload {"force": true} пропускает проверку запуска. Новые знания класса A уходят владельцу
    отдельными сообщениями («Заметил… [Да] [Нет]»); класс C уже работает как предупреждение.
    """
    from xcopilot.bot.brain_view import render_class_a

    async with deps.uow_factory() as uow:
        ctx = deps.ctx(uow.session, job.account_id)
        if not job.payload.get("force") and not await should_run(ctx):
            return 0
        result = await run(ctx)
        fresh = set(result.item_ids)
        pending = [
            i for i in await segments.class_a_pending(uow.session, job.account_id) if i.id in fresh
        ]
    for item in pending:
        text, keyboard = render_class_a(item)
        await notifier.notify(job.account_id, text, keyboard)
    return len(result.candidates)
