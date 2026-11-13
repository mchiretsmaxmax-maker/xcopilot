"""Этап 6 (доп.): репозитории для X — журнал x_api_calls, настройки аккаунта."""

import uuid
from collections.abc import Callable
from datetime import date
from uuid import UUID

import pytest

from xcopilot.db.repos import AccountsRepo, XApiCallsRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.errors import NotFound


async def test_spent_on_day_sums_only_own_account_and_day(
    uow_factory: Callable[[], UnitOfWork], account_id: UUID
) -> None:
    day = date(2026, 11, 13)
    async with uow_factory() as uow:
        other = await AccountsRepo(uow.session).create(handle="other")
        for acc, d, cost in (
            (account_id, day, 5_000),
            (account_id, day, 1_000),
            (account_id, date(2026, 11, 12), 7_000),
            (other.id, day, 9_000),
        ):
            await XApiCallsRepo(uow.session, acc).log(
                endpoint="GET /2/tweets/:id",
                resources=1,
                cost_micro_usd=cost,
                utc_day=d,
                http_status=200,
                status="ok",
            )
    async with uow_factory() as uow:
        repo = XApiCallsRepo(uow.session, account_id)
        assert await repo.spent_on_day(day) == 6_000
        assert await repo.spent_on_day(date(2026, 1, 1)) == 0


async def test_update_settings_merges_and_rejects_unknown_account(
    uow_factory: Callable[[], UnitOfWork], account_id: UUID
) -> None:
    async with uow_factory() as uow:
        repo = AccountsRepo(uow.session)
        await repo.update_settings(account_id, a=1)
        acc = await repo.update_settings(account_id, b=True)
        assert acc.settings == {"a": 1, "b": True}
        with pytest.raises(NotFound):
            await repo.update_settings(uuid.uuid4(), a=1)
