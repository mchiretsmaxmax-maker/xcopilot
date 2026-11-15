"""Слияние кандидата с базой (ТЗ v3.1, 9A, задача 6): ближайшие → отношение → действие."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from xcopilot.brain.context import BrainContext

RelationKind = Literal["same", "narrower", "broader", "contradicts", "different"]


@dataclass(frozen=True)
class Relation:
    kind: RelationKind
    confidence: float


@dataclass(frozen=True)
class Near:
    """Существующее знание рядом с кандидатом."""

    item_id: UUID
    statement: str
    similarity: float


async def nearest(ctx: BrainContext, vec: Sequence[float], k: int = 5) -> list[Near]:
    """До k рабочих знаний аккаунта ближе related_threshold, по убыванию сходства."""
    raise NotImplementedError


async def relate(ctx: BrainContext, statement: str, near: Near) -> Relation:
    """Отношение нового утверждения к существующему по судье (пороги из brain.toml)."""
    raise NotImplementedError


async def apply_relation(
    ctx: BrainContext, new_item_id: UUID, near: Near, relation: Relation
) -> str:
    """Слияние, связь или конфликт; вернуть действие: merged, linked, conflict, none."""
    raise NotImplementedError


async def resolve_conflicts(ctx: BrainContext) -> int:
    """Открытые противоречия с разрывом ≥ 3 своих доказательств решаются сами; вернуть число."""
    raise NotImplementedError
