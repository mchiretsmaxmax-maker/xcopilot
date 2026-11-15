"""Петля ворот качества на настоящей БД (этап 11, задачи 7–10, 12)."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.quality_stand import (
    CLEAN,
    CLEAN2,
    CLEAN3,
    GateStand,
    good,
    make_gate_stand,
    weak_voice,
)
from xcopilot.llm.errors import ProviderUnavailable

SF = async_sessionmaker[AsyncSession]


@pytest.fixture
async def gs(session_factory: SF) -> GateStand:
    return await make_gate_stand(session_factory)


async def test_lint_error_forces_rewrite_without_calling_judge(gs: GateStand) -> None:
    draft_id = await gs.draft("газ дешевеет, роллапы растут 🚀")  # паспорт: без эмодзи
    gs.rewriter.push(CLEAN)
    gs.judge.push(good())

    decision = await gs.run(draft_id)

    assert decision.outcome == "pass"
    assert len(gs.rewriter.calls) == 1
    assert len(gs.judge.calls) == 1  # судья видел только переписанный текст
    assert "🚀" not in gs.judge.calls[0].state and CLEAN in gs.judge.calls[0].state
    assert decision.revision == 2 and decision.rewrites == 1


async def test_rewrite_instructions_quote_found_fragment(gs: GateStand) -> None:
    draft_id = await gs.draft("это не просто апгрейд, а новая эпоха для всей сети")
    gs.rewriter.push(CLEAN)
    gs.judge.push(good())

    await gs.run(draft_id)

    instructions = gs.rewriter.calls[0]["instructions"]
    assert instructions
    assert any("не просто апгрейд, а" in line for line in instructions), instructions


async def test_loop_stops_after_two_rewrites_with_needs_owner(gs: GateStand) -> None:
    draft_id = await gs.draft(CLEAN)
    gs.judge.push(weak_voice(), weak_voice(), weak_voice())
    gs.rewriter.push(CLEAN2, CLEAN3)

    decision = await gs.run(draft_id)

    assert decision.outcome == "needs_owner"
    assert "max_rewrites" in decision.flags
    assert decision.rewrites == 2 and decision.revision == 3
    assert len(gs.judge.calls) == 3 and len(gs.rewriter.calls) == 2
    rows = await gs.rows("SELECT revision, status FROM drafts ORDER BY revision")
    assert [r[0] for r in rows] == [1, 2, 3]
    assert rows[-1][1] == "needs_owner"


async def test_loop_stops_early_on_pass(gs: GateStand) -> None:
    draft_id = await gs.draft(CLEAN)
    gs.judge.push(good())

    decision = await gs.run(draft_id)

    assert decision.outcome == "pass" and decision.rewrites == 0
    assert gs.rewriter.calls == []
    rows = await gs.rows("SELECT status FROM drafts WHERE id = :d", d=draft_id)
    assert rows[0][0] == "judged"


async def test_rewrite_creates_new_revision_linked_to_parent(gs: GateStand) -> None:
    draft_id = await gs.draft(CLEAN)
    gs.judge.push(weak_voice(), good())
    gs.rewriter.push(CLEAN2)

    decision = await gs.run(draft_id)

    rows = await gs.rows(
        "SELECT id, revision, parent_draft_id, run_id, variant_no, text, status FROM drafts"
        " ORDER BY revision"
    )
    assert len(rows) == 2
    first, second = rows
    assert second.parent_draft_id == first.id == draft_id
    assert second.run_id == first.run_id and second.variant_no == first.variant_no
    assert second.text == CLEAN2 and second.status == "judged"
    assert decision.draft_id == second.id
    assert await gs.count(
        "events", "type = 'draft.rewritten' AND entity_id = :d", d=second.id
    ) == 1  # fmt: skip


async def test_fallback_judge_sets_flag(gs: GateStand) -> None:
    draft_id = await gs.draft(CLEAN)
    gs.judge.push(good(fallback=True))

    decision = await gs.run(draft_id)

    assert decision.outcome == "pass"
    assert "fallback_judge" in decision.flags
    assert decision.verdict is not None and decision.verdict.fallback is True


async def test_no_judge_available_passes_lint_only_with_flag(
    gs: GateStand, session_factory: SF
) -> None:
    draft_id = await gs.draft(CLEAN)
    gs.judge.push(ProviderUnavailable("судья и резерв недоступны"))

    decision = await gs.run(draft_id)

    assert decision.outcome == "pass" and "lint_only" in decision.flags
    assert decision.verdict is None
    # без судьи вообще (режим «только линтер»)
    bare = await make_gate_stand(session_factory, no_judge=True)
    other = await bare.draft(CLEAN2)
    assert (await bare.run(other)).flags == ["lint_only"]


async def test_gate_stores_reports_and_emits_events(gs: GateStand) -> None:
    draft_id = await gs.draft("газ дешевеет, роллапы растут 🚀")
    gs.rewriter.push(CLEAN)
    gs.judge.push(good())

    decision = await gs.run(draft_id)

    first = await gs.rows("SELECT lint_report, judge_verdict FROM drafts WHERE id = :d", d=draft_id)
    report = first[0][0]
    assert any(f["rule"] == "emoji" for f in report["findings"])
    assert first[0][1] is None  # судья не вызывался
    final = await gs.rows(
        "SELECT lint_report, judge_verdict FROM drafts WHERE id = :d", d=decision.draft_id
    )
    assert final[0][0]["findings"] == [] or all(
        f["level"] == "warn" for f in final[0][0]["findings"]
    )
    assert final[0][1]["voice_match"]["score"] == 2.6
    types = [
        r[0]
        for r in await gs.rows("SELECT type FROM events WHERE entity_type = 'draft' ORDER BY ts")
    ]
    for expected in ("draft.lint_failed", "draft.rewritten", "draft.judged", "draft.gate_decided"):
        assert expected in types, types
    lint_failed = await gs.rows(
        "SELECT props FROM events WHERE type = 'draft.lint_failed' AND entity_id = :d", d=draft_id
    )
    assert lint_failed[0][0]["rule"] == "emoji"


async def test_gate_is_idempotent_per_revision(gs: GateStand) -> None:
    draft_id = await gs.draft(CLEAN)
    gs.judge.push(good())

    first = await gs.run(draft_id)
    events = await gs.count("events")
    second = await gs.run(draft_id)

    assert second.outcome == first.outcome == "pass"
    assert second.draft_id == first.draft_id
    assert len(gs.judge.calls) == 1
    assert await gs.count("events") == events
    assert await gs.count("drafts") == 1


async def test_applied_flags_recorded_for_final_revision(gs: GateStand) -> None:
    used = await gs.ws.knowledge_item("Начинай с вывода, детали потом")
    unused = await gs.ws.knowledge_item("Одна мысль на строку")
    gs.ws.knowledge.items = [used, unused]
    draft_id = await gs.draft(CLEAN)
    gs.detector.applied = {used.item_id}
    gs.judge.push(weak_voice(), good())
    gs.rewriter.push(CLEAN2)

    decision = await gs.run(draft_id)

    final = decision.draft_id
    assert final != draft_id
    rows = await gs.rows(
        "SELECT item_id, applied, checked_by, checked_at FROM knowledge_applications"
        " WHERE draft_id = :d",
        d=final,
    )
    marks = {r.item_id: r.applied for r in rows}
    assert marks == {used.item_id: True, unused.item_id: False}
    assert all(r.checked_by == "rubric" and r.checked_at is not None for r in rows)
    assert gs.detector.texts == [CLEAN2]  # детекторы — только на финальной ревизии
    root = await gs.rows(
        "SELECT applied FROM knowledge_applications WHERE draft_id = :d", d=draft_id
    )
    assert root and all(r.applied is None for r in root)
