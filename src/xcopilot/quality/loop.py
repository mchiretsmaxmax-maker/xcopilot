"""Петля ворот (этап 11, задачи 8–10): линтер → судья → решение, не больше двух переписываний."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, Field

from xcopilot.clock import Clock
from xcopilot.db.uow import UnitOfWork
from xcopilot.quality.attribution import AppliedDetector
from xcopilot.quality.judge import JudgePort
from xcopilot.quality.knowledge import ClassCRules
from xcopilot.quality.lint.engine import Linter
from xcopilot.quality.policy import Decision, Thresholds
from xcopilot.retrieval.context_pack import ContextPack

REWRITE_PROMPT = "writer.rewrite.v1"


class RewriteOut(BaseModel):
    text: str = Field(min_length=1, max_length=25_000)
    parts: list[str] = Field(default_factory=list, max_length=8)


class Rewriter(Protocol):
    async def rewrite(
        self,
        *,
        account_id: UUID,
        draft_id: UUID,
        text: str,
        parts: Sequence[str],
        format: str,
        instructions: Sequence[str],
        pack: ContextPack,
    ) -> RewriteOut: ...


class QualityGate:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        judge: JudgePort | None,
        rewriter: Rewriter,
        clock: Clock,
        linter: Linter | None = None,
        thresholds: Thresholds | None = None,
        class_c: ClassCRules | None = None,
        detector: AppliedDetector | None = None,
        global_banned: Sequence[str] | None = None,
    ) -> None:
        raise NotImplementedError

    async def run_gate(self, account_id: UUID, draft_id: UUID) -> Decision:
        raise NotImplementedError
