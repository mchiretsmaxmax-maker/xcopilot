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

    # --- паспорт стиля (этап 8) ---------------------------------------------------------

    async def get_by_version(self, version: int) -> StylePassport | None:
        stmt = self.scoped().where(StylePassport.version == version)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_versions(self) -> list[StylePassport]:
        """Все версии аккаунта по возрастанию номера."""
        stmt = self.scoped().order_by(StylePassport.version)
        return list((await self.session.execute(stmt)).scalars().all())

    async def latest(self) -> StylePassport | None:
        stmt = self.scoped().order_by(StylePassport.version.desc()).limit(1)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def archived_below(self, version: int) -> StylePassport | None:
        """Архивная версия с наибольшим номером меньше `version` (цель отката)."""
        stmt = (
            self.scoped()
            .where(StylePassport.status == "archived", StylePassport.version < version)
            .order_by(StylePassport.version.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()
