"""Пересчёт весов (ТЗ v3.1, 9A, задача 8): обёртка над SQL knowledge_recompute_scores.

Формулы раздела 2.4 живут в функции БД (миграции 0004, 0007): априор — из колонок знания,
спад по слою, потолок для чужих доказательств. Здесь — только вызов с настройками.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from xcopilot.brain.context import BrainContext
from xcopilot.db.repos.knowledge import KnowledgeItemsRepo


async def recompute(
    ctx: BrainContext, item_id: UUID | None = None, now: datetime | None = None
) -> int:
    """Пересчитать веса знаний аккаунта (или одного) на момент now; вернуть число строк."""
    return await KnowledgeItemsRepo(ctx.session, ctx.account_id).recompute(
        item_id,
        now=now or ctx.clock.now(),
        foreign_cap=ctx.config.thresholds.foreign_cap,
    )
