"""Live-проверка трёх провайдеров на настоящих ключах (`pytest -m live`, вручную и ночью).

Без ключей тест падает с понятным сообщением — в CI он не запускается (`-m 'not live'`).
"""

import pytest

from xcopilot.config import load_settings
from xcopilot.llm.cli import ping
from xcopilot.llm.factory import build_providers
from xcopilot.llm.models_config import ModelsConfig
from xcopilot.llm.pricing import PricingTable


@pytest.mark.live
async def test_live_each_role_roundtrip() -> None:
    settings = load_settings()
    missing = [
        name
        for name, value in (
            ("DEEPSEEK_API_KEY", settings.deepseek_api_key),
        )
        if not value
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
        assert r.cost_micro_usd > 0


@pytest.mark.live
async def test_live_claude_cli_roundtrip() -> None:
    """Настоящий `claude -p` на подписке: одна короткая фраза, стоимость 0."""
    from xcopilot.llm.providers.base import Message, TextRequest
    from xcopilot.llm.providers.claude_cli import ClaudeCliProvider

    result = await ClaudeCliProvider().complete(
        model="claude-opus-5-5",
        request=TextRequest(system=(), messages=(Message("user", "Ответь одним словом: pong"),)),
    )
    assert "pong" in result.text.lower()
    assert result.usage.output_tokens > 0
