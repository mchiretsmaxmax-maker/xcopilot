"""Сегменты и чтение базы для бота (ТЗ v3.1, 9C): стартовые сегменты, списки, карточка,
доказательства, поиск, счётчики. Только чтение (и копирование сегментов при онбординге)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from math import ceil
from typing import Any
from uuid import UUID

from xcopilot.db.repos import AccountsRepo
from xcopilot.db.repos.brain_owner import OwnerRepo, like_pattern
from xcopilot.db.repos.knowledge import KnowledgeItemsRepo
from xcopilot.db.uow import DbSession
from xcopilot.errors import NotFound

PAGE_SIZE = 5
# Сегменты «по цели» (ТЗ v3.1, Б2.1): общие девять приходят копией из аккаунта global.
GOAL_SEGMENTS: tuple[tuple[str, str, str], ...] = (
    ("goal_reach", "Охват", "reach"),
    ("goal_engagement", "Вовлечённость", "engagement"),
    ("goal_replies", "Ответы", "replies"),
    ("goal_saves", "Сохранения", "saves"),
)
HIGH, MEDIUM = 0.60, 0.45  # границы словесной уверенности (представление v_knowledge_cards)


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


def confidence_word(quality: float) -> str:
    return "высокая" if quality >= HIGH else "средняя" if quality >= MEDIUM else "низкая"


def _item(r: dict[str, Any]) -> SegmentItem:
    return SegmentItem(
        id=r["id"],
        statement=r["statement"],
        guidance=r["guidance"],
        kind=r["kind"],
        layer=r["layer"],
        element=r["element"],
        status=r["status"],
        owner_state=r["owner_state"],
        risk_class=r["risk_class"],
        quality=float(r["quality"]),
    )


async def ensure_defaults(session: DbSession, account_id: UUID) -> int:
    """Стартовые сегменты аккаунта (один раз): общие из global и четыре цели; вернуть новые."""
    repo = OwnerRepo(session, account_id)
    created = await repo.copy_default_segments((await AccountsRepo(session).get_global()).id)
    for position, (key, title, goal) in enumerate(GOAL_SEGMENTS, start=100):
        if await repo.add_segment(key, title, {"goal": [goal]}, position):
            created += 1
    return created


async def list_items(
    session: DbSession, account_id: UUID, segment_key: str, page: int = 1, size: int = PAGE_SIZE
) -> Page:
    """Страница сегмента: сверху самые сильные по качеству."""
    repo = OwnerRepo(session, account_id)
    if not await repo.segment_exists(segment_key):
        raise NotFound(f"segments: {segment_key} не найден")
    total = await repo.segment_total(segment_key)
    pages = max(1, ceil(total / size))
    page = min(max(page, 1), pages)
    rows = await repo.segment_page(segment_key, limit=size, offset=(page - 1) * size)
    return Page([_item(r) for r in rows], page, pages, total)


async def counts(session: DbSession, account_id: UUID) -> BrainCounts:
    repo = OwnerRepo(session, account_id)
    by_status = await repo.status_counts()
    working = ("candidate", "probation", "active", "weakened", "conflicted")
    return BrainCounts(
        materials=await repo.material_count(),
        items_total=sum(by_status.get(s, 0) for s in working),
        by_status=by_status,
        segments=[
            SegmentCount(r["key"], r["title"], int(r["n"])) for r in await repo.segment_counts()
        ],
        conflicts=await repo.conflict_count(),
        class_a_pending=len(await repo.class_a_pending()),
    )


async def class_a_pending(session: DbSession, account_id: UUID) -> list[SegmentItem]:
    """Знания класса A (голос), ждущие явного «да»."""
    return [_item(r) for r in await OwnerRepo(session, account_id).class_a_pending()]


async def item_view(session: DbSession, account_id: UUID, item_id: UUID) -> ItemView:
    row = await OwnerRepo(session, account_id).card(item_id)
    if row is None:
        raise NotFound(f"знание {item_id} не найдено")
    scopes = await KnowledgeItemsRepo(session, account_id).scopes(item_id)
    quality = float(row["quality"])
    return ItemView(
        item=_item(row),
        version=int(row["version"]),
        eff_conf=float(row["eff_conf"]),
        fit_conf=float(row["fit_conf"]),
        confidence_word=confidence_word(quality),
        fit_known=bool(row["fit_known"]),
        evidence_n=int(row["evidence_n"]),
        posts=int(row["posts"]),
        authors=int(row["authors"]),
        own_support=int(row["own_support"]),
        own_contra=int(row["own_contra"]),
        scopes=scopes,
        origin=row["origin"],
        target_metric=row["target_metric"],
    )


async def evidence_list(
    session: DbSession, account_id: UUID, item_id: UUID, limit: int = 10
) -> list[EvidenceRow]:
    """Живые доказательства знания со ссылкой на материал (автор, ссылка, начало текста)."""
    rows = await OwnerRepo(session, account_id).evidence(item_id, limit)
    return [
        EvidenceRow(
            id=r["id"],
            source_id=r["source_id"],
            author_handle=r["author_handle"],
            url=r["url"],
            snippet=r["snippet"],
            basis=r["basis"],
            dimension=r["dimension"],
            polarity=int(r["polarity"]),
            outcome_tier=r["outcome_tier"],
            observed_at=r["observed_at"],
        )
        for r in rows
    ]


async def search(
    session: DbSession, account_id: UUID, query: str, limit: int = 10
) -> list[SegmentItem]:
    """Поиск по тексту знаний (без учёта регистра); удалённые и влитые не показываются."""
    query = query.strip()
    if not query:
        return []
    rows = await OwnerRepo(session, account_id).search(like_pattern(query), limit)
    return [_item(r) for r in rows]
