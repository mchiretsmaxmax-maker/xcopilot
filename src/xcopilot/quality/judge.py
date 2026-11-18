"""Судья ворот №2 (этап 11, задачи 4–5): состояние, вопросы, типизированный вердикт."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel

from xcopilot.llm.schemas import ChoiceA, ChoiceQ, NoulA, NoulQ, Question, ScoreA, ScoreQ, Verdict
from xcopilot.llm.untrusted import wrap_untrusted
from xcopilot.quality.lint.engine import LintDraft
from xcopilot.retrieval.context_pack import ContextPack

PURPOSE = "draft.judge"
JEV_MAX_QUESTIONS = 10
JEV_MAX_INSTRUCTIONS = 2000
MAIN_ISSUES = (
    "none",
    "voice",
    "ai_phrases",
    "vague",
    "too_long",
    "weak_hook",
    "off_topic",
    "unsupported_claim",
)

EXAMPLES = 3
MAIN_ISSUE_OPTIONS: dict[str, str | None] = {
    "none": "серьёзных проблем нет",
    "voice": "звучит не как автор примеров",
    "ai_phrases": "шаблоны и штампы сгенерированного текста",
    "vague": "общие слова без конкретики",
    "too_long": "слишком длинно для мысли",
    "weak_hook": "слабая первая строка",
    "off_topic": "не раскрывает заданную тему",
    "unsupported_claim": "утверждение или цифра без опоры на факты",
}
assert tuple(MAIN_ISSUE_OPTIONS) == MAIN_ISSUES

# Вопросы ворот №2 (ТЗ, этап 11, задача 5). voice_match — главный страж голоса (v3.1, задача 14).
QUESTIONS_GATE2: dict[str, Question] = {
    "voice_match": ScoreQ(
        instructions="Насколько черновик звучит как автор примеров: лексика, ритм, тон, длина"
        " фраз, манера. Сравнивай с примерами и паспортом, а не с «хорошим текстом вообще»",
        levels=[
            "чужой голос",
            "заметно отличается от примеров",
            "похоже на автора",
            "неотличимо от примеров автора",
        ],
    ),
    "ai_smell": ScoreQ(
        instructions="Насколько черновик читается как написанный человеком, а не моделью:"
        " штампы, гладкость, противопоставления «не X, а Y», тройки, тире, общие слова",
        levels=[
            "явный AI-текст",
            "много признаков AI",
            "почти как человек",
            "читается как человек",
        ],
    ),
    "hook": ScoreQ(
        instructions="Насколько первая строка черновика цепляет читателя ленты X",
        levels=["нет зацепки", "средняя зацепка", "сильная первая строка"],
    ),
    "on_topic": NoulQ(instructions="Черновик раскрывает заданную тему (topic)"),
    "grounded": NoulQ(
        instructions="Все утверждения с цифрами, именами и фактами в черновике подтверждаются"
        " разделом facts; если цифр и фактов нет — ответ «да»"
    ),
    "risky": NoulQ(
        instructions="Черновик содержит оскорбления, финансовые обещания или советы купить,"
        " призывы или другое, опасное для репутации аккаунта"
    ),
    "main_issue": ChoiceQ(
        instructions="Главная проблема черновика (одна)", options=MAIN_ISSUE_OPTIONS
    ),
}


class JudgePort(Protocol):
    """То, что нужно воротам от шлюза (LLMGateway подходит как есть)."""

    async def decide(
        self,
        *,
        state: str,
        questions: Mapping[str, Question],
        account_id: UUID,
        purpose: str,
        entity: tuple[str, UUID] | None = None,
    ) -> Verdict: ...


class JudgeParseError(ValueError):
    """Ответ судьи не сводится к вердикту ворот (нет ответа или не тот тип)."""


class Scale(BaseModel):
    score: float
    confidence: float


class JudgeVerdict(BaseModel):
    voice_match: Scale
    ai_smell: Scale
    hook: Scale
    on_topic: float
    grounded: float
    risky: float
    main_issue: str
    main_issue_confidence: float = 1.0
    judge: str = ""
    fallback: bool = False

    @classmethod
    def from_verdict(cls, verdict: Verdict) -> JudgeVerdict:
        a = verdict.answers

        def scale(name: str) -> Scale:
            ans = a.get(name)
            if not isinstance(ans, ScoreA):
                raise JudgeParseError(f"судья: нет шкалы {name}")
            return Scale(score=ans.score, confidence=ans.confidence)

        def yes(name: str) -> float:
            ans = a.get(name)
            if not isinstance(ans, NoulA):
                raise JudgeParseError(f"судья: нет ответа да/нет {name}")
            return ans.p

        issue = a.get("main_issue")
        if not isinstance(issue, ChoiceA):
            raise JudgeParseError("судья: нет main_issue")
        return cls(
            voice_match=scale("voice_match"),
            ai_smell=scale("ai_smell"),
            hook=scale("hook"),
            on_topic=yes("on_topic"),
            grounded=yes("grounded"),
            risky=yes("risky"),
            main_issue=issue.choice if issue.choice in MAIN_ISSUES else "none",
            main_issue_confidence=issue.confidence,
            judge=verdict.judge,
            fallback=verdict.fallback,
        )


def _short_passport(passport: Mapping[str, Any] | None) -> dict[str, Any]:
    """Краткий паспорт: без жёстких правил и антипримеров (они идут отдельно или не нужны)."""
    if not passport:
        return {}
    return {
        k: v
        for k, v in passport.items()
        if k not in ("hard_rules", "anti_examples", "label", "origin", "owner_set")
    }


def build_state(draft: LintDraft, pack: ContextPack) -> dict[str, Any]:
    """Состояние для судьи: черновик (как данные), паспорт, жёсткие правила, 3 примера, факты."""
    text = draft.text if not draft.parts else "\n\n".join(draft.parts)
    return {
        "topic": pack.task.topic,
        "format": draft.format,
        "passport": _short_passport(pack.passport),
        "hard_rules": dict(pack.hard_rules) if isinstance(pack.hard_rules, Mapping) else {},
        "examples": [c.text for c in pack.examples[:EXAMPLES]],
        "facts": [f.statement for f in pack.facts],
        "draft": wrap_untrusted(text, "draft"),
    }


def render_state(state: Mapping[str, Any]) -> str:
    """Текст состояния для шлюза: служебные разделы — JSON, черновик — в обёртке в конце."""
    meta = {k: v for k, v in state.items() if k != "draft"}
    body = json.dumps(meta, ensure_ascii=False, indent=1, default=str)
    return f"Контекст (JSON):\n{body}\n\nЧерновик для оценки:\n{state['draft']}"


async def judge(
    draft: LintDraft,
    pack: ContextPack,
    *,
    port: JudgePort,
    account_id: UUID,
    entity: tuple[str, UUID] | None = None,
) -> JudgeVerdict:
    """Один вызов судьи на все 7 вопросов ворот №2; ответ — типизированный вердикт."""
    state = render_state(build_state(draft, pack))
    verdict = await port.decide(
        state=state,
        questions=QUESTIONS_GATE2,
        account_id=account_id,
        purpose=PURPOSE,
        entity=entity,
    )
    return JudgeVerdict.from_verdict(verdict)
