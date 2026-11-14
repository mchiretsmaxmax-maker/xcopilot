# ruff: noqa: E501
"""Свёртки, пояс аккаунта, «сегодня» на лету, ряд с пропусками, бэкфилл, документ."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.fakes import FakeClock
from tests.integration.metrics.helpers import D, at, ev, make_account
from xcopilot.metrics.catalog import sync_catalog
from xcopilot.metrics.docs import main as docs_main
from xcopilot.metrics.docs import render_metrics_md
from xcopilot.metrics.events import EventType as T
from xcopilot.metrics.query import get_breakdown, get_series, get_value
from xcopilot.metrics.rollup import backfill, run_rollups

ROOT = Path(__file__).resolve().parents[3]

VALUES = (
    "SELECT metric_key, grain, period_start, dims, value, sample_size, definition_version,"
    " computed_at FROM metric_values ORDER BY metric_key, period_start, dims_hash"
)


async def _approvals(session: AsyncSession, account_id, day: date, n: int) -> None:  # type: ignore[no-untyped-def]
    for i in range(n):
        await ev(session, account_id, T.REVIEW_APPROVED, at(9, i, day))


async def test_rollup_is_idempotent(session: AsyncSession) -> None:
    await sync_catalog(session)
    acc = await make_account(session, "idem")
    await _approvals(session, acc, D, 2)
    clock = FakeClock(datetime(2026, 3, 11, 3, tzinfo=UTC))
    assert await run_rollups(session, acc, D, clock=clock) > 0
    first = (await session.execute(text(VALUES))).all()
    assert first
    clock.advance(3600)
    await run_rollups(session, acc, D, clock=clock)
    assert (await session.execute(text(VALUES))).all() == first  # в том числе computed_at


async def test_rollup_respects_account_timezone(session: AsyncSession) -> None:
    await sync_catalog(session)
    minsk = await make_account(session, "tz_minsk", tz="Europe/Minsk")
    utc = await make_account(session, "tz_utc", tz="UTC")
    for acc in (minsk, utc):
        await ev(
            session, acc, T.REVIEW_APPROVED, at(22, 30)
        )  # 22:30 UTC = 01:30 следующего дня в Минске
    clock = FakeClock(datetime(2026, 3, 20, tzinfo=UTC))
    for acc in (minsk, utc):
        await backfill(session, acc, D, D + timedelta(days=1), clock=clock)
    rows = (
        await session.execute(
            text(
                "SELECT account_id, period_start FROM metric_values WHERE metric_key = 'posts_published_day' OR metric_key = 'zero_edit_rate' ORDER BY 1"
            )
        )
    ).all()
    days = {str(r[0]): r[1] for r in rows}
    assert days[str(minsk)] == D + timedelta(days=1)
    assert days[str(utc)] == D


async def test_today_computed_on_the_fly(session: AsyncSession) -> None:
    await sync_catalog(session)
    acc = await make_account(session, "today")
    await _approvals(session, acc, D, 3)
    clock = FakeClock(datetime(2026, 3, 10, 18, tzinfo=UTC))
    # свёртки за «сегодня» нет в metric_values, но ряд её показывает
    assert (await session.execute(text("SELECT count(*) FROM metric_values"))).scalar_one() == 0
    point = await get_value(session, acc, "zero_edit_rate", D, clock=clock)
    assert point.value == Decimal(1) and point.sample_size == 3
    await _approvals(session, acc, D, 1)  # ещё одно решение — «сегодня» видит сразу
    series = await get_series(session, acc, "zero_edit_rate", D, D, clock=clock)
    assert series[0].sample_size == 4
    assert (await session.execute(text("SELECT count(*) FROM metric_values"))).scalar_one() == 0


async def test_get_series_returns_null_for_gaps_not_zero(session: AsyncSession) -> None:
    await sync_catalog(session)
    acc = await make_account(session, "gaps")
    await _approvals(session, acc, D, 2)
    await _approvals(session, acc, D + timedelta(days=2), 1)
    clock = FakeClock(datetime(2026, 3, 20, tzinfo=UTC))
    await backfill(session, acc, D, D + timedelta(days=2), clock=clock)
    series = await get_series(session, acc, "zero_edit_rate", D, D + timedelta(days=3), clock=clock)
    assert [(p.period_start, p.value) for p in series] == [
        (D, Decimal(1)),
        (D + timedelta(days=1), None),
        (D + timedelta(days=2), Decimal(1)),
        (D + timedelta(days=3), None),
    ]
    assert series[1].sample_size == 0


async def test_breakdown_by_dimension(session: AsyncSession) -> None:
    await sync_catalog(session)
    acc = await make_account(session, "breakdown")
    await ev(session, acc, T.REVIEW_APPROVED, at(9))
    await ev(session, acc, T.REVIEW_REJECTED, at(10), reason_code="tone")
    await ev(session, acc, T.REVIEW_REJECTED, at(11), reason_code="offtopic")
    await ev(session, acc, T.REVIEW_REJECTED, at(12), reason_code="offtopic")
    clock = FakeClock(datetime(2026, 3, 20, tzinfo=UTC))
    await run_rollups(session, acc, D, clock=clock)
    rows = await get_breakdown(session, acc, "reject_rate_by_reason", D, "reason_code", clock=clock)
    assert {r.label: r.value for r in rows} == {"offtopic": Decimal("0.5"), "tone": Decimal("0.25")}


async def test_backfill_recomputes_range(session: AsyncSession) -> None:
    await sync_catalog(session)
    acc = await make_account(session, "backfill")
    for offset in range(3):
        await _approvals(session, acc, D + timedelta(days=offset), offset + 1)
    clock = FakeClock(datetime(2026, 3, 20, tzinfo=UTC))
    await backfill(session, acc, D, D + timedelta(days=2), clock=clock)
    sizes = (
        (
            await session.execute(
                text(
                    "SELECT sample_size FROM metric_values WHERE metric_key = 'zero_edit_rate' ORDER BY period_start"
                )
            )
        )
        .scalars()
        .all()
    )
    assert list(sizes) == [1, 2, 3]
    # испортили свёртку — бэкфилл возвращает правильное значение
    await session.execute(
        text(
            "UPDATE metric_values SET sample_size = 99, value = 0.5 WHERE metric_key = 'zero_edit_rate'"
        )
    )
    await backfill(session, acc, D, D + timedelta(days=2), clock=clock)
    sizes = (
        (
            await session.execute(
                text(
                    "SELECT sample_size FROM metric_values WHERE metric_key = 'zero_edit_rate' ORDER BY period_start"
                )
            )
        )
        .scalars()
        .all()
    )
    assert list(sizes) == [1, 2, 3]


def test_metrics_doc_is_up_to_date(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    text_md = render_metrics_md()
    for key in ("zero_edit_rate", "brain_confirm_load_week", "cost_per_published_post"):
        assert f"`{key}`" in text_md
    assert (ROOT / "docs/db/metrics.md").read_text() == text_md, "запусти make db-docs"
    monkeypatch.chdir(ROOT)
    assert docs_main(["--check"]) == 0
    monkeypatch.chdir(tmp_path)
    assert docs_main(["--check"]) == 1
    assert docs_main([]) == 0
    assert docs_main(["--check"]) == 0
