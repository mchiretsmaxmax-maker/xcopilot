"""Алерты владельцу (этап 15): заглушка до кода."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from xcopilot.clock import Clock
from xcopilot.db.uow import UnitOfWork
from xcopilot.llm.budget import BudgetGuard

ALERTS_KIND = "ops.alerts"


@dataclass(frozen=True, slots=True)
class Alert:
    code: str
    text: str
    key: str


class OwnerNotifier(Protocol):
    async def notify_owner(self, account_id: UUID, text: str) -> None: ...


@dataclass
class AlertDeps:
    uow_factory: Callable[[], UnitOfWork]
    clock: Clock
    budget: BudgetGuard | None = None
    open_since: Callable[[], Mapping[str, datetime | None]] | None = None


async def check_alerts(deps: AlertDeps, account_id: UUID) -> list[Alert]:
    raise NotImplementedError


async def send_alerts(deps: AlertDeps, account_id: UUID, notifier: OwnerNotifier) -> list[Alert]:
    raise NotImplementedError
