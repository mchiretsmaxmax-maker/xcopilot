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
    assert sorted(build_providers(settings())) == ["codex_cli"]
    providers = build_providers(settings(deepseek_api_key="d", typesafe_api_key="t"))
    assert sorted(providers) == ["codex_cli", "deepseek", "jev"]
    assert all(p.name == name for name, p in providers.items())


def test_build_gateway() -> None:
    def no_uow() -> UnitOfWork:
        raise AssertionError("шлюз не должен открывать транзакцию при сборке")

    gw = build_gateway(settings(), no_uow, FakeClock())
    assert isinstance(gw, LLMGateway)


async def test_ping_without_keys_reports_each_role() -> None:
    from xcopilot.llm.models_config import ModelsConfig
    from xcopilot.llm.pricing import PricingTable

    lines: list[str] = []
    results = await ping({}, ModelsConfig.load(), PricingTable.load(), out=lines.append)
    assert [r.ok for r in results] == [
        False,
        False,
        False,
    ]  # codex_cli не подан, deepseek без ключа
    assert all("нет ключа" in line for line in lines)


def test_main_ping_without_keys_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("DEEPSEEK_API_KEY", "TYPESAFE_API_KEY"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("CODEX_CLI_PATH", "/missing/xcopilot-test-codex")
    # значение уходит в глобальную маску логов: только уникальная строка
    monkeypatch.setenv("BOT_TOKEN", "factory-test-bot-token-7f3a")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x/y")
    assert main(["ping"]) == 1


def test_codex_cli_provider_always_built_without_key() -> None:
    providers = build_providers(settings())
    assert list(providers) == ["codex_cli"]
    assert providers["codex_cli"].name == "codex_cli"


async def test_ping_with_shipped_roles_uses_codex_cli_and_deepseek() -> None:
    from tests.fakes.llm import FakeLLM
    from xcopilot.llm.models_config import ModelsConfig
    from xcopilot.llm.pricing import PricingTable

    providers = {
        "codex_cli": FakeLLM(name="codex_cli").push("pong"),
        "deepseek": FakeLLM(name="deepseek").push('{"answers": {"pong": {"p": 0.9}}}', "pong"),
    }
    lines: list[str] = []
    results = await ping(providers, ModelsConfig.load(), PricingTable.load(), out=lines.append)
    assert [(r.role.value, r.provider, r.ok) for r in results] == [
        ("writer", "codex_cli", True),
        ("judge", "deepseek", True),
        ("cheap", "deepseek", True),
    ]
    assert results[0].cost_micro_usd == 0
