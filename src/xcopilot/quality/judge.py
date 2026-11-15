"""Судья ворот №2 (этап 11, задачи 4–5): состояние, вопросы, типизированный вердикт."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel

from xcopilot.llm.schemas import Question, Verdict
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

QUESTIONS_GATE2: dict[str, Question] = {}


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
        raise NotImplementedError


def build_state(draft: LintDraft, pack: ContextPack) -> dict[str, Any]:
    raise NotImplementedError


def render_state(state: Mapping[str, Any]) -> str:
    raise NotImplementedError


async def judge(
    draft: LintDraft,
    pack: ContextPack,
    *,
    port: JudgePort,
    account_id: UUID,
    entity: tuple[str, UUID] | None = None,
) -> JudgeVerdict:
    raise NotImplementedError
