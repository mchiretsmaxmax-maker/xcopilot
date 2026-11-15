"""Доп. интеграционные тесты ворот (не из red-набора): переписчик через шлюз, класс C из БД,
чужой материал из базы, рубрики «Мозга», очередь, сбои писателя и судьи, пороги аккаунта."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, cast

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.quality_stand import (
    CLEAN,
    CLEAN2,
    GateStand,
    ScriptedJudge,
    good,
    judge_answers,
    make_gate_stand,
)
from tests.writer_stand import full_prompt
from xcopilot.db.uow import UnitOfWork
from xcopilot.jobs.handlers import JobDeps, build_registry
from xcopilot.jobs.queue import JobQueue
from xcopilot.jobs.worker import Worker
from xcopilot.llm.errors import ProviderUnavailable
from xcopilot.llm.schemas import NoulA, ScoreA, Verdict
from xcopilot.quality.attribution import NoDetector, mark_applied
from xcopilot.quality.brain_detector import BrainRubricDetector
from xcopilot.quality.jobs import QUALITY_GATE_KIND, gate_idempotency_key, gate_payload
from xcopilot.quality.knowledge import DbClassCRules, NoClassC
from xcopilot.quality.loop import GatewayRewriter, QualityGate
from xcopilot.retrieval.context_pack import ContextPack, TaskSpec

SF = async_sessionmaker[AsyncSession]
FOREIGN = (
    "газ в эфире подешевел в десять раз после апгрейда, и теперь роллапы забирают всех "
    "новых пользователей у старых сетей"
)


@pytest.fixture
async def gs(session_factory: SF) -> GateStand:
    return await make_gate_stand(session_factory)


async def _sql(gs: GateStand, sql: str, **params: Any) -> None:
    async with gs.ws.sf() as s:
        await s.execute(text(sql), params)
        await s.commit()


async def test_gateway_rewriter_uses_rewrite_prompt(gs: GateStand) -> None:
    draft_id = await gs.draft(CLEAN)
    gs.ws.writer.push(json.dumps({"text": CLEAN2, "parts": []}, ensure_ascii=False))
    task = TaskSpec(format="post", lang="ru", topic="газ в l2")
    pack = ContextPack(task=task, budget_tokens=6000)
    out = await GatewayRewriter(gs.ws.gateway).rewrite(
        account_id=gs.account_id, draft_id=draft_id, text=CLEAN, parts=[], format="post",
        instructions=["Убери эмодзи «🚀»."], pack=pack,
    )  # fmt: skip
    assert out.text == CLEAN2
    prompt = full_prompt(gs.ws.writer.calls[-1])
    assert "Убери эмодзи «🚀»." in prompt and CLEAN in prompt and "газ в l2" in prompt


async def test_class_c_from_db_become_warnings(gs: GateStand) -> None:
    await _sql(
        gs,
        "INSERT INTO knowledge_items (account_id, kind, layer, element, statement, guidance,"
        " risk_class, origin, status) VALUES (:a, 'anti_pattern', 'humanness', 'general',"
        " 'Не используй слово «транзакциям» без конкретики',"
        " 'Пиши конкретный пример вместо общего слова', 'C', 'seed', 'active')",
        a=gs.account_id,
    )
    async with gs.ws.uow() as uow:
        rules = await DbClassCRules().load(uow.session, gs.account_id, "ru")
        assert await NoClassC().load(uow.session, gs.account_id, "ru") == []
    assert [r.phrases for r in rules] == [("транзакциям",)]
    draft_id = await gs.draft(CLEAN)
    gs.judge.push(good())
    decision = await gs.run(draft_id)
    assert decision.outcome == "pass"
    assert decision.lint is not None
    assert [f.rule for f in decision.lint.warnings] == ["knowledge_c"]


async def test_foreign_material_from_db_blocks_copy(gs: GateStand) -> None:
    await gs.ws.foreign_post(FOREIGN, analysis=False)
    copy = (
        "за месяц я понял одно: роллапы забирают всех новых пользователей у старых сетей, пока я"
        " спокойно читаю документацию по мостам и не спешу"
    )
    draft_id = await gs.draft(copy)
    gs.rewriter.push(CLEAN)
    gs.judge.push(good())
    decision = await gs.run(draft_id)
    assert decision.revision == 2
    first = await gs.rows("SELECT lint_report FROM drafts WHERE id = :d", d=draft_id)
    assert any(f["rule"] == "foreign_similarity" for f in first[0][0]["findings"])
    assert any("своими словами" in i for i in gs.rewriter.calls[0]["instructions"])


async def test_writer_provider_down_flagged(session_factory: SF) -> None:
    gs = await make_gate_stand(session_factory)

    class Down:
        async def rewrite(self, **_: Any) -> Any:
            raise ProviderUnavailable("писатель недоступен")

    gate = QualityGate(uow_factory=gs.ws.uow, judge=gs.judge, rewriter=Down(), clock=gs.ws.clock)
    draft_id = await gs.draft("газ дешевеет 🚀")
    decision = await gate.run_gate(gs.account_id, draft_id)
    assert decision.outcome == "needs_owner" and "writer_unavailable" in decision.flags


async def test_broken_judge_answer_means_lint_only(gs: GateStand) -> None:
    broken = judge_answers()
    del broken["hook"]
    gs.judge.push(Verdict(answers=broken, judge="j"))
    decision = await gs.run(await gs.draft(CLEAN))
    assert decision.outcome == "pass" and decision.flags == ["lint_only"]


async def test_account_threshold_override_applied(gs: GateStand) -> None:
    await _sql(
        gs,
        "UPDATE accounts SET settings = CAST(:s AS jsonb) WHERE id = :a",
        s=json.dumps({"thresholds": {"voice_match": 2.9}}),
        a=gs.account_id,
    )
    gs.judge.push(good(), good(), good())
    gs.rewriter.push(CLEAN2, CLEAN)
    decision = await gs.run(await gs.draft(CLEAN))
    assert decision.outcome == "needs_owner" and decision.rewrites == 2


async def test_needs_owner_on_unsure_and_card_line(gs: GateStand) -> None:
    unsure = ScoreA(score=2.2, confidence=0.3, probabilities={2: 1.0})
    gs.judge.push(Verdict(answers=judge_answers(voice_match=unsure), judge="j"))
    draft_id = await gs.draft(CLEAN)
    decision = await gs.run(draft_id)
    assert decision.outcome == "needs_owner" and decision.flags == ["judge_unsure"]
    status = await gs.rows("SELECT status FROM drafts WHERE id = :d", d=draft_id)
    assert status[0][0] == "needs_owner"
    again = await gs.run(draft_id)  # восстановление из БД
    assert again.verdict is not None and again.verdict.voice_match.confidence == 0.3


async def test_quality_gate_job_through_worker(gs: GateStand) -> None:
    queue = JobQueue(gs.ws.uow, gs.ws.clock)
    nothing = cast(Any, object())
    deps = JobDeps(
        uow_factory=gs.ws.uow, clock=gs.ws.clock, queue=queue, ingest=nothing, analyzer=nothing,
        importer=nothing, writer=nothing, topics=nothing, brain=nothing, x_for=nothing,
        notifier=nothing, bot=nothing, quality=gs.gate,
    )  # fmt: skip
    worker = Worker(queue, build_registry(deps))
    a, b = await gs.draft(CLEAN), await gs.draft(CLEAN2)
    job_id = await queue.enqueue(QUALITY_GATE_KIND, gate_payload([a, b]), account_id=gs.account_id,
                                 idempotency_key=gate_idempotency_key([b, a]))  # fmt: skip
    gs.judge.push(good(), good())
    assert await worker.drain() == 1
    done = await queue.get(job_id)
    assert done is not None and done.status == "done"
    assert await gs.count("drafts", "status = 'judged'") == 2
    bad = await queue.enqueue(QUALITY_GATE_KIND, {"draft_ids": ["нет"]}, account_id=gs.account_id)
    await worker.drain()
    failed = await queue.get(bad)
    assert failed is not None and failed.status == "dead"  # неверный payload — без повторов
    bare = Worker(queue, build_registry(replace(deps, quality=None)))
    missing = await queue.enqueue(QUALITY_GATE_KIND, gate_payload([a]), account_id=gs.account_id)
    await bare.drain()
    not_built = await queue.get(missing)
    assert not_built is not None and not_built.status != "done"


async def test_mark_applied_without_rows_is_zero(gs: GateStand) -> None:
    draft_id = await gs.draft(CLEAN)
    async with UnitOfWork(gs.ws.sf) as uow:
        n = await mark_applied(uow.session, gs.account_id, draft_id, detector=NoDetector(),
                               now=gs.ws.clock.now())  # fmt: skip
    assert n == 0


async def test_brain_rubric_detector_marks_applied(gs: GateStand) -> None:
    used = await gs.ws.knowledge_item("Начинай с вывода, детали потом")
    unsure = await gs.ws.knowledge_item("Одна мысль на строку")
    plain = await gs.ws.knowledge_item("Без детектора")
    rubric = {"type": "rubric", "on": "first_line", "question": "Первая строка сразу даёт вывод"}
    full = {"type": "rubric", "on": "full_text", "question": "В тексте одна мысль на строку"}
    for item, det in ((used.item_id, rubric), (unsure.item_id, full)):
        await _sql(
            gs,
            "UPDATE knowledge_items SET detector = CAST(:d AS jsonb), status = 'active'"
            " WHERE id = :i",
            d=json.dumps(det, ensure_ascii=False),
            i=item,
        )
    llm = ScriptedJudge()
    llm.push(
        Verdict(answers={"k0": NoulA(p=0.9)}, judge="j"),
        Verdict(answers={"k0": NoulA(p=0.5)}, judge="j"),
    )
    detector = BrainRubricDetector(llm, gs.ws.clock)  # type: ignore[arg-type]
    async with UnitOfWork(gs.ws.sf) as uow:
        hits = await detector.detect(
            uow.session, gs.account_id, CLEAN, [used.item_id, unsure.item_id, plain.item_id]
        )
        assert await detector.detect(uow.session, gs.account_id, CLEAN, [plain.item_id]) == {}
    assert set(hits) == {used.item_id}
    assert hits[used.item_id].applied is True and hits[used.item_id].by == "rubric"
    no = ScriptedJudge().push(Verdict(answers={"k0": NoulA(p=0.1)}, judge="j"))
    async with UnitOfWork(gs.ws.sf) as uow:
        miss = await BrainRubricDetector(no, gs.ws.clock).detect(  # type: ignore[arg-type]
            uow.session, gs.account_id, CLEAN, [used.item_id]
        )
    assert miss[used.item_id].applied is False
