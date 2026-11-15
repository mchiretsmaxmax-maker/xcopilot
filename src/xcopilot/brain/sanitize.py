"""Проверка кандидата судьёй (ТЗ v3.1, 9A, задача 5): ремесло, переносимость, слой.

Пороги (приложение E): `is_craft_claim` < 0.7 — отброшен; `portable` < 0.5 при слое не voice —
отброшен; слой от судьи с уверенностью ≥ 0.7 заменяет слой от модели-извлекателя.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast, get_args

from xcopilot.brain import judge_questions
from xcopilot.brain.context import BrainContext
from xcopilot.brain.schemas import KnowledgeCandidate, Layer
from xcopilot.llm.schemas import ChoiceA, NoulA

PURPOSE = "brain.candidate"
LAYERS = frozenset(get_args(Layer))


@dataclass(frozen=True)
class SanitizeVerdict:
    ok: bool
    layer: Layer
    reason: str | None = None
    scores: dict[str, float] | None = None


def _state(candidate: KnowledgeCandidate, stances: Sequence[str]) -> str:
    return (
        f"statement: {candidate.statement}\n"
        f"guidance: {candidate.guidance}\n"
        f"layer (по мнению извлекателя): {candidate.layer}\n"
        f"материалы-доказательства: {', '.join(stances) or 'нет'}"
    )


async def check(
    ctx: BrainContext, candidate: KnowledgeCandidate, stances: Sequence[str] = ()
) -> SanitizeVerdict:
    """Вопросы судье о кандидате; слой судьи с уверенностью ≥ 0.7 заменяет слой извлекателя."""
    cfg = ctx.config.judge
    verdict = await ctx.llm.decide(
        state=_state(candidate, stances),
        questions=judge_questions.CANDIDATE,
        account_id=ctx.account_id,
        purpose=PURPOSE,
    )
    craft = verdict.answers.get("is_craft_claim")
    portable = verdict.answers.get("portable")
    layer_a = verdict.answers.get("layer")
    p_craft = craft.p if isinstance(craft, NoulA) else 0.0
    p_portable = portable.p if isinstance(portable, NoulA) else 0.0
    layer: Layer = candidate.layer
    if (
        isinstance(layer_a, ChoiceA)
        and layer_a.choice in LAYERS
        and layer_a.confidence >= cfg.layer_override
    ):
        layer = cast(Layer, layer_a.choice)
    scores = {"is_craft_claim": p_craft, "portable": p_portable}
    if p_craft < cfg.craft_min:
        return SanitizeVerdict(False, layer, "not_craft", scores)
    if layer != "voice" and p_portable < cfg.portable_min:
        return SanitizeVerdict(False, layer, "not_portable", scores)
    return SanitizeVerdict(True, layer, None, scores)
