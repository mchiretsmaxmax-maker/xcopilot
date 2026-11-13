"""/cost: расход на модели и X за сегодня и месяц, остаток дневного лимита."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from xcopilot.bot import texts
from xcopilot.bot.context import AccountCtx, Recorder
from xcopilot.bot.deps import BotDeps
from xcopilot.db.repos import BudgetsRepo, UsageRepo


def build_router() -> Router:
    """Новый роутер (экземпляр нельзя подключить к двум диспетчерам)."""
    router = Router(name="cost")

    def _local_bounds(now: datetime, tz: ZoneInfo) -> tuple[datetime, datetime, datetime]:
        """Начало суток, начало следующих суток и начало месяца по поясу аккаунта."""
        local = now.astimezone(tz)
        day = local.replace(hour=0, minute=0, second=0, microsecond=0)
        nxt = (day + timedelta(days=1)).replace(tzinfo=None).replace(tzinfo=tz)
        month = day.replace(day=1)
        return day, nxt, month

    @router.message(Command("cost"))
    async def cmd_cost(
        message: Message, deps: BotDeps, acc: AccountCtx | None, rec: Recorder
    ) -> None:
        await rec("cost")
        if acc is None:
            await message.answer(texts.NO_ACCOUNT)
            return
        day, nxt, month = _local_bounds(deps.clock.now(), ZoneInfo(acc.tz))
        async with deps.uow_factory() as uow:
            usage = UsageRepo(uow.session, acc.id)
            budgets = BudgetsRepo(uow.session, acc.id)
            today_roles = await usage.llm_spend_by_role(day, nxt)
            today_x = await usage.x_spend(day, nxt)
            month_roles = await usage.llm_spend_by_role(month, nxt)
            month_x = await usage.x_spend(month, nxt)
            llm_row = await budgets.get_limit("llm", "day")
            x_row = await budgets.get_limit("x_api", "day")
        llm_limit = (
            llm_row.limit_micro_usd
            if llm_row is not None
            else round(deps.settings.llm_daily_limit_usd * 1_000_000)
        )
        x_limit = (
            x_row.limit_micro_usd
            if x_row is not None
            else round(deps.settings.x_daily_limit_usd * 1_000_000)
        )
        await message.answer(
            texts.cost_report(
                today_roles=today_roles,
                today_x=today_x,
                month_roles=month_roles,
                month_x=month_x,
                llm_remaining=max(llm_limit - sum(today_roles.values()), 0),
                llm_limit=llm_limit,
                x_remaining=max(x_limit - today_x, 0),
                x_limit=x_limit,
            )
        )

    return router
