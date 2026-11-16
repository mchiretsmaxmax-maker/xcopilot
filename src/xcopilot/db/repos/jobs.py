"""Очередь задач: SQL для xcopilot.jobs.queue. Репозиторий глобальный — воркер берёт задачи всех
аккаунтов, но каждая задача принадлежит аккаунту (account_id обязателен при постановке)."""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import case, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from xcopilot.db.models import Job
from xcopilot.errors import NotFound

ACTIVE = ("queued", "running")


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

    async def claim(
        self,
        worker_id: str,
        limit: int,
        now: datetime,
        *,
        kinds: Collection[str] | None = None,
        exclude: bool = False,
    ) -> list[Job]:
        """Взять до limit готовых задач; занятые другими воркерами пропускаются (SKIP LOCKED).

        kinds — только эти виды (exclude=True — все, кроме них); полосы воркера, ADR 0029.
        """
        ready = select(Job.id).where(Job.status == "queued", Job.run_at <= now)
        if kinds is not None:
            listed = sorted(kinds)
            ready = ready.where(Job.kind.notin_(listed) if exclude else Job.kind.in_(listed))
        ready = ready.order_by(Job.run_at, Job.id).limit(limit).with_for_update(skip_locked=True)
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

    async def release(self, job_id: UUID, now: datetime, reason: str) -> bool:
        """Вернуть взятую задачу в очередь сразу (остановка процесса): попытка не засчитывается."""
        stmt = (
            update(Job)
            .where(Job.id == job_id, Job.status == "running")
            .values(
                status="queued",
                attempts=case((Job.attempts > 0, Job.attempts - 1), else_=0),
                run_at=now,
                locked_by=None,
                locked_at=None,
                last_error=reason,
            )
            .returning(Job.id)
            .execution_options(synchronize_session=False)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none() is not None

    async def release_host(self, host_prefix: str, now: datetime, reason: str) -> int:
        """Вернуть в очередь задачи, взятые воркерами этого хоста (прошлая жизнь процесса).

        Попытка засчитывается: задача, которая роняет процесс, не крутится вечно; исчерпавшие
        попытки — dead.
        """
        stmt = (
            update(Job)
            .where(Job.status == "running", Job.locked_by.startswith(host_prefix, autoescape=True))
            .values(
                status=case((Job.attempts >= Job.max_attempts, "dead"), else_="queued"),
                run_at=now,
                locked_by=None,
                locked_at=None,
                last_error=reason,
            )
            .returning(Job.id)
            .execution_options(synchronize_session=False)
        )
        return len((await self.session.execute(stmt)).scalars().all())

    async def cancel(
        self, job_id: UUID, now: datetime, account_id: UUID | None = None
    ) -> tuple[str | None, str | None]:
        """Отменить задачу аккаунта; вернуть (статус после, статус до) или (None, None).

        queued и running → cancelled; остальные статусы не меняются (done, dead, cancelled).
        """
        stmt = select(Job.status, Job.account_id).where(Job.id == job_id).with_for_update()
        row = (await self.session.execute(stmt)).one_or_none()
        if row is None or (account_id is not None and row.account_id != account_id):
            return None, None
        before: str = row.status
        if before not in ACTIVE:
            return before, before
        await self._cancel_ids([job_id], now)
        return "cancelled", before

    async def cancel_active(
        self,
        account_id: UUID,
        now: datetime,
        *,
        progress_key: str | None = None,
        kinds: Collection[str] | None = None,
    ) -> list[tuple[UUID, str, str]]:
        """Отменить все queued/running задачи аккаунта (по ключу прогресса и видам, если даны).

        Ключ `source:<id>` относится и к задачам разбора материала (payload.source_id).
        Вернуть (id, kind, статус до).
        """
        stmt = select(Job.id, Job.kind, Job.status).where(
            Job.account_id == account_id, Job.status.in_(ACTIVE)
        )
        if progress_key is not None:
            conds = [Job.payload["progress"].astext == progress_key]
            if progress_key.startswith("source:"):
                conds.append(
                    Job.payload["source_id"].astext == progress_key.removeprefix("source:")
                )
            stmt = stmt.where(or_(*conds))
        if kinds is not None:
            stmt = stmt.where(Job.kind.in_(list(kinds)))
        rows = (
            await self.session.execute(stmt.order_by(Job.created_at, Job.id).with_for_update())
        ).all()
        await self._cancel_ids([r.id for r in rows], now)
        return [(r.id, r.kind, r.status) for r in rows]

    async def _cancel_ids(self, ids: list[UUID], now: datetime) -> None:
        if not ids:
            return
        stmt = (
            update(Job)
            .where(Job.id.in_(ids), Job.status.in_(ACTIVE))
            .values(status="cancelled", finished_at=now, locked_by=None, locked_at=None)
            .execution_options(synchronize_session=False)
        )
        await self.session.execute(stmt)

    async def _set(self, job_id: UUID, **values: Any) -> None:
        """Обновить задачу; отменённую не трогает (результат отменённой задачи выбрасывается)."""
        stmt = (
            update(Job)
            .where(Job.id == job_id, Job.status != "cancelled")
            .values(**values)
            .returning(Job.id)
            .execution_options(synchronize_session=False)
        )
        if (await self.session.execute(stmt)).scalar_one_or_none() is None:
            exists = select(Job.id).where(Job.id == job_id)
            if (await self.session.execute(exists)).scalar_one_or_none() is None:
                raise NotFound(f"jobs: {job_id} не найдена")
