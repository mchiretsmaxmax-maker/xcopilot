"""Бюджет расходов: проверка до вызова, учёт после (деньги — int микродоллары).

Лимиты — строки `budgets` аккаунта (scope llm / x_api, period day / month). Если строки на день
нет, действует дневной лимит по умолчанию из настроек (LLM_DAILY_LIMIT_USD / X_DAILY_LIMIT_USD),
жёсткий. Сутки и месяц считаются в часовом поясе аккаунта (как свёртки метрик).

Потрачено = сумма `cost_micro_usd` журнала за период: llm → `llm_calls`, x_api → `x_api_calls`.
Поэтому `record` вызывается после того, как строка журнала записана, а для x_api не вызывается
вовсе (этап 6 только пишет журнал) — иначе двойной счёт.

- `check`: если потрачено + оценка > лимита: жёсткий лимит → событие `budget.exceeded` (одно
  за период) и `BudgetExceeded`, провайдер не вызывается; мягкий → `budget.warning`, вызов идёт.
- `record`: потрачено ≥ 80% лимита → `budget.warning` (одно за период).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time
from uuid import UUID
from zoneinfo import ZoneInfo

from xcopilot.clock import Clock
from xcopilot.db.repos import AccountsRepo, BudgetsRepo, SpendRepo
from xcopilot.db.uow import DbSession, UnitOfWork
from xcopilot.errors import BudgetExceeded, NotFound
from xcopilot.metrics.events import EventType, emit


@dataclass(frozen=True)
class Limit:
    period: str
    limit: int
    hard: bool


def period_window(period: str, now: datetime, tz: str) -> tuple[datetime, datetime]:
    """Границы текущих суток или календарного месяца в поясе tz: [начало, конец)."""
    zone = ZoneInfo(tz)
    local = now.astimezone(zone).date()
    if period == "day":
        start = local
        end = date.fromordinal(local.toordinal() + 1)
    elif period == "month":
        start = local.replace(day=1)
        end = (start.replace(year=start.year + 1, month=1) if start.month == 12
               else start.replace(month=start.month + 1))  # fmt: skip
    else:
        raise ValueError(f"неизвестный период бюджета: {period}")
    return datetime.combine(start, time.min, zone), datetime.combine(end, time.min, zone)


class BudgetGuard:
    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        *,
        default_day_limits: Mapping[str, int],
        warn_ratio: float = 0.8,
    ) -> None:
        self._uow = uow_factory
        self._clock = clock
        self._defaults = dict(default_day_limits)
        self._warn_percent = round(warn_ratio * 100)

    async def check(self, account_id: UUID, scope: str, estimate: int) -> None:
        """Бросить BudgetExceeded, если вызов с оценкой estimate превысит жёсткий лимит."""
        now = self._clock.now()
        blocked: tuple[Limit, int] | None = None
        async with self._uow() as uow:
            tz = await self._tz(uow.session, account_id)
            spend = SpendRepo(uow.session, account_id)
            for lim in await self._limits(uow.session, account_id, scope):
                since, until = period_window(lim.period, now, tz)
                spent = await spend.spent(scope, since, until)
                if spent + estimate <= lim.limit:
                    continue
                if lim.hard:
                    blocked = (lim, spent)
                    await self._emit_once(
                        uow.session,
                        account_id,
                        EventType.BUDGET_EXCEEDED,
                        scope,
                        lim,
                        spent,
                        now,
                        tz,
                    )
                    break
                await self._emit_once(
                    uow.session, account_id, EventType.BUDGET_WARNING, scope, lim, spent, now, tz
                )
        if blocked is not None:
            lim, spent = blocked
            raise BudgetExceeded(
                f"бюджет {scope}/{lim.period}: потрачено {spent} из {lim.limit} мкр$,"
                f" вызов на ~{estimate} мкр$ заблокирован"
            )

    async def record(self, account_id: UUID, scope: str, actual: int) -> None:
        """После записи строки журнала: предупредить, если потрачено ≥ 80% лимита.

        `actual` уже учтён в журнале и сюда передаётся для единообразия интерфейса.
        """
        now = self._clock.now()
        async with self._uow() as uow:
            tz = await self._tz(uow.session, account_id)
            spend = SpendRepo(uow.session, account_id)
            for lim in await self._limits(uow.session, account_id, scope):
                since, until = period_window(lim.period, now, tz)
                spent = await spend.spent(scope, since, until)
                if spent * 100 >= lim.limit * self._warn_percent:
                    await self._emit_once(
                        uow.session,
                        account_id,
                        EventType.BUDGET_WARNING,
                        scope,
                        lim,
                        spent,
                        now,
                        tz,
                    )

    async def _tz(self, session: DbSession, account_id: UUID) -> str:
        account = await AccountsRepo(session).get(account_id)
        if account is None:
            raise NotFound(f"аккаунт {account_id} не найден")
        return str(account.tz)

    async def _limits(self, session: DbSession, account_id: UUID, scope: str) -> list[Limit]:
        repo = BudgetsRepo(session, account_id)
        limits: list[Limit] = []
        day = await repo.get_limit(scope, "day")
        if day is not None:
            limits.append(Limit("day", day.limit_micro_usd, day.hard))
        elif scope in self._defaults:
            limits.append(Limit("day", self._defaults[scope], True))
        month = await repo.get_limit(scope, "month")
        if month is not None:
            limits.append(Limit("month", month.limit_micro_usd, month.hard))
        return limits

    async def _emit_once(
        self,
        session: DbSession,
        account_id: UUID,
        type_: EventType,
        scope: str,
        lim: Limit,
        spent: int,
        now: datetime,
        tz: str,
    ) -> None:
        since, until = period_window(lim.period, now, tz)
        seen = await SpendRepo(session, account_id).budget_events(
            type_.value, scope, lim.period, since, until
        )
        if seen:
            return
        await emit(
            session,
            account_id=account_id,
            type=type_,
            props={
                "scope": scope,
                "period": lim.period,
                "spent_micro_usd": spent,
                "limit_micro_usd": lim.limit,
            },
            ts=now,
        )
