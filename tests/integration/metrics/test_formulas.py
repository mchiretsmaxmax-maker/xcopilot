# ruff: noqa: E501
"""17 метрик стартового каталога на фикстурах."""

from datetime import timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.metrics.helpers import (
    D,
    at,
    ev,
    llm_call,
    make_account,
    new_id,
    snapshot,
    x_call,
)
from xcopilot.db.repos import DraftsRepo, PublicationsRepo, RunsRepo
from xcopilot.metrics.catalog import sync_catalog
from xcopilot.metrics.events import EventType as T
from xcopilot.metrics.rollup import MetricRow, compute


async def one(session: AsyncSession, key: str, account_id: UUID) -> MetricRow:
    rows = await compute(session, key, account_id, D, D)
    assert len(rows) == 1, rows
    return rows[0]


async def decisions(session: AsyncSession, a: UUID) -> None:
    """10 решений: 4 без правок, 3 с правками (0.1/0.2/0.6), 3 отказа (offtopic ×2, tone ×1)."""
    for i in range(4):
        await ev(session, a, T.REVIEW_APPROVED, at(9, i))
    for i, dist in enumerate((0.1, 0.2, 0.6)):
        await ev(session, a, T.REVIEW_EDITED, at(10, i), edit_distance=dist)
    for i, reason in enumerate(("offtopic", "offtopic", "tone")):
        await ev(session, a, T.REVIEW_REJECTED, at(11, i), reason_code=reason)
    await ev(session, a, T.REVIEW_REGENERATE, at(12))  # не решение: в выборку не входит


@pytest.fixture
async def acc(session: AsyncSession) -> UUID:
    await sync_catalog(session)
    return await make_account(session, "formulas")


async def test_zero_edit_rate_on_fixture(session: AsyncSession, acc: UUID) -> None:
    await decisions(session, acc)
    row = await one(session, "zero_edit_rate", acc)
    assert row.value == Decimal("0.4") and row.sample_size == 10


async def test_approval_rate_on_fixture(session: AsyncSession, acc: UUID) -> None:
    await decisions(session, acc)
    row = await one(session, "approval_rate", acc)
    assert row.value == Decimal("0.7") and row.sample_size == 10


async def test_edit_distance_avg_on_fixture(session: AsyncSession, acc: UUID) -> None:
    await decisions(session, acc)
    row = await one(session, "edit_distance_avg", acc)
    assert row.value is not None and float(row.value) == pytest.approx(0.3)
    assert row.sample_size == 3


async def test_reject_rate_by_reason_has_dimension_rows(session: AsyncSession, acc: UUID) -> None:
    await decisions(session, acc)
    rows = await compute(session, "reject_rate_by_reason", acc, D, D)
    by_reason = {r.dims["reason_code"]: r for r in rows}
    assert set(by_reason) == {"offtopic", "tone"}
    assert by_reason["offtopic"].value == Decimal("0.2")
    assert by_reason["tone"].value == Decimal("0.1")
    assert all(r.sample_size == 10 for r in rows)


async def test_cost_per_published_post_on_fixture(session: AsyncSession, acc: UUID) -> None:
    await llm_call(session, acc, at(8), "writer", 1_000_000)
    await llm_call(session, acc, at(9), "judge", 2_000_000)
    await x_call(session, acc, at(10), "GET /2/tweets", 500_000)
    for i in range(3):
        await ev(
            session, acc, T.PUBLICATION_CONFIRMED, at(12, i), x_post_id=f"p{i}", matched_by="auto"
        )
    row = await one(session, "cost_per_published_post", acc)
    assert row.value is not None and float(row.value) == pytest.approx(3_500_000 / 3)
    assert row.sample_size == 3
    # ноль публикаций → NULL, а не деление на ноль
    other_day = D + timedelta(days=1)
    await llm_call(session, acc, at(8, day=other_day), "writer", 700_000)
    rows = await compute(session, "cost_per_published_post", acc, other_day, other_day)
    assert [(r.value, r.sample_size) for r in rows] == [(None, 0)]


async def test_engagement_rate_24h_uses_checkpoint_snapshot(
    session: AsyncSession, acc: UUID
) -> None:
    published = at(10)
    await snapshot(session, acc, "post1", published, 600, impressions=300, likes=3)
    await snapshot(
        session,
        acc,
        "post1",
        published,
        1470,
        impressions=1000,
        likes=30,
        replies=10,
        reposts=5,
        quotes=3,
        bookmarks=2,
    )
    await snapshot(session, acc, "post1", published, 4000, impressions=5000, likes=900)
    row = await one(session, "engagement_rate_24h", acc)
    assert row.value == Decimal("0.05")  # 50 / 1000 — снимок ближе к 24 ч, не последний
    assert row.sample_size == 1


async def test_judge_owner_agreement_on_fixture(session: AsyncSession, acc: UUID) -> None:
    d1, d2, d3, d4 = (new_id() for _ in range(4))
    for d, verdict in ((d1, "pass"), (d2, "pass"), (d3, "fail"), (d4, "fail")):
        await ev(session, acc, T.DRAFT_JUDGED, at(8), entity_id=d, verdict=verdict, score=0.5)
    await ev(session, acc, T.REVIEW_APPROVED, at(9), entity_id=d1)
    await ev(session, acc, T.REVIEW_REJECTED, at(9), entity_id=d2, reason_code="tone")
    await ev(session, acc, T.REVIEW_REJECTED, at(9), entity_id=d3, reason_code="tone")
    await ev(session, acc, T.REVIEW_EDITED, at(9), entity_id=d4, edit_distance=0.1)
    row = await one(session, "judge_owner_agreement", acc)
    assert row.value == Decimal("0.5") and row.sample_size == 4


async def test_rewrite_loop_avg_on_fixture(session: AsyncSession, acc: UUID) -> None:
    for _ in range(4):
        await ev(session, acc, T.DRAFT_GENERATED, at(8), variant_no=1, revision=1, format="post")
    for i in range(6):
        await ev(session, acc, T.DRAFT_REWRITTEN, at(9, i), revision=2, reason="lint")
    row = await one(session, "rewrite_loop_avg", acc)
    assert row.value == Decimal("1.5") and row.sample_size == 4


async def test_lint_fail_rate_on_fixture(session: AsyncSession, acc: UUID) -> None:
    for _ in range(4):
        await ev(session, acc, T.DRAFT_GENERATED, at(8), variant_no=1, revision=1, format="post")
    for rule in ("length", "length", "emoji"):
        await ev(session, acc, T.DRAFT_LINT_FAILED, at(9), rule=rule)
    rows = await compute(session, "lint_fail_rate", acc, D, D)
    by_rule = {r.dims["rule"]: r for r in rows}
    assert by_rule["length"].value == Decimal("0.5")
    assert by_rule["emoji"].value == Decimal("0.25")
    assert all(r.sample_size == 4 for r in rows)


async def test_time_to_decision_p50_on_fixture(session: AsyncSession, acc: UUID) -> None:
    ids = [new_id() for _ in range(3)]
    for d, minutes in zip(ids, (5, 10, 30), strict=True):
        await ev(session, acc, T.CARD_SHOWN, at(9), entity_id=d, kind="draft")
        await ev(session, acc, T.REVIEW_APPROVED, at(9) + timedelta(minutes=minutes), entity_id=d)
    row = await one(session, "time_to_decision_p50", acc)
    assert row.value == Decimal("10") and row.sample_size == 3


async def test_llm_and_x_cost_by_dimension(session: AsyncSession, acc: UUID) -> None:
    await llm_call(session, acc, at(8), "writer", 1_000_000)
    await llm_call(session, acc, at(9), "writer", 500_000)
    await llm_call(session, acc, at(9), "judge", 200_000)
    await x_call(session, acc, at(10), "GET /2/tweets", 300_000)
    await x_call(session, acc, at(11), "POST /2/tweets", 100_000)
    llm = {r.dims["role"]: r for r in await compute(session, "llm_cost_day", acc, D, D)}
    assert llm["writer"].value == Decimal(1_500_000) and llm["writer"].sample_size == 2
    assert llm["judge"].value == Decimal(200_000)
    x = {r.dims["endpoint"]: r for r in await compute(session, "x_api_cost_day", acc, D, D)}
    assert x["GET /2/tweets"].value == Decimal(300_000)
    assert x["POST /2/tweets"].value == Decimal(100_000)


async def test_posts_published_day_on_fixture(session: AsyncSession, acc: UUID) -> None:
    for i in range(3):
        await ev(
            session, acc, T.PUBLICATION_CONFIRMED, at(9, i), x_post_id=f"p{i}", matched_by="auto"
        )
    row = await one(session, "posts_published_day", acc)
    assert row.value == Decimal(3) and row.sample_size == 3


async def test_impressions_24h_median_on_fixture(session: AsyncSession, acc: UUID) -> None:
    for i, impressions in enumerate((100, 300, 900)):
        await snapshot(session, acc, f"p{i}", at(10, i), 1440, impressions=impressions)
        await snapshot(session, acc, f"p{i}", at(10, i), 4000, impressions=impressions * 10)
    row = await one(session, "impressions_24h_median", acc)
    assert row.value == Decimal(300) and row.sample_size == 3


async def test_lift_vs_baseline_on_fixture(session: AsyncSession, acc: UUID) -> None:
    run = await RunsRepo(session, acc).add(kind="post")
    draft = await DraftsRepo(session, acc).add(run_id=run.id, format="post", text="t")
    await PublicationsRepo(session, acc).add(
        draft_id=draft.id,
        x_post_id="bot1",
        url="https://x.com/a/status/1",
        published_at=at(10),
        matched_by="auto",
        final_text="t",
    )
    # пост с ботом: ER 0.10; обычные посты за предыдущие дни: ER 0.02, 0.04, 0.06 → медиана 0.04
    await snapshot(session, acc, "bot1", at(10), 1440, impressions=1000, likes=100)
    for i, likes in enumerate((20, 40, 60), start=1):
        published = at(10, day=D - timedelta(days=i))
        await snapshot(session, acc, f"own{i}", published, 1440, impressions=1000, likes=likes)
    row = await one(session, "lift_vs_baseline", acc)
    assert row.value is not None and float(row.value) == pytest.approx(2.5)
    assert row.sample_size == 1


async def test_followers_delta_day_on_fixture(session: AsyncSession, acc: UUID) -> None:
    for offset, followers in ((-1, 1000), (0, 1042)):
        await session.execute(
            text(
                "INSERT INTO account_metric_snapshots (account_id, date, followers) VALUES (:a, :d, :f)"
            ),
            {"a": acc, "d": D + timedelta(days=offset), "f": followers},
        )
    row = await one(session, "followers_delta_day", acc)
    assert row.value == Decimal(42)


async def test_lessons_accepted_week_on_fixture(session: AsyncSession, acc: UUID) -> None:
    # ТЗ v3.1: уроков нет; «принятые уроки за неделю» → brain_confirm_load_week (решения по знаниям)
    # неделя пн 2026-03-09 .. вс 2026-03-15; 2026-03-10 — вторник
    for day_offset in (0, 1, 3):
        await ev(
            session,
            acc,
            T.KNOWLEDGE_OWNER_DECISION,
            at(9, day=D + timedelta(days=day_offset)),
            entity_id=new_id(),
            entity_type="knowledge_item",
            item_id="i",
            decision="confirm",
        )
    await ev(
        session,
        acc,
        T.KNOWLEDGE_OWNER_DECISION,
        at(9, day=D + timedelta(days=10)),
        entity_id=new_id(),
        entity_type="knowledge_item",
        item_id="i",
        decision="confirm",
    )
    rows = await compute(session, "brain_confirm_load_week", acc, D, D + timedelta(days=4))
    assert [(r.period_start.isoformat(), r.value) for r in rows] == [("2026-03-09", Decimal(3))]
    assert rows[0].dims == {"decision": "confirm"}


async def test_metrics_scoped_to_account(session: AsyncSession, acc: UUID) -> None:
    other = await make_account(session, "formulas_other")
    await decisions(session, other)
    await llm_call(session, other, at(8), "writer", 9_000_000)
    assert await compute(session, "zero_edit_rate", acc, D, D) == []
    assert await compute(session, "llm_cost_day", acc, D, D) == []
    assert len(await compute(session, "zero_edit_rate", other, D, D)) == 1
