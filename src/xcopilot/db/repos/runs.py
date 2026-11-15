"""Запуски генерации."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from xcopilot.db.models import GenerationRun
from xcopilot.db.repos.base import TenantRepo


class RunsRepo(TenantRepo[GenerationRun]):
    model = GenerationRun

    async def finish(
        self,
        run_id: UUID,
        *,
        status: str,
        finished_at: datetime,
        error: str | None = None,
    ) -> GenerationRun:
        """Завершить запуск со статусом, временем и ошибкой."""
        return await self.update(run_id, status=status, finished_at=finished_at, error=error)

    async def latest(self) -> GenerationRun | None:
        """Последний запуск своего аккаунта (по id: UUIDv7 растёт со временем)."""
        stmt = self.scoped().order_by(self.col("id").desc()).limit(1)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def by_job(self, job_id: UUID) -> GenerationRun | None:
        """Последний запуск задачи очереди (`context_pack.job_id`) — для идемпотентности."""
        stmt = (
            self.scoped()
            .where(GenerationRun.context_pack["job_id"].astext == str(job_id))
            .order_by(self.col("id").desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()
