"""Откат базы знаний на дату и отмена отката (ТЗ v3.1, раздел 2.6)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from xcopilot.brain.context import BrainContext


@dataclass(frozen=True)
class RollbackPlan:
    account_id: UUID
    to_ts: datetime
    created_items: list[UUID] = field(default_factory=list)
    changed_items: list[UUID] = field(default_factory=list)
    evidence_added: int = 0


async def plan(ctx: BrainContext, to_ts: datetime) -> RollbackPlan:
    """Предпросмотр: что создано, изменено и добавлено после to_ts."""
    raise NotImplementedError


async def execute(ctx: BrainContext, rollback: RollbackPlan, user_id: UUID | None) -> UUID:
    """Откат одной транзакцией; вернуть id отката (причина отзыва — rollback:<id>)."""
    raise NotImplementedError


async def undo(ctx: BrainContext, rollback_id: UUID, user_id: UUID | None) -> None:
    """Отменить откат: вернуть доказательства, отменить переходы, восстановить тексты."""
    raise NotImplementedError
