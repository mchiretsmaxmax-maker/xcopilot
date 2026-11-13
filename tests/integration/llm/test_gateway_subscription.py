"""Шлюз с писателем на подписке (claude_cli) и судьёй на DeepSeek."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from tests.fakes.llm import FakeLLM
from tests.integration.llm.conftest import Rig
from xcopilot.llm.errors import ProviderUnavailable, TransientProviderError
from xcopilot.llm.models_config import ModelsConfig
from xcopilot.llm.roles import Role
from xcopilot.llm.schemas import NoulA, NoulQ

SHIPPED = ModelsConfig.load()
OK = json.dumps({"answers": {"q": {"p": 0.8}}})


class Post(BaseModel):
    text: str


async def test_judge_on_deepseek_returns_verdict_as_primary(rig: Rig) -> None:
    rig.cheap.push(OK)
    gw = rig.gateway(models=SHIPPED, providers={"deepseek": rig.cheap, "claude_cli": rig.writer})
    verdict = await gw.decide(
        state="Пост", questions={"q": NoulQ(instructions="Про кофе?")},
        account_id=rig.account_id, purpose="draft.judge",
    )  # fmt: skip
    assert verdict.answers == {"q": NoulA(p=0.8)}
    assert verdict.judge == "deepseek-flash"
    assert verdict.fallback is False
    [row] = await rig.rows("SELECT * FROM llm_calls WHERE account_id = :a", a=rig.account_id)
    assert (row.role, row.provider, row.status) == ("judge", "deepseek", "ok")


async def test_judge_on_deepseek_unavailable_raises(rig: Rig) -> None:
    rig.cheap.push(*[TransientProviderError("deepseek", status=503)] * 4)
    gw = rig.gateway(models=SHIPPED, providers={"deepseek": rig.cheap, "claude_cli": rig.writer})
    with pytest.raises(ProviderUnavailable):
        await gw.decide(
            state="П", questions={"q": NoulQ(instructions="?")},
            account_id=rig.account_id, purpose="draft.judge",
        )  # fmt: skip


async def test_writer_on_claude_cli_costs_nothing_and_logs_tokens(rig: Rig) -> None:
    writer = FakeLLM(name="claude_cli", clock=rig.clock, latency=2.0)
    writer.push('{"text": "привет"}')
    gw = rig.gateway(models=SHIPPED, providers={"claude_cli": writer, "deepseek": rig.cheap})
    post = await gw.generate(
        role=Role.WRITER, prompt_key="test.post", variables={"topic": "кофе"}, schema=Post,
        account_id=rig.account_id, purpose="draft.generate",
    )  # fmt: skip
    assert post.text == "привет"
    [row] = await rig.rows("SELECT * FROM llm_calls WHERE account_id = :a", a=rig.account_id)
    assert (row.provider, row.cost_micro_usd, row.input_tokens, row.output_tokens) == (
        "claude_cli", 0, 100, 20,
    )  # fmt: skip
    assert row.latency_ms == 2000


async def test_subscription_limit_becomes_provider_unavailable(rig: Rig, tmp_path: Path) -> None:
    from xcopilot.llm.errors import SubscriptionLimit

    writer = FakeLLM(name="claude_cli", clock=rig.clock)
    writer.push(*[SubscriptionLimit("claude_cli", kind="limit")] * 4)
    gw = rig.gateway(models=SHIPPED, providers={"claude_cli": writer, "deepseek": rig.cheap})
    with pytest.raises(ProviderUnavailable):
        await gw.generate(
            role=Role.WRITER, prompt_key="test.post", variables={"topic": "к"}, schema=Post,
            account_id=rig.account_id, purpose="draft.generate",
        )  # fmt: skip
