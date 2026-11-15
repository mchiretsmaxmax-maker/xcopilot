"""Извлечение знаний ролью writer (ТЗ v3.1, 9A, задача 4) и приём кандидатов (задачи 5–7)."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from xcopilot.brain.context import BrainContext
from xcopilot.brain.schemas import ExtractionResult

EXTRACT_PROMPT = "brain.extract_knowledge.v1"


@dataclass
class Batch:
    """Пачка разборов одного формата: M1..Mn → source_id, K1..Kn → знания."""

    account_id: UUID
    format: str
    materials: list[UUID]
    own_history: bool = False
    existing: list[UUID] = field(default_factory=list)


async def build_batches(ctx: BrainContext) -> list[Batch]:
    """Новые разборы по форматам, пачки до 25; своя история — только контрастная выборка."""
    raise NotImplementedError


async def contrast_sample(ctx: BrainContext, share: float = 0.2) -> list[UUID]:
    """Свои посты истории: верхние и нижние доли по исходу."""
    raise NotImplementedError


async def extract(ctx: BrainContext, batch: Batch) -> ExtractionResult:
    """Вызов writer с промптом brain.extract_knowledge.v1: разборы, а не сырые тексты."""
    raise NotImplementedError


async def ingest_candidates(
    ctx: BrainContext, result: ExtractionResult, batch: Batch
) -> list[UUID]:
    """Проверка, слияние и запись кандидатов с доказательствами; вернуть затронутые знания."""
    raise NotImplementedError


async def run(ctx: BrainContext) -> list[UUID]:
    """Все пачки аккаунта: извлечь, принять, отметить пачку обработанной."""
    raise NotImplementedError
