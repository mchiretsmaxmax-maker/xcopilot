"""Стенд шлюза: настоящая БД, подделки провайдеров, FakeClock, повторы без пауз."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeClock
from tests.fakes.llm import FakeJudge, FakeLLM
from xcopilot.db.repos import AccountsRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.llm.budget import BudgetGuard
from xcopilot.llm.gateway import LLMGateway
from xcopilot.llm.models_config import ModelsConfig
from xcopilot.llm.pricing import PricingTable
from xcopilot.llm.prompts.registry import PACKAGED_DIR, PromptRegistry
from xcopilot.llm.providers.base import DecisionProvider, TextProvider
from xcopilot.llm.resilience import RetryPolicy

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)  # вторник, вне пика DeepSeek
DEFAULT_LIMITS = {"llm": 5_000_000, "x_api": 1_000_000}

TEST_PROMPT = """---
key: test.post
role: writer
cacheable: passport
---
Пиши как автор.
@@user
Напиши пост на тему: {{ topic }}
"""


async def no_sleep(_: float) -> None:
    return None


@dataclass
class Rig:
    sf: async_sessionmaker[AsyncSession]
    clock: FakeClock
    account_id: UUID
    writer: FakeLLM
    cheap: FakeLLM
    judge: FakeJudge
    prompts: PromptRegistry
    budget: BudgetGuard
    models: ModelsConfig = field(default_factory=ModelsConfig.load)

    def uow(self) -> UnitOfWork:
        return UnitOfWork(self.sf)

    def gateway(
        self,
        *,
        models: ModelsConfig | None = None,
        providers: dict[str, TextProvider | DecisionProvider] | None = None,
    ) -> LLMGateway:
        return LLMGateway(
            models=models or self.models,
            pricing=PricingTable.load(),
            providers=providers
            or {"anthropic": self.writer, "deepseek": self.cheap, "jev": self.judge},
            prompts=self.prompts,
            budget=self.budget,
            uow_factory=self.uow,
            clock=self.clock,
            retry=RetryPolicy(),
            sleep=no_sleep,
        )

    async def new_account(self, handle: str, tz: str = "UTC") -> UUID:
        async with self.uow() as uow:
            return (await AccountsRepo(uow.session).create(handle=handle, tz=tz)).id

    async def rows(self, sql: str, **params: Any) -> list[Any]:
        async with self.sf() as s:
            return list((await s.execute(text(sql), params)).all())


@pytest.fixture
def prompts_dir(tmp_path: Path) -> Path:
    d = tmp_path / "prompts"
    d.mkdir()
    (d / "test.post.md").write_text(TEST_PROMPT)
    return d


@pytest.fixture
async def rig(session_factory: async_sessionmaker[AsyncSession], prompts_dir: Path) -> Rig:
    clock = FakeClock(NOW)
    uow_factory: Callable[[], UnitOfWork] = lambda: UnitOfWork(session_factory)  # noqa: E731
    async with uow_factory() as uow:
        account_id = (await AccountsRepo(uow.session).create(handle="llm_rig", tz="UTC")).id
    return Rig(
        sf=session_factory,
        clock=clock,
        account_id=account_id,
        writer=FakeLLM(name="anthropic", clock=clock),
        cheap=FakeLLM(name="deepseek", clock=clock),
        judge=FakeJudge(name="jev"),
        prompts=PromptRegistry(PACKAGED_DIR, prompts_dir),
        budget=BudgetGuard(uow_factory, clock, default_day_limits=DEFAULT_LIMITS),
    )
