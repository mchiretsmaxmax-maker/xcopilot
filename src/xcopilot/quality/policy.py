"""Политика решения ворот (этап 11, задача 6): pass / rewrite / needs_owner по порогам."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from xcopilot.quality.judge import JudgeVerdict
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

    @classmethod
    def load(cls, path: Path = CONFIG) -> Thresholds:
        raise NotImplementedError

    def with_overrides(self, overrides: Mapping[str, Any] | None) -> Thresholds:
        raise NotImplementedError


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


def decide(report: LintReport, verdict: JudgeVerdict | None, thresholds: Thresholds) -> Decision:
    raise NotImplementedError
