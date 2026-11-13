"""Решения владельца по черновикам."""

from __future__ import annotations

from uuid import UUID

from xcopilot.db.models import Review
from xcopilot.db.repos.base import TenantRepo


class ReviewsRepo(TenantRepo[Review]):
    model = Review

    async def list_by_draft(self, draft_id: UUID) -> list[Review]:
        """Ревью своего аккаунта по черновику в порядке возрастания id."""
        stmt = self.scoped().where(self.col("draft_id") == draft_id).order_by(self.col("id"))
        return list((await self.session.execute(stmt)).scalars().all())
