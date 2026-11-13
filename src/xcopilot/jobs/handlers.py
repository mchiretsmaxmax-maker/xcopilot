"""Обработчики фоновых задач (вид задачи → функция).

`metrics.rollup`: payload пустой — пересчитать вчерашний день по поясу аккаунта;
`{"day": "YYYY-MM-DD"}` — указанный день; `{"date_from": …, "date_to": …}` — бэкфилл диапазона.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

from xcopilot.clock import Clock
from xcopilot.db.repos import AccountsRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.errors import NotFound
from xcopilot.jobs.queue import Job
from xcopilot.metrics.catalog import sync_catalog
from xcopilot.metrics.rollup import account_tz, backfill, local_today, run_rollups

METRICS_ROLLUP_KIND = "metrics.rollup"


async def handle_metrics_rollup(
    job: Job, *, uow_factory: Callable[[], UnitOfWork], clock: Clock
) -> int:
    """Свёртка метрик аккаунта задачи; возвращает число записанных строк."""
    async with uow_factory() as uow:
        session = uow.session
        if await AccountsRepo(session).get(job.account_id) is None:
            raise NotFound(f"аккаунт {job.account_id} не найден")
        await sync_catalog(session)
        payload = job.payload
        if "date_from" in payload and "date_to" in payload:
            return await backfill(
                session,
                job.account_id,
                date.fromisoformat(payload["date_from"]),
                date.fromisoformat(payload["date_to"]),
                clock=clock,
            )
        if "day" in payload:
            day = date.fromisoformat(payload["day"])
        else:
            tz = await account_tz(session, job.account_id)
            day = local_today(clock.now(), tz) - timedelta(days=1)
        return await run_rollups(session, job.account_id, day, clock=clock)
