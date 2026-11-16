"""Кандидаты знаний из правок и отказов (ТЗ 13, задачи 2–3, 6)."""

from __future__ import annotations

from uuid import UUID

from xcopilot.brain.context import BrainContext
from xcopilot.brain.schemas import KnowledgeCandidate

PROMPT = "teacher.from_reviews.v1"
LEARNING_PROPOSE_KIND = "learning.propose"


async def propose(ctx: BrainContext) -> list[KnowledgeCandidate]:
    raise NotImplementedError


async def detect_removed_devices(ctx: BrainContext, review_id: UUID) -> list[UUID]:
    raise NotImplementedError
