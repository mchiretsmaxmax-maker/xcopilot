"""Live-проверка трёх провайдеров на настоящих ключах (`pytest -m live`, вручную и ночью).

Без ключей тест падает с понятным сообщением — в CI он не запускается (`-m 'not live'`).
"""

import pytest

from xcopilot.config import load_settings
from xcopilot.llm.cli import ping
from xcopilot.llm.factory import build_providers
from xcopilot.llm.models_config import ModelsConfig
from xcopilot.llm.pricing import PricingTable
from xcopilot.llm.roles import Role


@pytest.mark.live
async def test_live_each_role_roundtrip() -> None:
    settings = load_settings()
    missing = [
        name for name, value in (("DEEPSEEK_API_KEY", settings.deepseek_api_key),) if not value
    ]
    assert not missing, f"для live-проверки нужны ключи: {', '.join(missing)}"
    lines: list[str] = []
    results = await ping(
        build_providers(settings), ModelsConfig.load(), PricingTable.load(), out=lines.append
    )
    assert [r.role.value for r in results] == ["writer", "judge", "cheap"]
    for r in results:
        assert r.ok, f"{r.role}: {r.error}"
        assert r.usage.input_tokens > 0
        assert (r.cost_micro_usd == 0) == (r.role is Role.WRITER)


@pytest.mark.live
async def test_live_codex_cli_roundtrip() -> None:
    """Один синтетический запрос через реального писателя из конфигурации приложения."""
    results = await ping(
        build_providers(load_settings()),
        ModelsConfig.load(),
        PricingTable.load(),
        roles=(Role.WRITER,),
    )
    [result] = results
    assert result.ok, result.error
    assert result.answer.lower() == "pong"
    assert result.usage.output_tokens > 0
    assert result.cost_micro_usd == 0
