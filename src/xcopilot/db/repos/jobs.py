"""Очередь задач: SQL для xcopilot.jobs.queue. Репозиторий глобальный — воркер берёт задачи всех
аккаунтов, но каждая задача принадлежит аккаунту (account_id обязателен при постановке)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import case, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from xcopilot.db.models import Job
from xcopilot.errors import NotFound


class JobsRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def enqueue(
        self,
        *,
        account_id: UUID,
        kind: str,
        payload: dict[str, Any],
        run_at: datetime,
        max_attempts: int,
        idempotency_key: str | None,
    ) -> UUID:
        """Поставить задачу; при повторе idempotency_key вернуть id существующей."""
        stmt = (
            insert(Job)
            .values(
                account_id=account_id,
                kind=kind,
                payload=payload,
                run_at=run_at,
                max_attempts=max_attempts,
                idempotency_key=idempotency_key,
            )
            .on_conflict_do_nothing(index_elements=["account_id", "idempotency_key"])
            .returning(Job.id)
        )
        new_id = (await self.session.execute(stmt)).scalar_one_or_none()
        if new_id is not None:
            return new_id
        existing = select(Job.id).where(
            Job.account_id == account_id, Job.idempotency_key == idempotency_key
        )
        return (await self.session.execute(existing)).scalar_one()

    async def get(self, job_id: UUID) -> Job | None:
        return await self.session.get(Job, job_id, populate_existing=True)

    async def claim(self, worker_id: str, limit: int, now: datetime) -> list[Job]:
        """Взять до limit готовых задач; занятые другими воркерами пропускаются (SKIP LOCKED)."""
        ready = (
            select(Job.id)
            .where(Job.status == "queued", Job.run_at <= now)
            .order_by(Job.run_at, Job.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        stmt = (
            update(Job)
            .where(Job.id.in_(ready.scalar_subquery()))
            .values(
                status="running",
                attempts=Job.attempts + 1,
                locked_by=worker_id,
                locked_at=now,
            )
            .returning(Job)
            .execution_options(synchronize_session=False, populate_existing=True)
        )
        jobs = list((await self.session.execute(stmt)).scalars().all())
        return sorted(jobs, key=lambda j: (j.run_at, j.id))

    async def complete(self, job_id: UUID, now: datetime) -> None:
        await self._set(job_id, status="done", finished_at=now, locked_by=None, locked_at=None)

    async def fail(self, job_id: UUID, error: str, now: datetime, delay: timedelta | None) -> None:
        """Неудача: delay=None — задача мертва, иначе снова в очередь через delay."""
        if delay is None:
            values: dict[str, Any] = {"status": "dead", "finished_at": now}
        else:
            values = {"status": "queued", "run_at": now + delay}
        await self._set(job_id, last_error=error, locked_by=None, locked_at=None, **values)

    async def release_stale(self, cutoff: datetime) -> int:
        """Вернуть в очередь задачи, взятые раньше cutoff; исчерпавшие попытки — dead."""
        stmt = (
            update(Job)
            .where(Job.status == "running", Job.locked_at < cutoff)
            .values(
                status=case((Job.attempts >= Job.max_attempts, "dead"), else_="queued"),
                locked_by=None,
                locked_at=None,
                last_error="блокировка просрочена",
            )
            .returning(Job.id)
            .execution_options(synchronize_session=False)
        )
        return len((await self.session.execute(stmt)).scalars().all())

    async def _set(self, job_id: UUID, **values: Any) -> None:
        stmt = (
            update(Job)
            .where(Job.id == job_id)
            .values(**values)
            .returning(Job.id)
            .execution_options(synchronize_session=False)
        )
        if (await self.session.execute(stmt)).scalar_one_or_none() is None:
            raise NotFound(f"jobs: {job_id} не найдена")
