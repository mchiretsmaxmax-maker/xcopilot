"""Черновики, их ревизии и статусы."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from xcopilot.db.models import Draft
from xcopilot.db.models.content import DraftStatus, assert_transition
from xcopilot.db.repos.base import TenantRepo


class DraftsRepo(TenantRepo[Draft]):
    model = Draft

    async def list_by_run(self, run_id: UUID) -> list[Draft]:
        """Черновики своего аккаунта этого запуска в порядке variant_no, revision, id."""
        stmt = (
            self.scoped()
            .where(self.col("run_id") == run_id)
            .order_by(self.col("variant_no"), self.col("revision"), self.col("id"))
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def add_revision(
        self, parent_id: UUID, *, text: str, parts: list[Any] | None = None
    ) -> Draft:
        """Создать следующую ревизию черновика."""
        parent = await self.require(parent_id)
        return await self.add(
            run_id=parent.run_id,
            variant_no=parent.variant_no,
            revision=parent.revision + 1,
            parent_draft_id=parent.id,
            format=parent.format,
            reply_to_url=parent.reply_to_url,
            text=text,
            parts=parts if parts is not None else [],
        )

    async def set_status(self, draft_id: UUID, new: DraftStatus) -> Draft:
        """Сменить статус черновика с проверкой допустимости перехода."""
        draft = await self.require(draft_id)
        assert_transition(DraftStatus(draft.status), new)
        return await self.update(draft_id, status=new.value)
