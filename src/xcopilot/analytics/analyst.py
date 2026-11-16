"""Отчёт аналитика (этап 14, задача 7; v3.1, задача 15).

Закономерности считает код (`patterns.py`). Модель роли `writer` только пересказывает их и
предлагает до трёх улучшений. Каждый пункт обязан ссылаться на посты-доказательства из расчёта;
число в тексте обязано быть среди посчитанных (линтер `number_grounding`). Остальное отбрасывается.
Недоступная модель — отчёт без пересказа, только цифры.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import UUID

import structlog
from pydantic import BaseModel

from xcopilot.analytics.patterns import (
    MIN_SAMPLE,
    Pattern,
    Patterns,
    Period,
    compute_patterns,
    to_candidates,
)
from xcopilot.clock import Clock
from xcopilot.db.repos import AccountsRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.errors import BudgetExceeded, NotFound
from xcopilot.llm.errors import LLMError
from xcopilot.llm.gateway import LLMGateway
from xcopilot.llm.roles import Role
from xcopilot.llm.untrusted import wrap_untrusted
from xcopilot.quality.lint.numbers import extract_numbers, is_grounded

if TYPE_CHECKING:
    from xcopilot.brain.jobs import BrainDeps

log = structlog.get_logger(__name__)

PROMPT_KEY = "analyst.report.v1"
PERIOD_DAYS = 30
MAX_IMPROVE = 3


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
    knowledge: list[UUID] = field(default_factory=list)


def pattern_facts(p: Pattern) -> list[str]:
    """Числа закономерности в виде строк: только они разрешены в тексте отчёта."""
    return [
        p.value,
        f"{p.n} постов",
        f"{abs(round(p.lift * 100))}%",
        f"{round(p.median)}",
        f"{round(p.baseline)}",
    ]


def render_pattern(i: int, p: Pattern) -> str:
    sign = "+" if p.lift >= 0 else "-"
    return (
        f"#{i} {p.dimension}={p.value}: {p.n} постов, медиана {round(p.median)} показов,"
        f" {sign}{abs(round(p.lift * 100))}% к обычному ({round(p.baseline)});"
        f" посты: {', '.join(p.post_ids[:10])}"
    )


class Analyst:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        gateway: LLMGateway,
        clock: Clock,
        brain: BrainDeps | None = None,
    ) -> None:
        self._uow = uow_factory
        self._gateway = gateway
        self._clock = clock
        self._brain = brain

    async def build_report(self, account_id: UUID, period: Period | None = None) -> AnalystReport:
        now = self._clock.now()
        period = period or Period(now - timedelta(days=PERIOD_DAYS), now)
        async with self._uow() as uow:
            account = await AccountsRepo(uow.session).get(account_id)
            if account is None:
                raise NotFound(f"аккаунт {account_id} не найден")
            niche, lang, tz = account.niche or "", account.lang, account.tz
            patterns = await compute_patterns(uow.session, account_id, period, tz=tz)
        warning = (
            f"Мало данных: {patterns.sample_size} постов, для выводов нужно хотя бы {MIN_SAMPLE}."
            if patterns.small_sample
            else None
        )
        if not patterns.items:
            return AnalystReport(patterns, warning=warning)
        knowledge = await self._propose_knowledge(account_id, patterns)
        lines = [render_pattern(i + 1, p) for i, p in enumerate(patterns.items)]
        try:
            out = await self._gateway.generate(
                role=Role.WRITER,
                prompt_key=PROMPT_KEY,
                variables={
                    "niche": f"{niche} ({lang})",
                    "sample": patterns.sample_size,
                    "baseline": round(patterns.baseline_median or 0),
                    "patterns": wrap_untrusted("\n".join(lines), "patterns"),
                },
                schema=AnalystOut,
                account_id=account_id,
                purpose="analytics.report",
            )
        except (LLMError, BudgetExceeded):
            log.warning("analyst_model_unavailable", account_id=str(account_id))
            return AnalystReport(
                patterns,
                warning=warning or "Модель недоступна: показываю только цифры.",
                knowledge=knowledge,
            )
        facts = [f for p in patterns.items for f in pattern_facts(p)]
        facts += [str(patterns.sample_size), str(round(patterns.baseline_median or 0))]
        known = {pid for p in patterns.items for pid in p.post_ids}
        worked, dropped_w = self._check(out.worked, known, facts, knowledge)
        improve, dropped_i = self._check(out.improve[:MAX_IMPROVE], known, facts, knowledge)
        dropped = dropped_w + dropped_i + max(0, len(out.improve) - MAX_IMPROVE)
        return AnalystReport(patterns, worked, improve, warning, dropped, knowledge)

    @staticmethod
    def _check(
        claims: list[Claim], known: set[str], facts: list[str], knowledge: list[UUID]
    ) -> tuple[list[Claim], int]:
        """Оставить пункты с доказательствами и посчитанными числами."""
        kept: list[Claim] = []
        allowed_k = {str(k) for k in knowledge}
        for c in claims:
            post_ids = [pid for pid in c.post_ids if pid in known]
            numbers = [t for t in extract_numbers(c.text) if not t.skip]
            if not post_ids or not all(is_grounded(t, facts, "") for t in numbers):
                continue
            kept.append(
                Claim(
                    text=c.text,
                    post_ids=post_ids,
                    knowledge_ids=[k for k in c.knowledge_ids if k in allowed_k],
                )
            )
        return kept, len(claims) - len(kept)

    async def _propose_knowledge(self, account_id: UUID, patterns: Patterns) -> list[UUID]:
        """Закономерности → кандидаты знаний `own_metrics` через механизм слияния 9A."""
        if self._brain is None or patterns.small_sample:
            return []
        from xcopilot.brain.extract import Batch, ingest_candidates
        from xcopilot.brain.schemas import ExtractionResult

        candidates = to_candidates(patterns)
        if not candidates:
            return []
        async with self._uow() as uow:
            ctx = self._brain.ctx(uow.session, account_id)
            batch = Batch(account_id, "post", list(patterns.sources))
            return await ingest_candidates(
                ctx, ExtractionResult(candidates=candidates), batch, origin="own_metrics"
            )


def render_claims(title: str, claims: list[Claim]) -> str:
    """Пункты отчёта списком, у каждого — ссылки на посты-доказательства."""
    lines = [title]
    for c in claims:
        links = ", ".join(f"https://x.com/i/web/status/{pid}" for pid in c.post_ids[:3])
        lines.append(f"• {c.text}\n  {links}")
    return "\n".join(lines)
