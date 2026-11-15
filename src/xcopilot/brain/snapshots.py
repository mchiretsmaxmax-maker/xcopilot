"""Ежедневные снимки состояния знаний (ТЗ v3.1, 9A, задача 10)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from xcopilot.brain.context import BrainContext


@dataclass(frozen=True)
class ItemSnapshot:
    snapshot_date: date
    item_id: UUID
    status: str
    owner_state: str
    version: int
    quality: float
    eff_conf: float
    fit_conf: float


async def take(ctx: BrainContext, day: date) -> int:
    """Снять снимок на день один раз; повтор того же дня — 0."""
    raise NotImplementedError


async def state_on(ctx: BrainContext, day: date) -> list[ItemSnapshot]:
    """Состояние на конец дня по последнему снимку не позже day."""
    raise NotImplementedError
