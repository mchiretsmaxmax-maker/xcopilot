"""Планировщик по таблице schedules (заглушка red-фазы)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time
from uuid import UUID

from xcopilot.clock import Clock
from xcopilot.db.uow import UnitOfWork


@dataclass(frozen=True, slots=True)
class DefaultSchedule:
    kind: str
    at_local_time: time
    weekday: int | None = None


DEFAULT_SCHEDULES: tuple[DefaultSchedule, ...] = ()


def due_at(
    now: datetime, tz: str, at_local_time: time, weekday: int | None, last_run_at: datetime | None
) -> datetime | None:
    raise NotImplementedError


class Scheduler:
    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        *,
        interval: float = 60.0,
        max_attempts: int = 5,
    ) -> None:
        self._uow = uow_factory
        self._clock = clock

    async def tick(self) -> list[UUID]:
        raise NotImplementedError

    async def run(self, stop: asyncio.Event) -> None:
        raise NotImplementedError
