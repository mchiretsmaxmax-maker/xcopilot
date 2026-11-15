"""Пересчёт весов (ТЗ v3.1, 9A, задача 8): обёртка над SQL knowledge_recompute_scores."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from xcopilot.brain.context import BrainContext


async def recompute(
    ctx: BrainContext, item_id: UUID | None = None, now: datetime | None = None
) -> int:
    """Пересчитать веса знаний аккаунта (или одного) на момент now; вернуть число строк."""
    raise NotImplementedError
