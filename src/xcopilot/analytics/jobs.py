"""Задачи этапа 14: analytics.sync, analytics.snapshot, reports.daily. Заглушка."""

from __future__ import annotations

from typing import Any

from xcopilot.analytics.reports import Reports
from xcopilot.analytics.sync import OwnSync
from xcopilot.jobs.queue import Job

REPORTS_DAILY_KIND = "reports.daily"


async def handle_analytics_sync(job: Job, *, sync: OwnSync) -> Any:
    raise NotImplementedError


async def handle_analytics_snapshot(job: Job, *, sync: OwnSync) -> Any:
    raise NotImplementedError


async def handle_reports_daily(
    job: Job, *, reports: Reports, notifier: Any, uow_factory: Any
) -> Any:
    raise NotImplementedError
