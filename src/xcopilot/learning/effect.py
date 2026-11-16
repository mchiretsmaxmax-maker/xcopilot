"""Замер эффекта знания: zero_edit_rate на 20 решениях до и после (ТЗ 13, задача 8)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from xcopilot.brain.context import BrainContext
from xcopilot.db.repos.learning import LearningRepo

WINDOW = 20


@dataclass(frozen=True)
class Effect:
    before_rate: float | None
    after_rate: float | None
    before_n: int
    after_n: int
    delta: float | None
    enough: bool  # обе стороны набрали окно WINDOW — вывод можно показывать


def _rate(actions: list[str]) -> float | None:
    return sum(a == "approve" for a in actions) / len(actions) if actions else None


async def item_effect(ctx: BrainContext, item_id: UUID) -> Effect:
    """Доля одобрений без правок на 20 решениях до и 20 после подтверждения знания."""
    repo = LearningRepo(ctx.session, ctx.account_id)
    at = await repo.confirmed_at(item_id)
    if at is None:
        return Effect(None, None, 0, 0, None, False)
    before = await repo.decisions_before(at, WINDOW)
    after = await repo.decisions_after(at, WINDOW)
    b, a = _rate(before), _rate(after)
    delta = None if a is None or b is None else a - b
    return Effect(b, a, len(before), len(after), delta, len(before) == len(after) == WINDOW)
