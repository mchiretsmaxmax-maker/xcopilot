"""Этап 14, задачи 5–6, 11 и 15: базовый уровень, закономерности, кандидаты знаний."""

from __future__ import annotations

from datetime import datetime, timedelta

from tests.analytics_stand import AStand
from tests.brain_core_stand import FakeBrainLLM, candidate_judge, make_ctx
from xcopilot.analytics.baseline import baseline
from xcopilot.analytics.patterns import Patterns, Period, compute_patterns, to_candidates
from xcopilot.brain.extract import Batch, ingest_candidates
from xcopilot.brain.schemas import ExtractionResult


def _day(st: AStand, days_ago: int, hour: int) -> datetime:
    d = st.clock.now() - timedelta(days=days_ago)
    return d.replace(hour=hour, minute=0, second=0, microsecond=0)


async def seed_fixture(st: AStand) -> None:
    """17 постов: 7 в 9:00 по 3000 показов (тема defi), 7 в 20:00 по 1000, 3 ответа по 400."""
    for i in range(7):
        await st.seed_post(
            f"Утренний пост номер {i} про ликвидность и комиссии в сетях l2",
            posted_at=_day(st, i + 1, 9),
            views=3000,
            tags=("topic:defi",),
        )
        await st.seed_post(
            f"Вечерний пост номер {i}",
            posted_at=_day(st, i + 1, 20),
            views=1000,
        )
    for i in range(3):
        await st.seed_post(
            f"Ответ {i}",
            posted_at=_day(st, i + 8, 20),
            views=400,
            kind="reply",
        )


def _period(st: AStand, days: int = 30) -> Period:
    return Period(st.clock.now() - timedelta(days=days), st.clock.now())


async def test_baseline_is_trailing_30d_median(st: AStand) -> None:
    for i, views in enumerate([100, 200, 300, 400, 500]):
        await st.seed_post(f"Пост {i}", posted_at=_day(st, i + 1, 10), views=views)
    await st.seed_post("Старый", posted_at=_day(st, 40, 10), views=99_999)
    await st.seed_post("Ответ", posted_at=_day(st, 2, 10), views=10_000, kind="reply")
    async with st.sf() as s:
        b = await baseline(s, st.account_id, "post", 30, now=st.clock.now())
        r = await baseline(s, st.account_id, "reply", 30, now=st.clock.now())
    assert (b.median_impressions, b.sample_size) == (300, 5)
    assert (r.median_impressions, r.sample_size) == (10_000, 1)


async def test_patterns_by_format_and_hour_on_fixture(st: AStand) -> None:
    await seed_fixture(st)
    async with st.sf() as s:
        p = await compute_patterns(s, st.account_id, _period(st), tz="UTC")
    assert (p.sample_size, p.small_sample, p.baseline_median) == (17, False, 1000)
    by = {(x.dimension, x.value): x for x in p.items}
    assert (by["hour", "9"].n, by["hour", "9"].lift) == (7, 2.0)
    assert (by["format", "reply"].n, by["format", "reply"].lift) == (3, -0.6)
    assert ("hour", "20") not in by  # вечер = базовый уровень, не закономерность
    assert (by["topic", "defi"].n, by["topic", "defi"].lift) == (7, 2.0)
    assert len(by["hour", "9"].post_ids) == 7


async def test_small_sample_warning(st: AStand) -> None:
    for i in range(6):
        await st.seed_post(f"Пост {i}", posted_at=_day(st, i + 1, 9), views=500 + i)
    async with st.sf() as s:
        p = await compute_patterns(s, st.account_id, _period(st), tz="UTC")
    assert (p.sample_size, p.small_sample) == (6, True)


async def test_analyst_patterns_become_candidates_origin_own_metrics(st: AStand) -> None:
    await seed_fixture(st)
    async with st.sf() as s:
        p: Patterns = await compute_patterns(s, st.account_id, _period(st), tz="UTC")
    cands = to_candidates(p)
    assert cands and all(c.supporting for c in cands)
    llm = FakeBrainLLM(judge=candidate_judge())
    async with st.sf() as s:
        ids = list(p.sources)  # метки M1..Mn кандидатов — номера в этом списке
        ctx = make_ctx(s, st.account_id, llm=llm, clock=st.clock)
        batch = Batch(st.account_id, "post", ids, own_history=True)
        touched = await ingest_candidates(
            ctx, ExtractionResult(candidates=cands), batch, origin="own_metrics"
        )
        await s.commit()
    assert touched
    rows = await st.rows(
        "SELECT DISTINCT origin FROM knowledge_items WHERE account_id = :a", a=st.account_id
    )
    assert [r[0] for r in rows] == ["own_metrics"]
