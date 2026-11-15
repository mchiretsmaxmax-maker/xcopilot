"""Детекторы знаний (ТЗ v3.1, 9A, задача 2): feature — кодом по полям разбора, rubric — судьёй."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from xcopilot.brain.context import BrainContext
from xcopilot.brain.schemas import FeatureDetector

Part = Literal["first_line", "full_text"]


@dataclass(frozen=True)
class RubricItem:
    """Знание с детектором-рубрикой: id и утверждение для судьи."""

    item_id: UUID
    question: str


@dataclass(frozen=True)
class Hit:
    """Результат детектора: знание есть в тексте (present) или точно нет."""

    item_id: UUID
    present: bool
    score: float
    by: Literal["feature", "rubric"]


def eval_feature(
    detector: FeatureDetector | Mapping[str, Any], analysis: Mapping[str, Any]
) -> float:
    """1.0 — все условия выполнены, 0.0 — нет. Неизвестное поле — ValueError."""
    raise NotImplementedError


async def eval_rubrics(
    ctx: BrainContext, items: Sequence[RubricItem], text: str, part: Part
) -> dict[UUID, float]:
    """Вероятность «да» судьи по каждому знанию; вопросы — пачками по 10."""
    raise NotImplementedError


async def detect(ctx: BrainContext, source_id: UUID) -> list[Hit]:
    """Детекторы всех рабочих знаний на текущем разборе материала; неуверенные — отброшены."""
    raise NotImplementedError
