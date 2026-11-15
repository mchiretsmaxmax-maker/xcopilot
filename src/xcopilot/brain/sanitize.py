"""Проверка кандидата судьёй (ТЗ v3.1, 9A, задача 5): ремесло, переносимость, слой."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from xcopilot.brain.context import BrainContext
from xcopilot.brain.schemas import KnowledgeCandidate, Layer


@dataclass(frozen=True)
class SanitizeVerdict:
    ok: bool
    layer: Layer
    reason: str | None = None
    scores: dict[str, float] | None = None


async def check(
    ctx: BrainContext, candidate: KnowledgeCandidate, stances: Sequence[str] = ()
) -> SanitizeVerdict:
    """Вопросы судье о кандидате; слой судьи с уверенностью ≥ 0.7 заменяет слой извлекателя."""
    raise NotImplementedError
