"""Сегменты и чтение базы для бота (ТЗ v3.1, 9C): стартовые сегменты, списки, карточка,
доказательства, поиск, счётчики. Только чтение (и копирование сегментов при онбординге)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

PAGE_SIZE = 5


@dataclass(frozen=True)
class SegmentItem:
    id: UUID
    statement: str
    guidance: str
    kind: str
    layer: str
    element: str
    status: str
    owner_state: str
    risk_class: str
    quality: float


@dataclass(frozen=True)
class Page:
    items: list[SegmentItem]
    page: int
    pages: int
    total: int


@dataclass(frozen=True)
class SegmentCount:
    key: str
    title: str
    count: int


@dataclass(frozen=True)
class BrainCounts:
    materials: int
    items_total: int
    by_status: dict[str, int]
    segments: list[SegmentCount]
    conflicts: int
    class_a_pending: int


@dataclass(frozen=True)
class ItemView:
    item: SegmentItem
    version: int
    eff_conf: float
    fit_conf: float
    confidence_word: str
    fit_known: bool
    evidence_n: int
    posts: int
    authors: int
    own_support: int
    own_contra: int
    scopes: dict[str, list[str]] = field(default_factory=dict)
    origin: str = "materials"
    target_metric: str = "engagement"


@dataclass(frozen=True)
class EvidenceRow:
    id: UUID
    source_id: UUID | None
    author_handle: str | None
    url: str | None
    snippet: str | None
    basis: str
    dimension: str
    polarity: int
    outcome_tier: str | None
    observed_at: datetime


async def ensure_defaults(session: AsyncSession, account_id: UUID) -> int:
    raise NotImplementedError


async def list_items(
    session: AsyncSession, account_id: UUID, segment_key: str, page: int = 1, size: int = PAGE_SIZE
) -> Page:
    raise NotImplementedError


async def counts(session: AsyncSession, account_id: UUID) -> BrainCounts:
    raise NotImplementedError


async def item_view(session: AsyncSession, account_id: UUID, item_id: UUID) -> ItemView:
    raise NotImplementedError


async def evidence_list(
    session: AsyncSession, account_id: UUID, item_id: UUID, limit: int = 10
) -> list[EvidenceRow]:
    raise NotImplementedError


async def search(
    session: AsyncSession, account_id: UUID, query: str, limit: int = 10
) -> list[SegmentItem]:
    raise NotImplementedError
