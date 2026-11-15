"""Eval ворот качества на настоящем судье (`pytest -m eval`, ночью; в CI не идёт).

Набор: 30 AI-текстов и 30 постов владельца (`tests/fixtures/quality/gate_eval_set.yaml`).
Ворота (линтер + судья + политика) ловят ≥ 80% первых и бракуют ≤ 20% вторых.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeClock
from tests.retrieval_stand import Kb, ScriptedEmbedder, default_passport_body
from tests.writer_stand import OWN_POSTS
from xcopilot.config import load_settings
from xcopilot.db.uow import UnitOfWork
from xcopilot.llm.factory import build_gateway
from xcopilot.llm.prompts.registry import PromptRegistry
from xcopilot.quality.judge import judge
from xcopilot.quality.lint.engine import LintContext, LintDraft, Linter
from xcopilot.quality.policy import Thresholds, decide
from xcopilot.retrieval.context_pack import ContextPack, PackChunk, TaskSpec
from xcopilot.style.passport import HardRules

SF = async_sessionmaker[AsyncSession]
SET = Path(__file__).parents[1] / "fixtures" / "quality" / "gate_eval_set.yaml"


def eval_set() -> dict[str, list[str]]:
    data: dict[str, Any] = yaml.safe_load(SET.read_text())
    return {"ai": list(data["ai"]), "owner": list(data["owner"])}


@pytest.mark.eval
async def test_eval_gate_on_seeded_set(session_factory: SF) -> None:
    data = eval_set()
    assert len(data["ai"]) == 30 and len(data["owner"]) == 30
    settings = load_settings()
    acc = await Kb(session_factory, ScriptedEmbedder()).account("gate_eval")
    async with UnitOfWork(session_factory) as u:
        await PromptRegistry().sync(u.session)
    gateway = build_gateway(settings, lambda: UnitOfWork(session_factory), FakeClock())
    pack = ContextPack(
        task=TaskSpec(format="post", lang="ru", topic="крипта и l2"),
        budget_tokens=6000,
        passport=default_passport_body(),
        hard_rules={"casing": "lower", "emoji": "none", "max_len": 240},
        examples=[PackChunk(chunk_id=uuid.uuid4(), kind="own_post", text=t) for t in OWN_POSTS],
    )
    ctx = LintContext(hard_rules=HardRules(casing="any", emoji="none", max_len=280), lang="ru")
    linter, thresholds = Linter(), Thresholds.load()

    async def blocked(text: str) -> bool:
        draft = LintDraft(text=text)
        report = linter.run(draft, ctx)
        if not report.ok:
            return True
        verdict = await judge(draft, pack, port=gateway, account_id=acc)
        return decide(report, verdict, thresholds).outcome != "pass"

    caught = [await blocked(t) for t in data["ai"]]
    rejected = [await blocked(t) for t in data["owner"]]
    assert sum(caught) / len(caught) >= 0.8, caught
    assert sum(rejected) / len(rejected) <= 0.2, rejected
