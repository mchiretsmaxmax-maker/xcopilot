"""Задача `x.recheck_metrics` (v3.1): повторное чтение метрик поста, прочитанного младше суток.

Ставит её `XService` (через 24 часа после публикации). Здесь — обработчик: перечитать пост
через XClient и дописать сигнал `x_api` к материалу с тем же id поста (если он уже есть).
Сигналы только добавляются — прежние цифры остаются.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from uuid import UUID

from xcopilot.clock import Clock
from xcopilot.db.repos import MaterialSignalsRepo, SourcesRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.jobs.queue import Job
from xcopilot.x.client import XClient
from xcopilot.x.models import PostRef


async def handle_recheck_metrics(
    job: Job,
    *,
    client_for: Callable[[UUID], XClient],
    uow_factory: Callable[[], UnitOfWork],
    clock: Clock,
) -> bool:
    """Перечитать пост задачи; True — сигнал записан. Сбой чтения пробрасывается (повтор)."""
    post_id = str(job.payload["post_id"])
    handle = job.payload.get("handle")
    post = await client_for(job.account_id).get_post(PostRef(post_id, handle))
    if post.source != "api" or post.metrics is None:
        return False
    m = post.metrics
    age_hours = (
        Decimal(str(round((clock.now() - post.created_at).total_seconds() / 3600, 2)))
        if post.created_at
        else None
    )
    async with uow_factory() as uow:
        source = await SourcesRepo(uow.session, job.account_id).by_external_id(post_id)
        if source is None:
            return False
        await MaterialSignalsRepo(uow.session, job.account_id).add(
            source_id=source.id,
            origin="x_api",
            post_age_hours=age_hours,
            views=m.impressions,
            likes=m.likes,
            replies=m.replies,
            reposts=m.reposts,
            quotes=m.quotes,
            bookmarks=m.bookmarks,
            raw={"recheck": True},
        )
    return True
