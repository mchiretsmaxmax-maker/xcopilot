"""Ежедневный отчёт (этап 14, задача 8; v3.1: исходы знаний на вчерашних постах).

Числа считает код. Дешёвая модель только пересказывает готовый шаблон; если модель недоступна
или в пересказе появилось число, которого нет в шаблоне, владельцу уходит сам шаблон.
Режим X `off`: охватов нет, в отчёте только продуктовые цифры.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from datetime import tzinfo as _tzinfo
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from pydantic import BaseModel

from xcopilot.analytics.baseline import baseline
from xcopilot.clock import Clock
from xcopilot.db.repos import AccountsRepo, AnalyticsRepo
from xcopilot.db.uow import DbSession, UnitOfWork
from xcopilot.errors import BudgetExceeded, NotFound
from xcopilot.llm.errors import LLMError
from xcopilot.llm.gateway import LLMGateway
from xcopilot.llm.roles import Role
from xcopilot.quality.lint.numbers import extract_numbers, is_grounded

log = structlog.get_logger(__name__)

PROMPT_KEY = "reports.daily.v1"


class DailyText(BaseModel):
    text: str


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


def plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def format_views(n: int) -> str:
    return f"{round(n / 1000)} тыс." if n >= 1000 else str(n)


def daily_report_template(stats: DayStats) -> str:
    if stats.posts == 0:
        head = "Вчера: постов не было."
    elif stats.reach_hidden or stats.best_impressions is None:
        head = f"Вчера: {stats.posts} {plural(stats.posts, 'пост', 'поста', 'постов')}."
    else:
        head = (
            f"Вчера: {stats.posts} {plural(stats.posts, 'пост', 'поста', 'постов')},"
            f" лучший — {format_views(stats.best_impressions)} показов"
        )
        if stats.best_vs_baseline_pct is not None:
            sign = "+" if stats.best_vs_baseline_pct >= 0 else "-"
            head += f" ({sign}{abs(stats.best_vs_baseline_pct)}% к обычному)"
        head += "."
    parts = [head]
    if stats.decided:
        parts.append(f"Без правок одобрено {stats.approved_clean} из {stats.decided}.")
    parts.append(f"Расход ${stats.spend_usd:.2f}.")
    if stats.confirmed or stats.refuted:
        line = (
            f"Вчерашний пост подтвердил {stats.confirmed}"
            f" {plural(stats.confirmed, 'знание', 'знания', 'знаний')}"
            f" и опроверг {stats.refuted}"
        )
        if stats.refuted_example:
            line += f": «{stats.refuted_example}» у тебя не сработало"
        parts.append(line + ".")
    return " ".join(parts)


def day_bounds(day: date, tz: str) -> tuple[datetime, datetime]:
    zone: _tzinfo = ZoneInfo(tz)
    start = datetime.combine(day, time(0), tzinfo=zone)
    end = datetime.combine(day + timedelta(days=1), time(0), tzinfo=zone)
    return start.astimezone(UTC), end.astimezone(UTC)


class Reports:
    def __init__(
        self, *, uow_factory: Callable[[], UnitOfWork], gateway: LLMGateway | None, clock: Clock
    ) -> None:
        self._uow = uow_factory
        self._gateway = gateway
        self._clock = clock

    def now(self) -> datetime:
        return self._clock.now()

    async def daily_report(self, account_id: UUID, day: date) -> str:
        async with self._uow() as uow:
            stats = await self._stats(uow.session, account_id, day)
        template = daily_report_template(stats)
        if self._gateway is None:
            return template
        try:
            out = await self._gateway.generate(
                role=Role.CHEAP,
                prompt_key=PROMPT_KEY,
                variables={"day": day.isoformat(), "template": template},
                schema=DailyText,
                account_id=account_id,
                purpose="reports.daily",
            )
        except (LLMError, BudgetExceeded):
            log.info("daily_report_template", account_id=str(account_id))
            return template
        retold = out.text.strip()
        numbers = [t for t in extract_numbers(retold) if not t.skip]
        if not retold or not all(is_grounded(t, [template], "") for t in numbers):
            return template
        return retold

    async def _stats(self, session: DbSession, account_id: UUID, day: date) -> DayStats:
        account = await AccountsRepo(session).get(account_id)
        if account is None:
            raise NotFound(f"аккаунт {account_id} не найден")
        start, end = day_bounds(day, account.tz)
        hidden = account.x_read_mode == "off"
        repo = AnalyticsRepo(session, account_id)
        posts = 0
        best: int | None = None
        pct: int | None = None
        if hidden:
            posts = await repo.publications_count(start, end)
        else:
            rows = await repo.own_posts_with_views(start, end - timedelta(seconds=1))
            posts = await repo.own_posts_count(start, end)
            if rows:
                top = max(rows, key=lambda r: int(r["impressions"]))
                best = int(top["impressions"])
                base = await baseline(session, account_id, top["kind"], 30, now=self._clock.now())
                if base.median_impressions:
                    pct = round((best / base.median_impressions - 1) * 100)
        decided, clean = await repo.review_counts(start, end)
        confirmed, refuted = await repo.own_outcome_counts(start, end)
        return DayStats(
            day=day,
            posts=posts,
            best_impressions=best,
            best_vs_baseline_pct=pct,
            approved_clean=clean,
            decided=decided,
            spend_usd=await repo.spend_micro(start, end) / 1_000_000,
            confirmed=confirmed,
            refuted=refuted,
            refuted_example=await repo.first_refuted_statement(start, end) if refuted else None,
            reach_hidden=hidden,
        )
