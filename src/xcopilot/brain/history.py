"""Импорт своей истории при онбординге (ТЗ v3.1, раздел 1.3 п. 5; этап 7A)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from xcopilot.clock import Clock
from xcopilot.db.uow import UnitOfWork
from xcopilot.x.client import XClient

HISTORY_KIND = "brain.history_import"


class BudgetLike(Protocol):
    async def check(self, account_id: UUID, scope: str, estimate: int) -> None: ...


@dataclass(frozen=True)
class ImportResult:
    imported: int
    skipped: int
    stopped_by_budget: bool
    text: str


class HistoryImporter:
    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        x_for: Callable[[UUID], Awaitable[XClient]],
        budget: BudgetLike,
        clock: Clock,
    ) -> None:
        self._uow = uow_factory
        self._x_for = x_for
        self._budget = budget
        self._clock = clock

    async def import_own_history(self, account_id: UUID, limit: int = 500) -> ImportResult:
        raise NotImplementedError
