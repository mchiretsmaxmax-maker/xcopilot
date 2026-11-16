"""Когда запускать обучение: 5 новых ревью или раз в неделю (ТЗ 13, задача 7)."""

from __future__ import annotations

from xcopilot.brain.context import BrainContext

MIN_REVIEWS = 5
WEEK_DAYS = 7


async def should_run(ctx: BrainContext) -> bool:
    raise NotImplementedError
