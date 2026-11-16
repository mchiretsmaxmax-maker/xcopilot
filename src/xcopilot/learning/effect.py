"""Замер эффекта знания: zero_edit_rate на 20 решениях до и после (ТЗ 13, задача 8)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from xcopilot.brain.context import BrainContext

WINDOW = 20


@dataclass(frozen=True)
class Effect:
    before_rate: float | None
    after_rate: float | None
    before_n: int
    after_n: int
    delta: float | None
    enough: bool


async def item_effect(ctx: BrainContext, item_id: UUID) -> Effect:
    raise NotImplementedError
