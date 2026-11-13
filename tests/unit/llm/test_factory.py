"""Сборка провайдеров и шлюза из настроек; CLI без ключей."""

import pytest

from tests.fakes import FakeClock
from xcopilot.config import Settings
from xcopilot.db.uow import UnitOfWork
from xcopilot.llm.cli import main, ping
from xcopilot.llm.factory import build_gateway, build_providers
from xcopilot.llm.gateway import LLMGateway


def settings(**keys: str) -> Settings:
    return Settings(bot_token="t", database_url="postgresql+asyncpg://x/y", **keys)  # type: ignore[arg-type]


def test_build_providers_only_with_keys() -> None:
    assert sorted(build_providers(settings())) == ["claude_cli"]
    providers = build_providers(
        settings(anthropic_api_key="a", deepseek_api_key="d", typesafe_api_key="t")
    )
    assert sorted(providers) == ["anthropic", "claude_cli", "deepseek", "jev"]
    assert all(p.name == name for name, p in providers.items())


def test_build_gateway() -> None:
    def no_uow() -> UnitOfWork:
        raise AssertionError("шлюз не должен открывать транзакцию при сборке")

    gw = build_gateway(settings(anthropic_api_key="a"), no_uow, FakeClock())
    assert isinstance(gw, LLMGateway)


async def test_ping_without_keys_reports_each_role() -> None:
    from xcopilot.llm.models_config import ModelsConfig
    from xcopilot.llm.pricing import PricingTable

    lines: list[str] = []
    results = await ping({}, ModelsConfig.load(), PricingTable.load(), out=lines.append)
    assert [r.ok for r in results] == [False, False, False]  # claude_cli не подан, deepseek без ключа
    assert all("нет ключа" in line for line in lines)


def test_main_ping_without_keys_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "TYPESAFE_API_KEY"):
        monkeypatch.setenv(name, "")
    # значение уходит в глобальную маску логов: только уникальная строка
    monkeypatch.setenv("BOT_TOKEN", "factory-test-bot-token-7f3a")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x/y")
    assert main(["ping"]) == 1


def test_claude_cli_provider_always_built_without_key() -> None:
    providers = build_providers(settings())
    assert list(providers) == ["claude_cli"]
    assert providers["claude_cli"].name == "claude_cli"


async def test_ping_with_shipped_roles_uses_claude_cli_and_deepseek() -> None:
    from tests.fakes.llm import FakeLLM
    from xcopilot.llm.models_config import ModelsConfig
    from xcopilot.llm.pricing import PricingTable

    providers = {
        "claude_cli": FakeLLM(name="claude_cli").push("pong"),
        "deepseek": FakeLLM(name="deepseek").push("pong", '{"answers": {"pong": {"p": 0.9}}}'),
    }
    lines: list[str] = []
    results = await ping(providers, ModelsConfig.load(), PricingTable.load(), out=lines.append)  # type: ignore[arg-type]
    assert [(r.role.value, r.provider, r.ok) for r in results] == [
        ("writer", "claude_cli", True),
        ("judge", "deepseek", True),
        ("cheap", "deepseek", True),
    ]
    assert results[0].cost_micro_usd == 0
