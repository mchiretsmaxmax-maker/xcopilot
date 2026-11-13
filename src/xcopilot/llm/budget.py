"""Бюджет расходов: проверка до вызова, учёт после (деньги — int микродоллары)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from uuid import UUID

from xcopilot.clock import Clock
from xcopilot.db.uow import UnitOfWork


class BudgetGuard:
    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        *,
        default_day_limits: Mapping[str, int],
        warn_ratio: float = 0.8,
    ) -> None:
        raise NotImplementedError

    async def check(self, account_id: UUID, scope: str, estimate: int) -> None:
        raise NotImplementedError

    async def record(self, account_id: UUID, scope: str, actual: int) -> None:
        raise NotImplementedError
