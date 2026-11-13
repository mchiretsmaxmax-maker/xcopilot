"""Состояния диалогов бота."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert

from xcopilot.db.models import BotSession
from xcopilot.db.repos.base import TenantRepo


class BotSessionsRepo(TenantRepo[BotSession]):
    model = BotSession

    async def get_for_user(self, user_id: UUID) -> BotSession | None:
        stmt = self.scoped().where(BotSession.user_id == user_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def save(self, user_id: UUID, state: str | None, data: dict[str, Any]) -> None:
        """Записать состояние и данные диалога (создать или заменить)."""
        stmt = (
            insert(BotSession)
            .values(account_id=self.account_id, user_id=user_id, state=state, data=data)
            .on_conflict_do_update(
                index_elements=["account_id", "user_id"],
                set_={"state": state, "data": data, "updated_at": func.now()},
            )
        )
        await self.session.execute(stmt)
