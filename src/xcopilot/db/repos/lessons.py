"""Уроки из правок и отказов."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from xcopilot.db.models import Lesson
from xcopilot.db.repos.base import TenantRepo


class LessonsRepo(TenantRepo[Lesson]):
    model = Lesson

    async def list_by_status(self, status: str) -> list[Lesson]:
        """Уроки своего аккаунта с этим статусом, по возрастанию id."""
        stmt = self.scoped().where(Lesson.status == status).order_by(Lesson.id)
        return list((await self.session.execute(stmt)).scalars().all())

    async def decide(
        self,
        lesson_id: UUID,
        *,
        status: str,
        decided_by: UUID | None,
        decided_at: datetime,
    ) -> Lesson:
        """Принять решение по уроку."""
        return await self.update(
            lesson_id, status=status, decided_by=decided_by, decided_at=decided_at
        )
