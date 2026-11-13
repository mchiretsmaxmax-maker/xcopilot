"""Паспорта стиля (версии, один активный)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select

from xcopilot.db.models import StylePassport
from xcopilot.db.repos.base import TenantRepo


class PassportsRepo(TenantRepo[StylePassport]):
    model = StylePassport

    async def get_active(self) -> StylePassport | None:
        """Активный паспорт своего аккаунта."""
        stmt = self.scoped().where(StylePassport.status == "active")
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def next_version(self) -> int:
        """Следующий номер версии паспорта своего аккаунта."""
        stmt = select(func.max(StylePassport.version)).where(
            StylePassport.account_id == self.account_id
        )
        current = (await self.session.execute(stmt)).scalar_one_or_none()
        return 1 if current is None else current + 1

    async def activate(self, passport_id: UUID) -> StylePassport:
        """Сделать паспорт активным, архивируя предыдущий активный."""
        await self.require(passport_id)
        active = await self.get_active()
        if active is not None and active.id != passport_id:
            await self.update(active.id, status="archived")
            await self.session.flush()
        return await self.update(passport_id, status="active")
