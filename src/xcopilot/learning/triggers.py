"""Когда запускать обучение: 5 новых ревью или раз в неделю (ТЗ 13, задача 7)."""

from __future__ import annotations

from datetime import timedelta

from xcopilot.brain.context import BrainContext
from xcopilot.db.repos.learning import LearningRepo

MIN_REVIEWS = 5
WEEK_DAYS = 7


async def should_run(ctx: BrainContext) -> bool:
    """≥ 5 ревью после прошлого прохода; или есть хоть одно, а проходу (или ревью) больше недели."""
    repo = LearningRepo(ctx.session, ctx.account_id)
    since = await repo.last_run_at()
    rows = await repo.reviews_after(since)
    if len(rows) >= MIN_REVIEWS:
        return True
    if not rows:
        return False
    anchor = since or rows[0]["created_at"]
    return ctx.clock.now() - anchor >= timedelta(days=WEEK_DAYS)
