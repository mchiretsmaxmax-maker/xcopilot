"""Закономерности кодом (этап 14, задача 6, v3.1: кандидаты знаний). Заглушка."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from xcopilot.brain.schemas import KnowledgeCandidate
from xcopilot.db.uow import DbSession

MIN_SAMPLE = 10  # меньше постов — предупреждение о малой выборке
MIN_GROUP = 3


@dataclass(frozen=True)
class Period:
    start: datetime
    end: datetime


@dataclass(frozen=True)
class Pattern:
    dimension: str  # format | length | hour | topic
    value: str
    n: int
    median: float
    baseline: float
    lift: float  # median / baseline - 1
    post_ids: tuple[str, ...]
    source_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True)
class Patterns:
    sample_size: int
    small_sample: bool
    baseline_median: float | None
    items: tuple[Pattern, ...] = field(default_factory=tuple)
    sources: tuple[UUID, ...] = ()  # все посты закономерностей: номер = метка M<n> кандидатов


async def compute_patterns(
    session: DbSession, account_id: UUID, period: Period, *, tz: str = "UTC"
) -> Patterns:
    raise NotImplementedError


def to_candidates(patterns: Patterns) -> list[KnowledgeCandidate]:
    raise NotImplementedError
