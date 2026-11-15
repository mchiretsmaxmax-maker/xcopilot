"""Доказательства знаний (ТЗ v3.1, 9A, задачи 3 и 11): наличие × исход → строка с весами."""

from __future__ import annotations

from uuid import UUID

from xcopilot.brain.context import BrainContext


async def record_from_material(ctx: BrainContext, source_id: UUID) -> int:
    """Детекторы на материале → доказательства eff; вернуть число новых строк."""
    raise NotImplementedError


async def record_own_outcome(ctx: BrainContext, source_id: UUID) -> int:
    """Исход своего поста → доказательства own_outcome (вес 2.0, без скидки за автора)."""
    raise NotImplementedError


async def record_from_review(ctx: BrainContext, review_id: UUID) -> int:
    """Решение владельца по черновику → доказательства fit применённых знаний."""
    raise NotImplementedError


async def revoke(
    ctx: BrainContext,
    *,
    source_id: UUID | None = None,
    author_id: UUID | None = None,
    reason: str,
) -> int:
    """Отозвать доказательства материала или автора и пересчитать веса; вернуть число."""
    raise NotImplementedError
