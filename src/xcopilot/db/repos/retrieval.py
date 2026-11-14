"""SQL поиска по кускам (вектор, полнотекст) — заглушка до этапа кода."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class Hit:
    """Найденный кусок: текст, вид источника, метки и счёт (больше — лучше)."""

    chunk_id: UUID
    text: str
    kind: str
    source_id: UUID
    tags: tuple[str, ...]
    lang: str | None
    created_at: datetime
    score: float
    vec: tuple[float, ...] | None = None


@dataclass(frozen=True)
class HitFilters:
    """Фильтры поиска: метки (любая из), язык, интервал дат куска."""

    tags: tuple[str, ...] = ()
    lang: str | None = None
    since: datetime | None = None
    until: datetime | None = None


class RetrievalRepo:
    """Поиск по кускам одного аккаунта."""

    def __init__(self, session: AsyncSession, account_id: UUID) -> None:
        self.session = session
        self.account_id = account_id

    async def vector_hits(
        self,
        vec: Sequence[float],
        *,
        kinds: Sequence[str] | None,
        filters: HitFilters,
        limit: int,
    ) -> list[Hit]:
        raise NotImplementedError

    async def keyword_hits(
        self,
        query: str,
        *,
        kinds: Sequence[str] | None,
        filters: HitFilters,
        limit: int,
    ) -> list[Hit]:
        raise NotImplementedError

    async def explain_vector(
        self,
        vec: Sequence[float],
        *,
        kinds: Sequence[str] | None,
        filters: HitFilters,
        limit: int,
        force_index: bool,
    ) -> str:
        raise NotImplementedError

    async def chunks_by_ids(self, ids: Sequence[UUID]) -> list[Hit]:
        raise NotImplementedError
