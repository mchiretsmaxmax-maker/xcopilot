"""Ежедневные снимки состояния знаний (ТЗ v3.1, 9A, задача 10).

Снимок дня снимается один раз; повтор того же дня ничего не меняет (состояние дня — первое
снятое, ночной проход идёт после пересчёта и переходов). Состояние на дату — последний снимок не
позже даты; внутри дня точность даёт журнал переходов.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from xcopilot.brain.context import BrainContext
from xcopilot.db.repos.brain import BrainRepo
from xcopilot.db.repos.knowledge import KnowledgeSnapshotsRepo


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
    if await BrainRepo(ctx.session, ctx.account_id).has_snapshot(day):
        return 0
    return await KnowledgeSnapshotsRepo(ctx.session, ctx.account_id).take(day)


async def state_on(ctx: BrainContext, day: date) -> list[ItemSnapshot]:
    """Состояние на конец дня по последнему снимку не позже day."""
    rows = await KnowledgeSnapshotsRepo(ctx.session, ctx.account_id).state_on(day)
    return [
        ItemSnapshot(
            snapshot_date=r.snapshot_date,
            item_id=r.item_id,
            status=r.status,
            owner_state=r.owner_state,
            version=r.version,
            quality=float(r.quality),
            eff_conf=float(r.eff_conf),
            fit_conf=float(r.fit_conf),
        )
        for r in rows
    ]
