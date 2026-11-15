"""Отбор и ранжирование знаний под задачу (ТЗ v3.1, 4.1–4.2). Чистые функции, без БД."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from xcopilot.brain.schemas import PackItem, TaskSpec


@dataclass(frozen=True)
class Candidate:
    """Знание, допущенное к выдаче, со всем, что нужно для оценки."""

    item_id: UUID
    version: int
    kind: str
    layer: str
    element: str
    statement: str
    guidance: str
    skeleton: str | None
    status: str
    owner_state: str
    quality: float
    scopes: Mapping[str, frozenset[str]]
    vec: Sequence[float] | None = None
    last_evidence_at: datetime | None = None
    own_support: int = 0
    own_contra: int = 0
    probation_started_at: datetime | None = None
    applied: int = 0


@dataclass(frozen=True)
class Scored:
    item: Candidate
    score: float


def slots_for(fmt: str) -> dict[str, int]:
    raise NotImplementedError


def scope_match(item: Candidate, task: TaskSpec) -> float | None:
    raise NotImplementedError


def score(
    item: Candidate, task: TaskSpec, topic_vec: Sequence[float] | None, now: datetime
) -> float:
    raise NotImplementedError


def fill_slots(
    scored: Sequence[Scored], slots: Mapping[str, int], budget_tokens: int
) -> list[PackItem]:
    raise NotImplementedError


def pick_probation(candidates: Sequence[Candidate]) -> Candidate | None:
    raise NotImplementedError


def item_tokens(item: PackItem) -> int:
    """Токены знания в пакете (по тексту, который увидит модель)."""
    raise NotImplementedError


def shares_run(text: str, foreign: Sequence[str], n: int = 6) -> bool:
    """Есть ли в тексте n слов подряд из чужого материала (защита от дословного переноса)."""
    raise NotImplementedError
