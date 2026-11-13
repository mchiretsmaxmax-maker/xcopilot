"""Сборка настоящих провайдеров и шлюза из настроек (ключи — только из Settings / .env)."""

from __future__ import annotations

from collections.abc import Callable

from xcopilot.clock import Clock
from xcopilot.config import Settings
from xcopilot.db.uow import UnitOfWork
from xcopilot.llm.budget import BudgetGuard
from xcopilot.llm.gateway import LLMGateway
from xcopilot.llm.models_config import ModelsConfig
from xcopilot.llm.pricing import PricingTable
from xcopilot.llm.prompts.registry import PromptRegistry
from xcopilot.llm.providers.anthropic import AnthropicProvider
from xcopilot.llm.providers.base import DecisionProvider, TextProvider
from xcopilot.llm.providers.deepseek import DeepSeekProvider
from xcopilot.llm.providers.jev import JevProvider

MICRO = 1_000_000


def build_providers(settings: Settings) -> dict[str, TextProvider | DecisionProvider]:
    """Провайдеры, для которых задан ключ; без ключа провайдера нет (роль недоступна)."""
    providers: dict[str, TextProvider | DecisionProvider] = {}
    if settings.anthropic_api_key:
        providers["anthropic"] = AnthropicProvider(settings.anthropic_api_key)
    if settings.deepseek_api_key:
        providers["deepseek"] = DeepSeekProvider(settings.deepseek_api_key)
    if settings.typesafe_api_key:
        providers["jev"] = JevProvider(settings.typesafe_api_key)
    return providers


def build_budget_guard(
    settings: Settings, uow_factory: Callable[[], UnitOfWork], clock: Clock
) -> BudgetGuard:
    """Страж бюджета: дневные лимиты по умолчанию — LLM_DAILY_LIMIT_USD и X_DAILY_LIMIT_USD."""
    return BudgetGuard(
        uow_factory,
        clock,
        default_day_limits={
            "llm": round(settings.llm_daily_limit_usd * MICRO),
            "x_api": round(settings.x_daily_limit_usd * MICRO),
        },
    )


def build_gateway(
    settings: Settings, uow_factory: Callable[[], UnitOfWork], clock: Clock
) -> LLMGateway:
    """Шлюз с конфигами из config/, встроенными промптами и дневными лимитами из настроек."""
    return LLMGateway(
        models=ModelsConfig.load(),
        pricing=PricingTable.load(),
        providers=build_providers(settings),
        prompts=PromptRegistry(),
        budget=build_budget_guard(settings, uow_factory, clock),
        uow_factory=uow_factory,
        clock=clock,
    )
