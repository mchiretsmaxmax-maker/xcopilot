"""Темы постов."""

from __future__ import annotations

from uuid import UUID

from xcopilot.db.models import Topic
from xcopilot.db.repos.base import TenantRepo


class TopicsRepo(TenantRepo[Topic]):
    model = Topic

    async def list_by_status(self, status: str) -> list[Topic]:
        """Темы своего аккаунта с этим статусом, по возрастанию id."""
        stmt = self.scoped().where(Topic.status == status).order_by(Topic.id)
        return list((await self.session.execute(stmt)).scalars().all())

    async def set_status(self, topic_id: UUID, status: str) -> Topic:
        """Изменить статус темы."""
        return await self.update(topic_id, status=status)
