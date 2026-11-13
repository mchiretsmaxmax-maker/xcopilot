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
