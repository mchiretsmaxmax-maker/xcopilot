"""Авторы материалов (ТЗ v3.1): нормировка хэндла, «я сам», блокировка."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert

from xcopilot.db.models import Author, KnowledgeEvidence
from xcopilot.db.repos.base import TenantRepo

_HANDLE = re.compile(r"^[a-z0-9_]{1,50}$")


def normalize_handle(handle: str) -> str:
    """Хэндл X без @ в нижнем регистре; ValueError, если это не хэндл."""
    norm = handle.strip().lstrip("@").lower()
    if not _HANDLE.match(norm):
        raise ValueError(f"не хэндл X: {handle!r}")
    return norm


class AuthorsRepo(TenantRepo[Author]):
    """Авторы аккаунта; хэндл уникален в аккаунте, «я сам» — один."""

    model = Author

    async def by_handle(self, handle: str) -> Author | None:
        stmt = self.scoped().where(Author.handle == normalize_handle(handle))
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_or_create(self, handle: str, **fields: Any) -> Author:
        """Автор по хэндлу; нет — создать с полями `fields` (у существующего они не меняются)."""
        norm = normalize_handle(handle)
        stmt = (
            insert(Author)
            .values(account_id=self.account_id, handle=norm, **fields)
            .on_conflict_do_nothing(index_elements=["account_id", "handle"])
            .returning(Author.id)
        )
        new_id = (await self.session.execute(stmt)).scalar_one_or_none()
        if new_id is not None:
            return await self.require(new_id)
        existing = await self.by_handle(norm)
        assert existing is not None
        return existing

    async def self_author(self) -> Author | None:
        stmt = self.scoped().where(Author.is_self.is_(True))
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def set_self(self, handle: str) -> Author:
        """Отметить автора с этим хэндлом как владельца аккаунта (прежняя отметка снимается)."""
        author = await self.get_or_create(handle)
        current = await self.self_author()
        if current is not None and current.id != author.id:
            await self.update(current.id, is_self=False)
        return await self.update(author.id, is_self=True)

    async def block(self, author_id: UUID, reason: str) -> Author:
        """Заблокировать автора: его материалы не учатся, доказательства отзываются."""
        author = await self.update(author_id, trust=-1, note=reason)
        await self.session.execute(
            update(KnowledgeEvidence)
            .where(
                KnowledgeEvidence.account_id == self.account_id,
                KnowledgeEvidence.author_id == author_id,
                KnowledgeEvidence.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC), revoked_reason="author_blocked")
            .execution_options(synchronize_session=False)
        )
        return author
