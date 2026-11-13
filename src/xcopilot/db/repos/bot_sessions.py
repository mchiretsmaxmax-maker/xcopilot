"""Состояния диалогов бота."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from xcopilot.db.models import BotSession
from xcopilot.db.repos.base import TenantRepo


class BotSessionsRepo(TenantRepo[BotSession]):
    model = BotSession

    async def get_for_user(self, user_id: UUID) -> BotSession | None:
        raise NotImplementedError

    async def save(self, user_id: UUID, state: str | None, data: dict[str, Any]) -> None:
        raise NotImplementedError
