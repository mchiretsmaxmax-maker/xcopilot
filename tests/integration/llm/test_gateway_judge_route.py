"""Судья для ответов (ветка feat/reply-gate-strict): `draft.judge.reply` → deepseek-v4-pro."""

from __future__ import annotations

import json

from tests.integration.llm.conftest import Rig
from xcopilot.llm.errors import TransientProviderError
from xcopilot.llm.models_config import ModelsConfig
from xcopilot.llm.schemas import NoulA, NoulQ

SHIPPED = ModelsConfig.load()
OK = json.dumps({"answers": {"q": {"p": 0.8}}})
Q = {"q": NoulQ(instructions="Про кофе?")}


async def test_reply_judge_goes_to_v4_pro(rig: Rig) -> None:
    rig.cheap.push(OK)
    gw = rig.gateway(models=SHIPPED, providers={"deepseek": rig.cheap, "claude_cli": rig.writer})
    verdict = await gw.decide(
        state="Ответ", questions=Q, account_id=rig.account_id, purpose="draft.judge.reply"
    )
    assert verdict.answers == {"q": NoulA(p=0.8)}
    assert verdict.judge == "deepseek-v4-pro" and verdict.fallback is False
    assert [c.model for c in rig.cheap.calls] == ["deepseek-v4-pro"]
    [row] = await rig.rows("SELECT * FROM llm_calls WHERE account_id = :a", a=rig.account_id)
    assert (row.role, row.provider, row.model, row.status) == (
        "judge", "deepseek", "deepseek-v4-pro", "ok",
    )  # fmt: skip


async def test_post_judge_stays_on_flash(rig: Rig) -> None:
    rig.cheap.push(OK)
    gw = rig.gateway(models=SHIPPED, providers={"deepseek": rig.cheap, "claude_cli": rig.writer})
    verdict = await gw.decide(
        state="Пост", questions=Q, account_id=rig.account_id, purpose="draft.judge"
    )
    assert verdict.judge == "deepseek-flash"
    assert [c.model for c in rig.cheap.calls] == ["deepseek-flash"]


async def test_reply_judge_unavailable_falls_back_to_base_model(rig: Rig) -> None:
    rig.cheap.push(*[TransientProviderError("deepseek", status=503)] * 4, OK)
    gw = rig.gateway(models=SHIPPED, providers={"deepseek": rig.cheap, "claude_cli": rig.writer})
    verdict = await gw.decide(
        state="Ответ", questions=Q, account_id=rig.account_id, purpose="draft.judge.reply"
    )
    assert verdict.judge == "deepseek-flash" and verdict.fallback is True
    assert rig.cheap.calls[-1].model == "deepseek-flash"


async def test_reply_judge_request_disables_thinking(rig: Rig) -> None:
    rig.cheap.push(OK, OK)
    gw = rig.gateway(models=SHIPPED, providers={"deepseek": rig.cheap, "claude_cli": rig.writer})
    await gw.decide(state="О", questions=Q, account_id=rig.account_id, purpose="draft.judge.reply")
    await gw.decide(state="П", questions=Q, account_id=rig.account_id, purpose="draft.judge")
    assert [c.request.thinking for c in rig.cheap.calls] == [False, None]
