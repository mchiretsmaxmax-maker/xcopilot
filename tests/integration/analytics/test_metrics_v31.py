"""Этап 14 (v3.1): метрики brain_knowledge_lift и brain_voice_drift на фикстуре."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.analytics_stand import AStand
from tests.brain_core_stand import knowledge
from tests.fakes.embedder import FakeEmbedder
from tests.integration.db.brain_helpers import pack
from xcopilot.db.repos.chunks import ChunksRepo, EmbeddingModelsRepo
from xcopilot.db.repos.sources import SourcesRepo
from xcopilot.metrics.catalog import sync_catalog
from xcopilot.metrics.rollup import compute

WEEK = date(2026, 11, 9)  # понедельник; часы стенда — 14.11 12:00 UTC
END = WEEK + timedelta(days=6)


async def _outcome_post(st: AStand, s: AsyncSession, xid: str, pct: float, *, with_k: bool) -> None:
    sid, _ = await st.seed_post(
        f"Пост {xid}", posted_at=datetime(2026, 11, 10, 9, tzinfo=UTC), views=1000, x_post_id=xid
    )
    await s.execute(
        text(
            "INSERT INTO material_outcomes (account_id, source_id, method_version, basis,"
            " signal_origin, reliability, pct_engagement)"
            " VALUES (:a, :s, 1, 'own_baseline', 'own_sync', 1, :p)"
        ),
        {"a": st.account_id, "s": sid, "p": pct},
    )
    if with_k:
        draft = await st.publish(f"Пост {xid}", xid, datetime(2026, 11, 10, 9, tzinfo=UTC))
        item = await knowledge(s, st.account_id)
        await s.execute(
            text(
                "INSERT INTO knowledge_applications (account_id, pack_id, item_id, draft_id, slot,"
                " applied) VALUES (:a, :p, :i, :d, 'core', true)"
            ),
            {"a": st.account_id, "p": await pack(s, st.account_id), "i": item, "d": draft},
        )


async def test_knowledge_lift_metric_on_fixture(st: AStand) -> None:
    async with st.sf() as s:
        await sync_catalog(s)
        await _outcome_post(st, s, "801", 0.8, with_k=True)
        await _outcome_post(st, s, "802", 0.9, with_k=True)
        await _outcome_post(st, s, "803", 0.5, with_k=False)
        await _outcome_post(st, s, "804", 0.5, with_k=False)
        await s.commit()
    async with st.sf() as s:
        [row] = await compute(s, "brain_knowledge_lift", st.account_id, WEEK, END)
    assert round(float(row.value or 0), 2) == 0.35  # 0.85 − 0.50
    assert row.sample_size == 2


async def _own_with_vec(st: AStand, s: AsyncSession, body: str, *, origin: str, xid: str) -> None:
    repo = SourcesRepo(s, st.account_id)
    src = await repo.add(
        kind="post", stance="own", origin=origin, content_hash=xid, external_id=xid,
        posted_at=datetime(2026, 11, 10, 9, tzinfo=UTC), status="indexed",
    )  # fmt: skip
    doc = await repo.add_document(src.id, text=body, text_clean=body)
    chunks = ChunksRepo(s, st.account_id)
    chunk = await chunks.add_chunk(doc.id, idx=0, text=body, tags=[], lang="ru")
    model = await EmbeddingModelsRepo(s).active()
    assert model is not None
    await chunks.add_embedding(chunk.id, model_id=model.id, vec=FakeEmbedder().embed([body])[0])


async def test_voice_drift_metric_on_fixture(st: AStand) -> None:
    async with st.sf() as s:
        await sync_catalog(s)
        await _own_with_vec(st, s, "alpha beta gamma", origin="history_backfill", xid="901")
        await _own_with_vec(st, s, "delta epsilon zeta", origin="x_api", xid="902")
        await s.commit()
    await st.publish("delta epsilon zeta", "902", datetime(2026, 11, 10, 9, tzinfo=UTC))
    async with st.sf() as s:
        [row] = await compute(s, "brain_voice_drift", st.account_id, WEEK, END)
    assert round(float(row.value or 0), 2) == 1.0  # слова не пересекаются — векторы ортогональны
    assert row.sample_size == 1


async def test_voice_drift_zero_for_same_voice(st: AStand) -> None:
    async with st.sf() as s:
        await sync_catalog(s)
        await _own_with_vec(st, s, "alpha beta gamma", origin="history_backfill", xid="911")
        await _own_with_vec(st, s, "alpha beta gamma", origin="x_api", xid="912")
        await s.commit()
    await st.publish("alpha beta gamma", "912", datetime(2026, 11, 10, 9, tzinfo=UTC))
    async with st.sf() as s:
        [row] = await compute(s, "brain_voice_drift", st.account_id, WEEK, END)
    assert abs(float(row.value or 0)) < 0.01
