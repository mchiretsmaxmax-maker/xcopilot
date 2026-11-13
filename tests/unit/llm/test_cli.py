"""`python -m xcopilot.llm ping`: ответ каждой роли, токены и стоимость."""

from pathlib import Path

from tests.fakes.llm import FakeJudge, FakeLLM, yes_answers
from xcopilot.llm.cli import main, ping
from xcopilot.llm.errors import TransientProviderError
from xcopilot.llm.models_config import ModelsConfig
from xcopilot.llm.pricing import PricingTable
from xcopilot.llm.providers.base import DecisionProvider, TextProvider

LEGACY = Path(__file__).parents[2] / "fixtures" / "models_anthropic_jev.toml"


async def test_ping_reports_each_role() -> None:
    providers: dict[str, TextProvider | DecisionProvider] = {
        "anthropic": FakeLLM(name="anthropic").push("pong"),
        "deepseek": FakeLLM(name="deepseek").push(TransientProviderError("deepseek", status=503)),
        "jev": FakeJudge(name="jev").push(yes_answers(["pong"])),
    }
    lines: list[str] = []
    results = await ping(
        providers, ModelsConfig.load(LEGACY), PricingTable.load(), out=lines.append
    )
    assert [(r.role.value, r.provider, r.ok) for r in results] == [
        ("writer", "anthropic", True),
        ("judge", "jev", True),
        ("cheap", "deepseek", False),
    ]
    assert results[0].answer == "pong" and results[0].cost_micro_usd > 0
    assert results[1].answer == "pong: p=0.90"
    text = "\n".join(lines)
    assert "writer" in text and "claude-opus-5-5" in text and "pong" in text
    assert "in=100 out=20" in text and "$" in text
    assert "cheap" in text and "TransientProviderError 503" in text


def test_main_rejects_unknown_command() -> None:
    assert main(["nope"]) == 2
