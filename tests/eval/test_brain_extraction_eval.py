"""Eval 9A: качество извлечения на настоящей модели (`pytest -m eval`, ночью; в CI не идёт).

Три эталонные пачки (`tests/fixtures/brain/eval_batches.json`): разборы и ручная разметка —
какие закономерности в пачке полезны. Кандидат полезен, если его детектор или утверждение
попадает в размеченную закономерность. Порог: ≥ 60% полезных; ни одного с именем, ссылкой
или чужой манерой (слой voice из чужих материалов).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.brain_core_stand import analysis_fields, material, new_account, new_author
from xcopilot.brain import extract
from xcopilot.brain.context import BrainContext
from xcopilot.clock import SystemClock
from xcopilot.config import load_settings
from xcopilot.db.uow import UnitOfWork
from xcopilot.llm.factory import build_gateway

BATCHES = Path(__file__).parents[1] / "fixtures" / "brain" / "eval_batches.json"


def useful(cand: Any, labels: list[dict[str, str]]) -> bool:
    det = json.dumps(cand.detector.model_dump(), ensure_ascii=False)
    for label in labels:
        if label.get("path") and label["path"] in det and label.get("value", "") in det:
            return True
        if label.get("keyword") and label["keyword"].lower() in cand.statement.lower():
            return True
    return False


@pytest.mark.eval
async def test_eval_extraction_quality(session_factory: async_sessionmaker[AsyncSession]) -> None:
    settings = load_settings()
    data = json.loads(BATCHES.read_text(encoding="utf-8"))
    uow_factory = lambda: UnitOfWork(session_factory)  # noqa: E731
    gateway = build_gateway(settings, uow_factory, SystemClock())
    total = good = 0
    for spec in data["batches"]:
        async with UnitOfWork(session_factory) as uow:
            acc = await new_account(uow.session)
            srcs = [
                await material(
                    uow.session,
                    acc,
                    kind=spec["format"],
                    stance=m["stance"],
                    tier=m["tier"],
                    author_id=await new_author(uow.session, acc),
                    analysis=analysis_fields(format=spec["format"], **m["analysis"]),
                )
                for m in spec["materials"]
            ]
            ctx = BrainContext(
                session=uow.session, account_id=acc, clock=SystemClock(), llm=gateway
            )
            res = await extract.extract(
                ctx, extract.Batch(account_id=acc, format=spec["format"], materials=srcs)
            )
        foreign = all(m["stance"] != "own" for m in spec["materials"])
        for cand in res.candidates:
            total += 1
            assert not (foreign and cand.layer == "voice"), cand.statement
            good += useful(cand, spec["labels"])
    assert total > 0
    assert good / total >= 0.6
