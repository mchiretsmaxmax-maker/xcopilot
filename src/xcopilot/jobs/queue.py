"""Очередь задач в PostgreSQL (FOR UPDATE SKIP LOCKED)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from xcopilot.clock import Clock
from xcopilot.db.uow import UnitOfWork


@dataclass(frozen=True, slots=True)
class Job:
    """Снимок задачи."""

    id: UUID
    account_id: UUID
    kind: str
    payload: dict[str, Any]
    status: str
    attempts: int
    max_attempts: int
    run_at: datetime
    locked_by: str | None
    last_error: str | None


class JobQueue:
    """enqueue / claim / complete / fail / release_stale."""

    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        *,
        max_attempts: int = 5,
        base_delay: timedelta = timedelta(seconds=30),
        max_delay: timedelta = timedelta(hours=1),
    ) -> None:
        raise NotImplementedError

    async def enqueue(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        account_id: UUID,
        run_at: datetime | None = None,
        idempotency_key: str | None = None,
    ) -> UUID:
        raise NotImplementedError

    async def claim(self, worker_id: str, limit: int) -> list[Job]:
        raise NotImplementedError

    async def complete(self, job_id: UUID) -> None:
        raise NotImplementedError

    async def fail(self, job_id: UUID, error: str) -> None:
        raise NotImplementedError

    async def release_stale(self, older_than: timedelta) -> int:
        raise NotImplementedError

    async def get(self, job_id: UUID) -> Job | None:
        raise NotImplementedError
