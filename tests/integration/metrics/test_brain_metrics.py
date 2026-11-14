# ruff: noqa: E501
"""ТЗ v3.1, дельта этапа 3: события «Мозга», формулы по любым таблицам, недельные метрики."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.fakes import FakeClock
from tests.integration.metrics.helpers import D, at, make_account
from xcopilot.metrics.catalog import load_definitions, sync_catalog
from xcopilot.metrics.errors import InvalidEventProps
from xcopilot.metrics.events import PROPS_SCHEMA, EventType, emit
from xcopilot.metrics.query import get_series
from xcopilot.metrics.rollup import compute, run_rollups

BRAIN_EVENTS: dict[str, dict[str, Any]] = {
    "material.analyzed": {
        "source_id": "s",
        "analysis_id": "a",
        "format": "post",
        "analyzer_version": "brain.analyze@1",
    },
    "material.signal_added": {"source_id": "s", "signal_id": "g", "origin": "screenshot"},
    "material.quarantined": {"source_id": "s", "reason": "injection"},
    "material.removed": {"source_id": "s", "revoked_evidence": 2},
    "knowledge.candidate_created": {
        "item_id": "i",
        "kind": "thesis",
        "layer": "craft",
        "origin": "materials",
    },
    "knowledge.merged": {"item_id": "i", "into_item_id": "j", "evidence_moved": 3},
    "knowledge.evidence_added": {
        "item_id": "i",
        "evidence_id": "e",
        "dimension": "eff",
        "basis": "own_outcome",
        "polarity": 1,
    },
    "knowledge.transition": {
        "item_id": "i",
        "from_status": "candidate",
        "to_status": "probation",
        "actor": "system",
        "rule": "auto_probation",
    },
    "knowledge.reverted": {"item_id": "i", "transition_id": "t", "to_status": "candidate"},
    "knowledge.owner_decision": {"item_id": "i", "decision": "confirm"},
    "knowledge.conflict_opened": {"link_id": "l", "from_item_id": "i", "to_item_id": "j"},
    "knowledge.pack_built": {
        "pack_id": "p",
        "consumer": "writer",
        "items": 9,
        "token_count": 800,
        "format": "post",
    },
    "knowledge.rollback": {
        "to_date": "2026-11-01",
        "transitions_reverted": 4,
        "evidence_revoked": 0,
    },
}

BAD_PROPS: dict[str, dict[str, Any]] = {
    "material.signal_added": {"source_id": "s", "signal_id": "g", "origin": "tiktok"},
    "knowledge.evidence_added": {
        "item_id": "i",
        "evidence_id": "e",
        "dimension": "eff",
        "basis": "own_outcome",
        "polarity": 5,
    },
    "knowledge.transition": {
        "item_id": "i",
        "from_status": None,
        "to_status": "active",
        "actor": "robot",
        "rule": "x",
    },
    "knowledge.owner_decision": {"item_id": "i", "decision": "maybe"},
    "knowledge.pack_built": {"pack_id": "p", "consumer": "writer", "items": -1, "token_count": 1},
    "material.removed": {"source_id": "s"},
}

BRAIN_METRICS = {
    "brain_auto_revert_rate",
    "brain_confirm_load_week",
    "brain_cost_per_100_materials",
    "brain_items_by_status",
    "brain_knowledge_lift",
    "brain_materials_day",
    "brain_own_validated_share",
    "brain_pack_coverage",
    "brain_probation_win_rate",
    "brain_thesis_precision_own",
    "brain_voice_drift",
    "zero_edit_rate_by_variant",
}


async def test_brain_event_props_validated(session: AsyncSession) -> None:
    acc = await make_account(session, "brain_events")
    assert set(BRAIN_EVENTS) <= {t.value for t in EventType}
    assert set(PROPS_SCHEMA) == set(EventType)
    for type_, props in BRAIN_EVENTS.items():
        await emit(session, account_id=acc, type=type_, props=props)
    for type_, props in BAD_PROPS.items():
        with pytest.raises(InvalidEventProps):
            await emit(session, account_id=acc, type=type_, props=props)
    with pytest.raises(InvalidEventProps):
        await emit(
            session,
            account_id=acc,
            type="knowledge.owner_decision",
            props={"item_id": "i", "decision": "confirm", "extra": 1},
        )
    count = (
        await session.execute(text("SELECT count(*) FROM events WHERE account_id = :a"), {"a": acc})
    ).scalar()
    assert count == len(BRAIN_EVENTS)


def test_brain_metrics_in_catalog_and_lessons_metric_gone() -> None:
    keys = {d.key for d in load_definitions()}
    assert keys >= BRAIN_METRICS
    assert "lessons_accepted_week" not in keys
    units = {d.key: d.unit for d in load_definitions()}
    assert units["brain_cost_per_100_materials"] == "micro_usd"
    assert units["brain_voice_drift"] == "distance"


async def _item(session: AsyncSession, acc: UUID, layer: str = "craft") -> UUID:
    return cast(
        UUID,
        (
            await session.execute(
                text(
                    "INSERT INTO knowledge_items (account_id, kind, layer, element, statement, guidance,"
                    " risk_class, origin) VALUES (:a, 'thesis', :l, 'hook',"
                    " 'Личная цифра в первой строке даёт больше', 'Начни с цифры', :r, 'materials')"
                    " RETURNING id"
                ),
                {"a": acc, "l": layer, "r": "A" if layer == "voice" else "B"},
            )
        ).scalar_one(),
    )


async def _snapshot(
    session: AsyncSession,
    acc: UUID,
    item: UUID,
    day: date,
    status: str,
    layer: str = "craft",
    own: tuple[int, int] = (0, 0),
) -> None:
    await session.execute(
        text(
            "INSERT INTO knowledge_snapshots (snapshot_date, account_id, item_id, status, owner_state,"
            " version, layer, eff_alpha, eff_beta, fit_alpha, fit_beta, eff_conf, fit_conf, quality,"
            " evidence_n, own_support, own_contra) VALUES (:d, :a, :i, :s, 'none', 1, :l, 1, 1, 2, 1,"
            " 0.2, 0.4, 0.3, 0, :os, :oc)"
        ),
        {"d": day, "a": acc, "i": item, "s": status, "l": layer, "os": own[0], "oc": own[1]},
    )


async def test_metric_formula_can_read_snapshot_tables(session: AsyncSession) -> None:
    await sync_catalog(session)
    acc = await make_account(session, "brain_snap")
    items = [await _item(session, acc) for _ in range(4)]
    voice = await _item(session, acc, layer="voice")
    await _snapshot(session, acc, items[0], D, "active", own=(2, 0))
    await _snapshot(session, acc, items[1], D, "active", own=(1, 0))
    await _snapshot(session, acc, items[2], D, "probation", own=(1, 1))
    await _snapshot(session, acc, items[3], D, "candidate")
    await _snapshot(session, acc, voice, D, "active", layer="voice", own=(5, 0))
    by_status = await compute(session, "brain_items_by_status", acc, D, D)
    counts = {(r.dims["status"], r.dims["layer"]): r.value for r in by_status}
    assert counts == {
        ("active", "craft"): 2,
        ("probation", "craft"): 1,
        ("candidate", "craft"): 1,
        ("active", "voice"): 1,
    }
    [share] = await compute(session, "brain_own_validated_share", acc, D, D)
    assert share.value == Decimal(2) / Decimal(3) and share.sample_size == 3  # голос не в счёт


async def test_weekly_grain_metric_rolls_up(session: AsyncSession) -> None:
    await sync_catalog(session)
    acc = await make_account(session, "brain_week")
    uid = (
        await session.execute(
            text("INSERT INTO users (tg_user_id, name) VALUES (777001, 'o') RETURNING id")
        )
    ).scalar_one()
    item = await _item(session, acc)
    monday = D - timedelta(days=D.weekday())
    week = [
        datetime(monday.year, monday.month, monday.day, 10, tzinfo=UTC) + timedelta(days=i)
        for i in (0, 2, 4, 6)
    ]
    for i, ts in enumerate(week):
        await session.execute(
            text(
                "INSERT INTO knowledge_transitions (account_id, item_id, ts, from_status, to_status,"
                " actor, rule, reverted_at, reverted_by) VALUES (:a, :i, :ts, 'candidate', 'probation',"
                " 'system', 'auto_probation', :rv, :u)"
            ),
            {
                "a": acc,
                "i": item,
                "ts": ts,
                "rv": ts + timedelta(hours=1) if i == 0 else None,
                "u": uid if i == 0 else None,
            },
        )
    # ручной переход и переход следующей недели не попадают в неделю
    await session.execute(
        text(
            "INSERT INTO knowledge_transitions (account_id, item_id, ts, to_status, actor, user_id, rule) VALUES (:a, :i, :ts, 'active', 'owner', :u, 'owner_confirm')"
        ),
        {"a": acc, "i": item, "ts": week[1], "u": uid},
    )
    rows = await compute(session, "brain_auto_revert_rate", acc, monday, monday + timedelta(days=6))
    assert len(rows) == 1
    assert (
        rows[0].period_start == monday
        and rows[0].value == Decimal("0.25")
        and rows[0].sample_size == 4
    )
    clock = FakeClock(datetime(2026, 4, 1, tzinfo=UTC))
    await run_rollups(session, acc, monday + timedelta(days=6), clock=clock)
    series = await get_series(
        session, acc, "brain_auto_revert_rate", monday, monday + timedelta(days=6), clock=clock
    )
    assert [(p.period_start, p.value) for p in series] == [(monday, Decimal("0.25"))]


async def test_every_brain_metric_runs_on_fixture(session: AsyncSession) -> None:
    """Все формулы приложения C выполняются на маленьком наборе данных и опираются только на БД."""
    await sync_catalog(session)
    acc = await make_account(session, "brain_all")
    item = await _item(session, acc)
    src = (
        await session.execute(
            text(
                "INSERT INTO sources (account_id, kind, stance, origin, content_hash, posted_at, external_id)"
                " VALUES (:a, 'post', 'own', 'history_backfill', :h, :p, 'x1') RETURNING id"
            ),
            {"a": acc, "h": uuid4().hex, "p": at(8)},
        )
    ).scalar_one()
    await session.execute(
        text(
            "INSERT INTO material_analyses (account_id, source_id, analyzer_version, taxonomy_version, model, format, created_at) VALUES (:a, :s, 'a@1', 1, 'm', 'post', :t)"
        ),
        {"a": acc, "s": src, "t": at(9)},
    )
    await session.execute(
        text(
            "INSERT INTO material_outcomes (account_id, source_id, method_version, basis, signal_origin, reliability, pct_engagement) VALUES (:a, :s, 1, 'own_baseline', 'own_sync', 1, 0.8)"
        ),
        {"a": acc, "s": src},
    )
    await session.execute(
        text(
            "INSERT INTO knowledge_evidence (account_id, item_id, source_id, dimension, basis, polarity, w_source, w_scope, w_magnitude, observed_at, created_at) VALUES (:a, :i, :s, 'eff', 'own_outcome', 1, 1, 2, 1, :t, :t)"
        ),
        {"a": acc, "i": item, "s": src, "t": at(10)},
    )
    await session.execute(
        text(
            'INSERT INTO knowledge_packs (account_id, consumer, task, schema_version, taxonomy_version, items, token_count, created_at) VALUES (:a, \'writer\', \'{"format": "post"}\', 1, 1, \'[{"slot": "core"}]\', 10, :t)'
        ),
        {"a": acc, "t": at(11)},
    )
    await emit(
        session,
        account_id=acc,
        type="knowledge.owner_decision",
        props={"item_id": str(item), "decision": "confirm"},
        ts=at(12),
    )
    await session.execute(
        text(
            "INSERT INTO llm_calls (account_id, ts, role, provider, model, purpose, cost_micro_usd, status) VALUES (:a, :t, 'cheap', 'deepseek', 'm', 'brain.analyze', 5000, 'ok')"
        ),
        {"a": acc, "t": at(9)},
    )
    week_start = D - timedelta(days=D.weekday())
    results = {}
    for key in sorted(BRAIN_METRICS):
        results[key] = await compute(session, key, acc, week_start, week_start + timedelta(days=6))
    assert results["brain_materials_day"][0].value == 1
    assert results["brain_materials_day"][0].dims == {"stance": "own"}
    assert results["brain_thesis_precision_own"][0].value == 1
    assert results["brain_pack_coverage"][0].value == 0  # одно ядро из восьми
    assert results["brain_confirm_load_week"][0].dims == {"decision": "confirm"}
    assert results["brain_cost_per_100_materials"][0].value == 500000
    assert results["brain_knowledge_lift"][0].sample_size == 0  # своих постов с ботом нет
