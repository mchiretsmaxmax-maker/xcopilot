"""Очередь задач в PostgreSQL (FOR UPDATE SKIP LOCKED).

Каждый вызов — своя транзакция (UnitOfWork). Время берётся из Clock, поэтому паузы
и run_at управляемы в тестах. Неудача: пауза base_delay · 2^(попытка−1), не больше
max_delay; после max_attempts — статус dead. Задачи умершего воркера возвращает release_stale.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from xcopilot.clock import Clock
from xcopilot.db.repos.jobs import JobsRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.metrics.events import EventType, emit


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

    @classmethod
    def from_row(cls, row: Any) -> Job:
        return cls(**{name: getattr(row, name) for name in cls.__slots__})


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
        if max_attempts < 1:
            raise ValueError("max_attempts должен быть ≥ 1")
        self._uow = uow_factory
        self._clock = clock
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay

    def backoff(self, attempts: int) -> timedelta:
        """Пауза после неудачной попытки номер attempts (1, 2, …)."""
        factor: int = 2 ** max(attempts - 1, 0)
        return min(self.base_delay * factor, self.max_delay)

    async def enqueue(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        account_id: UUID,
        run_at: datetime | None = None,
        idempotency_key: str | None = None,
    ) -> UUID:
        async with self._uow() as uow:
            return await JobsRepo(uow.session).enqueue(
                account_id=account_id,
                kind=kind,
                payload=payload,
                run_at=run_at or self._clock.now(),
                max_attempts=self.max_attempts,
                idempotency_key=idempotency_key,
            )

    async def claim(self, worker_id: str, limit: int) -> list[Job]:
        async with self._uow() as uow:
            rows = await JobsRepo(uow.session).claim(worker_id, limit, self._clock.now())
            return [Job.from_row(r) for r in rows]

    async def complete(self, job_id: UUID) -> None:
        async with self._uow() as uow:
            await JobsRepo(uow.session).complete(job_id, self._clock.now())

    async def fail(self, job_id: UUID, error: str, *, retry: bool = True) -> str:
        """Неудача попытки; вернуть новый статус: queued (повтор после паузы) или dead.

        retry=False — повтор бесполезен (неизвестный вид, неверная задача): сразу dead.
        Умершая задача пишет событие job.dead в той же транзакции.
        """
        async with self._uow() as uow:
            repo = JobsRepo(uow.session)
            row = await repo.get(job_id)
            if row is None:
                await repo.complete(job_id, self._clock.now())  # NotFound
                return "dead"
            dead = not retry or row.attempts >= row.max_attempts
            delay = None if dead else self.backoff(row.attempts)
            await repo.fail(job_id, error[:2000], self._clock.now(), delay)
            if dead:
                await emit(
                    uow.session,
                    account_id=row.account_id,
                    type=EventType.JOB_DEAD,
                    entity=("job", job_id),
                    props={"kind": row.kind, "error": error[:500]},
                )
            return "dead" if dead else "queued"

    async def release(self, job_id: UUID, reason: str = "остановка процесса") -> bool:
        """Вернуть взятую задачу в очередь сразу, не засчитывая попытку (корректная остановка)."""
        async with self._uow() as uow:
            return await JobsRepo(uow.session).release(job_id, self._clock.now(), reason)

    async def release_host(self, host_prefix: str, reason: str = "процесс перезапущен") -> int:
        """Вернуть задачи, которые держали воркеры этого хоста до рестарта."""
        async with self._uow() as uow:
            return await JobsRepo(uow.session).release_host(host_prefix, self._clock.now(), reason)

    async def release_stale(self, older_than: timedelta) -> int:
        async with self._uow() as uow:
            return await JobsRepo(uow.session).release_stale(self._clock.now() - older_than)

    async def get(self, job_id: UUID) -> Job | None:
        async with self._uow() as uow:
            row = await JobsRepo(uow.session).get(job_id)
            return None if row is None else Job.from_row(row)
