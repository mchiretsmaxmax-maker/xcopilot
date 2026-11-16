"""Стенд эксплуатации (этап 15): аккаунт с владельцем, часы, бюджет и зависимости алертов."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeClock
from tests.integration.db.brain_helpers import account, user
from xcopilot.db.repos import AccountsRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.llm.budget import BudgetGuard
from xcopilot.ops.alerts import AlertDeps

SF = async_sessionmaker[AsyncSession]


@dataclass
class OwnerInbox:
    """Notifier: что ушло владельцу аккаунта."""

    sent: list[tuple[UUID, str]] = field(default_factory=list)

    async def notify_owner(self, account_id: UUID, text: str) -> None:
        self.sent.append((account_id, text))

    async def notify(
        self, account_id: UUID, text: str, keyboard: Any = None, roles: tuple[str, ...] = ("owner",)
    ) -> int:
        self.sent.append((account_id, text))
        return 1

    def texts(self) -> list[str]:
        return [t for _, t in self.sent]


@dataclass
class OpsStand:
    sf: SF
    clock: FakeClock
    account_id: UUID
    user_id: UUID
    budget: BudgetGuard
    breakers: dict[str, datetime | None]
    inbox: OwnerInbox

    def uow(self) -> UnitOfWork:
        return UnitOfWork(self.sf)

    @property
    def deps(self) -> AlertDeps:
        return AlertDeps(
            uow_factory=self.uow,
            clock=self.clock,
            budget=self.budget,
            open_since=lambda: dict(self.breakers),
        )

    async def exec(self, sql: str, **params: Any) -> None:
        async with self.sf() as s:
            await s.execute(text(sql), params)
            await s.commit()

    async def scalar(self, sql: str, **params: Any) -> Any:
        async with self.sf() as s:
            return (await s.execute(text(sql), params)).scalar()


@pytest.fixture
async def ops(session_factory: SF, clock: FakeClock) -> OpsStand:
    async with session_factory() as s:
        acc = await account(s, handle="ops_acc")
        uid = await user(s)
        await AccountsRepo(s).add_member(acc, uid, role="owner")
        await s.commit()

    def uow() -> UnitOfWork:
        return UnitOfWork(session_factory)

    budget = BudgetGuard(uow, clock, default_day_limits={"llm": 1_000_000, "x_api": 1_000_000})
    return OpsStand(
        sf=session_factory,
        clock=clock,
        account_id=acc,
        user_id=uid,
        budget=budget,
        breakers={},
        inbox=OwnerInbox(),
    )
