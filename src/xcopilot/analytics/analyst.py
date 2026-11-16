"""Отчёт аналитика: модель пересказывает посчитанное (этап 14, задача 7). Заглушка."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import UUID

from pydantic import BaseModel

from xcopilot.analytics.patterns import Patterns, Period
from xcopilot.clock import Clock
from xcopilot.db.uow import UnitOfWork
from xcopilot.llm.gateway import LLMGateway

PROMPT_KEY = "analyst.report.v1"


class Claim(BaseModel):
    text: str
    post_ids: list[str]
    knowledge_ids: list[str] = []


class AnalystOut(BaseModel):
    worked: list[Claim]
    improve: list[Claim]


@dataclass(frozen=True)
class AnalystReport:
    patterns: Patterns
    worked: list[Claim] = field(default_factory=list)
    improve: list[Claim] = field(default_factory=list)
    warning: str | None = None
    dropped: int = 0


class Analyst:
    def __init__(
        self, *, uow_factory: Callable[[], UnitOfWork], gateway: LLMGateway, clock: Clock
    ) -> None:
        raise NotImplementedError

    async def build_report(self, account_id: UUID, period: Period | None = None) -> AnalystReport:
        raise NotImplementedError
