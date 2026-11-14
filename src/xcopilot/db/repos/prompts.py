"""Версии промптов (глобальные, без account_id). Старые версии не меняются."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from xcopilot.db.models import Prompt


class PromptsRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, prompt_id: UUID) -> Prompt | None:
        """Версия промпта по id или None."""
        return await self.session.get(Prompt, prompt_id)

    async def find(self, key: str, content_hash: str) -> Prompt | None:
        """Версия промпта с таким содержимым (самая ранняя, если их несколько)."""
        stmt = (
            select(Prompt)
            .where(Prompt.key == key, Prompt.content_hash == content_hash)
            .order_by(Prompt.version)
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def last_version(self, key: str) -> int:
        """Номер последней версии ключа; 0, если версий нет."""
        stmt = select(func.coalesce(func.max(Prompt.version), 0)).where(Prompt.key == key)
        return int((await self.session.execute(stmt)).scalar_one())

    async def add(
        self,
        *,
        key: str,
        version: int,
        role: str,
        template: str,
        content_hash: str,
        output_schema: dict[str, Any] | None = None,
    ) -> Prompt:
        row = Prompt(
            key=key,
            version=version,
            role=role,
            template=template,
            content_hash=content_hash,
            output_schema=output_schema,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row
