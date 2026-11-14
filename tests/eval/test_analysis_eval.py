"""Eval: согласие разбора с ручной разметкой (`pytest -m eval`, ночью; в CI не идёт).

Этап 7A: на 40 постах, размеченных вручную, `hook_type` и `structure` совпадают с разметкой
не реже 70%. Нужен настоящий ключ DeepSeek в .env.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.brain_stand import make_brain_stand
from tests.fakes import FakeClock
from xcopilot.brain.analyze import Analyzer
from xcopilot.config import load_settings
from xcopilot.db.uow import UnitOfWork
from xcopilot.llm.factory import build_gateway

SF = async_sessionmaker[AsyncSession]
DATA = Path(__file__).resolve().parents[1] / "fixtures" / "brain" / "eval_analysis_40.json"


@pytest.mark.eval
async def test_eval_analysis_agreement(session_factory: SF) -> None:
    labelled = json.loads(DATA.read_text())
    assert len(labelled) == 40
    clock = FakeClock()
    stand = await make_brain_stand(session_factory, clock)

    def uow() -> UnitOfWork:
        return UnitOfWork(session_factory)

    analyzer = Analyzer(uow, build_gateway(load_settings(), uow, clock), clock)
    hooks = structures = 0
    for item in labelled:
        sid = await stand.material(item["text"])
        result = await analyzer.analyze(stand.account_id, sid)
        assert result is not None
        hooks += result.hook_type == item["hook_type"]
        structures += result.structure == item["structure"]
    assert hooks / 40 >= 0.7, f"hook_type: {hooks}/40"
    assert structures / 40 >= 0.7, f"structure: {structures}/40"
