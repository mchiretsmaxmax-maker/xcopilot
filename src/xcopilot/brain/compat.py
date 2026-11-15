"""Совместимость указания с паспортом голоса (ТЗ v3.1, 9A, задача 7).

Совместимость = allowed × natural / 2 (приложение E): `allowed` — вероятность, что указание не
нарушает жёстких правил, `natural` — ожидаемая оценка по шкале 0..2. Для автоиспытания ≥ 0.6.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from xcopilot.brain import judge_questions
from xcopilot.brain.context import BrainContext
from xcopilot.llm.schemas import NoulA, ScoreA

PURPOSE = "brain.voice_compat"


def passport_text(passport: Mapping[str, Any]) -> str:
    """Краткий паспорт для судьи и извлечения: резюме голоса и жёсткие правила."""
    summary = passport.get("summary") or passport.get("voice") or ""
    if not isinstance(summary, str):
        summary = json.dumps(summary, ensure_ascii=False)
    rules = passport.get("hard_rules") or []
    return f"voice: {summary[:1200]}\nhard_rules: {json.dumps(rules, ensure_ascii=False)[:1200]}"


async def voice_compat(
    ctx: BrainContext, guidance: str, passport: Mapping[str, Any] | None
) -> float:
    """allowed × natural / 2 (0..1); без паспорта — 1.0 (нарушать нечего)."""
    if not passport:
        return 1.0
    verdict = await ctx.llm.decide(
        state=f"{passport_text(passport)}\nguidance: {guidance}",
        questions=judge_questions.VOICE_COMPAT,
        account_id=ctx.account_id,
        purpose=PURPOSE,
    )
    allowed = verdict.answers.get("allowed")
    natural = verdict.answers.get("natural")
    p = allowed.p if isinstance(allowed, NoulA) else 0.0
    score = natural.score if isinstance(natural, ScoreA) else 0.0
    return round(max(0.0, min(1.0, p * score / 2)), 4)
