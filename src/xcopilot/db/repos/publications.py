"""Публикации черновиков в X."""

from __future__ import annotations

from uuid import UUID

from xcopilot.db.models import Publication
from xcopilot.db.repos.base import TenantRepo


class PublicationsRepo(TenantRepo[Publication]):
    model = Publication

    async def get_by_x_post_id(self, x_post_id: str) -> Publication | None:
        """Публикация своего аккаунта по ID поста X."""
        stmt = self.scoped().where(self.col("x_post_id") == x_post_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_draft(self, draft_id: UUID) -> Publication | None:
        """Публикация своего аккаунта по черновику."""
        stmt = self.scoped().where(self.col("draft_id") == draft_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()
