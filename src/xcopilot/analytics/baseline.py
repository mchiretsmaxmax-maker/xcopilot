"""Базовый уровень: медиана показов своих постов формата за последние дни (этап 14, задача 5).

Для каждого поста берётся последний снимок метрик: чем старше пост, тем он зрелее.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median
from uuid import UUID

from xcopilot.db.repos import AnalyticsRepo
from xcopilot.db.uow import DbSession


@dataclass(frozen=True)
class Baseline:
    fmt: str
    days: int
    median_impressions: float | None
    sample_size: int


async def baseline(
    session: DbSession, account_id: UUID, fmt: str, days: int = 30, *, now: datetime
) -> Baseline:
    rows = await AnalyticsRepo(session, account_id).own_posts_with_views(
        now - timedelta(days=days), now, fmt
    )
    views = [float(r["impressions"]) for r in rows]
    return Baseline(fmt, days, median(views) if views else None, len(views))
