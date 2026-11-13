"""LLMGateway: вызов модели по роли с проверкой ответа, журналом, бюджетом и заменами."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from xcopilot.clock import Clock
from xcopilot.db.uow import UnitOfWork
from xcopilot.llm.budget import BudgetGuard
from xcopilot.llm.models_config import ModelsConfig
from xcopilot.llm.pricing import PricingTable
from xcopilot.llm.prompts.registry import PromptRegistry
from xcopilot.llm.providers.base import DecisionProvider, TextProvider
from xcopilot.llm.resilience import RetryPolicy, Sleep
from xcopilot.llm.roles import Role
from xcopilot.llm.schemas import Question, Verdict


class LLMGateway:
    def __init__(
        self,
        *,
        models: ModelsConfig,
        pricing: PricingTable,
        providers: Mapping[str, TextProvider | DecisionProvider],
        prompts: PromptRegistry,
        budget: BudgetGuard,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        retry: RetryPolicy | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        raise NotImplementedError

    async def generate[T: BaseModel](
        self,
        *,
        role: Role,
        prompt_key: str,
        variables: Mapping[str, Any],
        schema: type[T],
        account_id: UUID,
        purpose: str,
        entity: tuple[str, UUID] | None = None,
    ) -> T:
        raise NotImplementedError

    async def decide(
        self,
        *,
        state: str,
        questions: Mapping[str, Question],
        account_id: UUID,
        purpose: str,
        entity: tuple[str, UUID] | None = None,
    ) -> Verdict:
        raise NotImplementedError
