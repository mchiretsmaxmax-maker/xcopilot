"""Сборка провайдеров и шлюза из настроек; CLI без ключей."""

import pytest

from tests.fakes import FakeClock
from xcopilot.config import Settings
from xcopilot.db.uow import UnitOfWork
from xcopilot.llm import cli
from xcopilot.llm.factory import build_gateway, build_providers
from xcopilot.llm.gateway import LLMGateway


def settings(**keys: str) -> Settings:
    return Settings(bot_token="t", database_url="postgresql+asyncpg://x/y", **keys)  # type: ignore[arg-type]


def test_build_providers_only_with_keys() -> None:
    assert build_providers(settings()) == {}
    providers = build_providers(
        settings(anthropic_api_key="a", deepseek_api_key="d", typesafe_api_key="t")
    )
    assert sorted(providers) == ["anthropic", "deepseek", "jev"]
    assert [p.name for p in providers.values()] == ["anthropic", "deepseek", "jev"]


def test_build_gateway() -> None:
    def no_uow() -> UnitOfWork:
        raise AssertionError("шлюз не должен открывать транзакцию при сборке")

    gw = build_gateway(settings(anthropic_api_key="a"), no_uow, FakeClock())
    assert isinstance(gw, LLMGateway)


async def test_ping_without_keys_reports_each_role() -> None:
    from xcopilot.llm.models_config import ModelsConfig
    from xcopilot.llm.pricing import PricingTable

    lines: list[str] = []
    results = await cli.ping({}, ModelsConfig.load(), PricingTable.load(), out=lines.append)
    assert [r.ok for r in results] == [False, False, False]
    assert all("нет ключа" in line for line in lines)


def test_main_ping_without_keys_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "TYPESAFE_API_KEY"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("BOT_TOKEN", "t")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x/y")
    assert cli.main(["ping"]) == 1
