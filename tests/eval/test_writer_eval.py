"""Evals писателя на настоящей модели (`pytest -m eval`, ночью; в CI не идёт).

Нужны ключи в .env. Блокирует только регрессия больше 10% от baseline (ТЗ 1.0).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeClock
from tests.retrieval_stand import Kb, ScriptedEmbedder
from tests.writer_stand import OWN_POSTS, post_task
from xcopilot.config import load_settings
from xcopilot.db.uow import UnitOfWork
from xcopilot.llm.factory import build_gateway
from xcopilot.llm.prompts.registry import PromptRegistry
from xcopilot.llm.schemas import ScoreA, ScoreQ
from xcopilot.style.global_rules import read_seed
from xcopilot.writer.drafts import WriterService
from xcopilot.writer.schemas import GenerationRun

SF = async_sessionmaker[AsyncSession]
GOLDEN = Path(__file__).parents[1] / "fixtures" / "writer" / "golden_topics.yaml"
VOICE_BASELINE = 0.6  # доля шкалы; поднять после первой ночной оценки
FACT = "Комиссии в l2 упали на 90% после апгрейда"


def golden() -> list[dict[str, Any]]:
    data: dict[str, Any] = yaml.safe_load(GOLDEN.read_text())
    return list(data["topics"])


async def _service(sf: SF) -> tuple[WriterService, Any, Any]:
    settings = load_settings()
    embedder = ScriptedEmbedder()
    kb = Kb(sf, embedder)
    acc = await kb.account("writer_eval")
    await kb.passport(acc)
    for post in OWN_POSTS:
        await kb.chunk(acc, post)
    await kb.fact(acc, FACT)
    async with UnitOfWork(sf) as u:
        await PromptRegistry().sync(u.session)

    def uow() -> UnitOfWork:
        return UnitOfWork(sf)

    gateway = build_gateway(settings, uow, FakeClock())
    service = WriterService(uow_factory=uow, gateway=gateway, embedder=embedder, clock=FakeClock())
    return service, gateway, acc


async def _runs(sf: SF, limit: int | None = None) -> tuple[list[GenerationRun], Any, Any]:
    service, gateway, acc = await _service(sf)
    runs = [
        await service.generate(acc, post_task(t["topic"], fmt=t["format"]))
        for t in golden()[:limit]
    ]
    return runs, gateway, acc


@pytest.mark.eval
async def test_eval_voice_score_on_golden_topics(session_factory: SF) -> None:
    """Судья оценивает близость к голосу (0–4); средняя доля шкалы ≥ baseline."""
    runs, gateway, acc = await _runs(session_factory)
    question = ScoreQ(
        instructions="Насколько текст звучит как автор этих постов: " + " | ".join(OWN_POSTS),
        levels=["совсем не он", "скорее не он", "похоже", "очень похоже", "неотличимо"],
    )
    scores: list[float] = []
    for run in runs:
        for draft in run.drafts:
            verdict = await gateway.decide(
                state=draft.text, questions={"voice": question}, account_id=acc,
                purpose="eval.voice",
            )  # fmt: skip
            answer = verdict.answers["voice"]
            assert isinstance(answer, ScoreA)
            scores.append(answer.score / 4)
    assert sum(scores) / len(scores) >= VOICE_BASELINE * 0.9


@pytest.mark.eval
async def test_eval_no_banned_phrases(session_factory: SF) -> None:
    """Ни один черновик не содержит фраз анти-AI пакета и запретов паспорта."""
    runs, _, _ = await _runs(session_factory, limit=5)
    banned = [
        q.lower() for r in read_seed() if r.kind == "dont" for q in re.findall(r"«([^»]+)»", r.text)
    ]
    assert banned
    for run in runs:
        for draft in run.drafts:
            low = draft.text.lower()
            assert not [b for b in banned if b and b in low], draft.text


@pytest.mark.eval
async def test_eval_number_grounding(session_factory: SF) -> None:
    """Каждая цифра в черновике есть в фактах пакета (политика цифр)."""
    runs, _, _ = await _runs(session_factory, limit=5)
    allowed = set(re.findall(r"\d+", FACT))
    for run in runs:
        for draft in run.drafts:
            numbers = set(re.findall(r"\d+", draft.text))
            assert numbers <= allowed, draft.text
