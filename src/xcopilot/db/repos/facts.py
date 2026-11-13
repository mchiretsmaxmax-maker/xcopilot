"""Проверяемые факты."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_

from xcopilot.db.models import Fact
from xcopilot.db.repos.base import TenantRepo


class FactsRepo(TenantRepo[Fact]):
    model = Fact

    async def list_valid(self, now: datetime) -> list[Fact]:
        """Факты своего аккаунта, у которых срок годности не истёк."""
        stmt = (
            self.scoped()
            .where(or_(Fact.expires_at.is_(None), Fact.expires_at > now))
            .order_by(Fact.id)
        )
        return list((await self.session.execute(stmt)).scalars().all())
