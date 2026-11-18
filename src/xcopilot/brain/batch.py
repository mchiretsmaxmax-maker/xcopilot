"""Сводка пачки материалов вместо карточки на каждый (feat/auto-outcome, ADR 0033)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol
from uuid import UUID

from xcopilot.db.uow import UnitOfWork

SUMMARY_KIND = "brain.batch_summary"


class Notifier(Protocol):
    async def notify(self, account_id: UUID, text: str, keyboard: Any = None) -> int: ...


async def send_summary(
    uow_factory: Callable[[], UnitOfWork], notifier: Notifier, account_id: UUID, batch: str
) -> bool:
    """Сводка пачки (в том числе по таймауту); уже отправлена — False."""
    raise NotImplementedError
