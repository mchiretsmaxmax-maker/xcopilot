"""Задача `ops.cleanup` (AUD-009): ежедневная очистка старых задач и отметок алертов."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

from xcopilot.clock import Clock
from xcopilot.db.repos.ops import OpsRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.jobs.queue import Job

CLEANUP_KIND = "ops.cleanup"
RETENTION = timedelta(days=30)


async def handle_ops_cleanup(
    job: Job, *, uow_factory: Callable[[], UnitOfWork], clock: Clock
) -> int:
    async with uow_factory() as uow:
        return await OpsRepo(uow.session).cleanup(job.account_id, clock.now() - RETENTION)
