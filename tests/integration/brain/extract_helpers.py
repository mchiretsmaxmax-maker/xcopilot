"""Помощники тестов извлечения: кандидаты и счётчики."""

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from xcopilot.brain.schemas import ExtractionResult, KnowledgeCandidate


def candidate(**over: Any) -> KnowledgeCandidate:
    base: dict[str, Any] = {
        "kind": "hook_pattern",
        "layer": "craft",
        "element": "hook",
        "statement": "Личная цифра в первой строке даёт больше ответов",
        "guidance": "Начни пост с личной цифры",
        "target_metric": "engagement",
        "detector": {
            "type": "feature",
            "all": [{"path": "features.has_number_first_line", "op": "is_true"}],
        },
        "supporting": ["M1", "M2"],
        "rationale": "У обоих материалов цифра в первой строке",
    }
    base.update(over)
    return KnowledgeCandidate.model_validate(base)


def result(*cands: KnowledgeCandidate) -> ExtractionResult:
    return ExtractionResult(candidates=list(cands))


async def count(session: AsyncSession, sql: str, **params: Any) -> int:
    return int((await session.execute(text(sql), params)).scalar_one())


async def items_of(session: AsyncSession, acc: UUID) -> list[dict[str, Any]]:
    res = await session.execute(
        text(
            "SELECT id, status, layer, risk_class, merged_into, statement FROM knowledge_items"
            " WHERE account_id = :a ORDER BY created_at, id"
        ),
        {"a": acc},
    )
    return [dict(r._mapping) for r in res.all()]
