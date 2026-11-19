"""Политика решения ворот (этап 11, задача 6): pass / rewrite / needs_owner по порогам."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from xcopilot.quality.judge import SLOP_OPTIONS, JudgeVerdict
from xcopilot.quality.lint.engine import LintReport

Outcome = Literal["pass", "rewrite", "needs_owner"]
CONFIG = Path(__file__).resolve().parents[3] / "config" / "thresholds.toml"


class Thresholds(BaseModel):
    voice_match: float = 2.0
    ai_smell: float = 2.0
    on_topic: float = 0.7
    grounded: float = 0.7
    risky: float = 0.3
    min_confidence: float = 0.6
    unsure_low: float = 0.4
    unsure_high: float = 0.6
    max_rewrites: int = 2
    strict_formats: list[str] = Field(default_factory=lambda: ["reply", "quote"])
    strict_slop_confidence: float = 0.4
    strict_hook_max: float = 1.0

    @classmethod
    def load(cls, path: Path = CONFIG) -> Thresholds:
        """Пороги из `config/thresholds.toml` (раздел [gate]); нет файла — значения по умолчанию."""
        if not path.exists():
            return cls()
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        return cls().with_overrides(data.get("gate", {}))

    def with_overrides(self, overrides: Mapping[str, Any] | None) -> Thresholds:
        """Переопределение аккаунта (`accounts.settings["thresholds"]`); чужие ключи — мимо."""
        if not overrides:
            return self
        known = {k: v for k, v in overrides.items() if k in type(self).model_fields}
        return self.model_validate({**self.model_dump(), **known})


class Reason(BaseModel):
    source: Literal["lint", "judge"]
    code: str
    fragment: str = ""
    message: str = ""


class Decision(BaseModel):
    outcome: Outcome
    reasons: list[Reason] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    lint: LintReport | None = None
    verdict: JudgeVerdict | None = None
    draft_id: UUID | None = None
    revision: int = 1
    rewrites: int = 0


_ISSUE_OF_SCORE = {"voice_match": "voice", "ai_smell": "ai_phrases"}


def _unsure(verdict: JudgeVerdict, t: Thresholds) -> bool:
    scales = (verdict.voice_match, verdict.ai_smell, verdict.hook)
    if any(s.confidence < t.min_confidence for s in scales):
        return True
    return any(t.unsure_low <= p <= t.unsure_high for p in (verdict.on_topic, verdict.grounded,
                                                              verdict.risky))  # fmt: skip


def decide(
    report: LintReport, verdict: JudgeVerdict | None, thresholds: Thresholds, format: str = ""
) -> Decision:
    """Ошибки линтера → rewrite; risky → needs_owner; судья не уверен → needs_owner;
    порог не взят → rewrite с кодами причин; иначе pass. Без судьи — pass с `lint_only`."""
    t = thresholds
    if report.errors:
        reasons = [
            Reason(source="lint", code=f.rule, fragment=f.fragment, message=f.message)
            for f in report.errors
        ]
        return Decision(outcome="rewrite", reasons=reasons, lint=report, verdict=verdict)
    if verdict is None:
        return Decision(outcome="pass", flags=["lint_only"], lint=report)
    flags = ["fallback_judge"] if verdict.fallback else []
    if verdict.risky > t.risky:
        reason = Reason(source="judge", code="risky", message="опасно для аккаунта")
        return Decision(outcome="needs_owner", reasons=[reason], flags=[*flags, "risky"],
                        lint=report, verdict=verdict)  # fmt: skip
    if _unsure(verdict, t):
        return Decision(outcome="needs_owner", flags=[*flags, "judge_unsure"], lint=report,
                        verdict=verdict)  # fmt: skip
    codes: list[str] = []
    if verdict.voice_match.score < t.voice_match:
        codes.append("voice")
    if verdict.ai_smell.score < t.ai_smell:
        codes.append("ai_phrases")
    if verdict.on_topic < t.on_topic:
        codes.append("off_topic")
    if verdict.grounded < t.grounded:
        codes.append("unsupported_claim")
    slop = verdict.ai_slop != "none" and verdict.ai_slop_confidence >= t.min_confidence
    if (codes or slop) and verdict.main_issue not in ("none", *codes):
        codes.append(verdict.main_issue)
    if codes or slop:
        reasons = [Reason(source="judge", code=c) for c in codes]
        if slop:
            label = SLOP_OPTIONS.get(verdict.ai_slop) or verdict.ai_slop
            reasons.append(Reason(source="judge", code="ai_slop", fragment=verdict.ai_slop_quote,
                                  message=label))  # fmt: skip
        return Decision(outcome="rewrite", reasons=reasons, flags=flags, lint=report,
                        verdict=verdict)  # fmt: skip
    return Decision(outcome="pass", flags=flags, lint=report, verdict=verdict)
