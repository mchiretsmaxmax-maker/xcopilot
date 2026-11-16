"""Ежедневный отчёт (этап 14, задача 8). Заглушка."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from xcopilot.clock import Clock
from xcopilot.db.uow import UnitOfWork
from xcopilot.llm.gateway import LLMGateway

PROMPT_KEY = "reports.daily.v1"


@dataclass(frozen=True)
class DayStats:
    day: date
    posts: int
    best_impressions: int | None
    best_vs_baseline_pct: int | None
    approved_clean: int
    decided: int
    spend_usd: float
    confirmed: int = 0
    refuted: int = 0
    refuted_example: str | None = None
    reach_hidden: bool = False
    facts: list[str] = field(default_factory=list)


def daily_report_template(stats: DayStats) -> str:
    raise NotImplementedError


class Reports:
    def __init__(
        self, *, uow_factory: Callable[[], UnitOfWork], gateway: LLMGateway | None, clock: Clock
    ) -> None:
        raise NotImplementedError

    async def daily_report(self, account_id: UUID, day: date) -> str:
        raise NotImplementedError
