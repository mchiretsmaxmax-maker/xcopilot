"""Задачи этапа 14: `analytics.sync`, `analytics.snapshot`, `reports.daily`."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

from xcopilot.analytics.analyst import Analyst, render_claims
from xcopilot.analytics.drift import drift_alert
from xcopilot.analytics.reports import Reports
from xcopilot.analytics.sync import OwnSync
from xcopilot.db.repos import AccountsRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.errors import NotFound
from xcopilot.jobs.queue import Job
from xcopilot.metrics.rollup import local_today

REPORTS_DAILY_KIND = "reports.daily"


async def handle_analytics_sync(job: Job, *, sync: OwnSync) -> int:
    """Синк своей ленты аккаунта; вернуть число новых постов."""
    return (await sync.sync_own_posts(job.account_id)).new_posts


async def handle_analytics_snapshot(job: Job, *, sync: OwnSync) -> bool:
    """Контрольный снимок метрик поста: payload {"x_post_id", "checkpoint_min"}."""
    result = await sync.take_snapshot(
        job.account_id, str(job.payload["x_post_id"]), job.payload.get("checkpoint_min")
    )
    return result.taken


async def handle_reports_daily(
    job: Job,
    *,
    reports: Reports,
    notifier: Any,
    uow_factory: Callable[[], UnitOfWork],
    analyst: Analyst | None = None,
) -> str:
    """Ежедневный отчёт владельцу за вчера (по поясу аккаунта), алерт дрейфа голоса и по
    понедельникам — недельный отчёт аналитика."""
    async with uow_factory() as uow:
        account = await AccountsRepo(uow.session).get(job.account_id)
        if account is None:
            raise NotFound(f"аккаунт {job.account_id} не найден")
        tz = account.tz
        today = local_today(reports.now(), tz)
        monday = reports.now().astimezone(ZoneInfo(tz)).weekday() == 0
    text = await reports.daily_report(job.account_id, today - timedelta(days=1))
    await notifier.notify(job.account_id, text)
    async with uow_factory() as uow:
        alert = await drift_alert(uow.session, job.account_id)
    if alert is not None:
        await notifier.notify(job.account_id, alert)
    if monday and analyst is not None:
        report = await analyst.build_report(job.account_id)
        body = "\n\n".join(
            render_claims(title, claims)
            for title, claims in (("Что зашло:", report.worked), ("Что улучшить:", report.improve))
            if claims
        )
        if body:
            await notifier.notify(job.account_id, f"📊 Неделя\n\n{body}")
    return text
