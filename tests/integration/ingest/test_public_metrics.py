"""Ссылка на пост → публичные цифры сохраняются сигналом public_metrics (ADR 0033)."""

from __future__ import annotations

from datetime import timedelta

from tests.ingest_stand import Stand
from tests.samples import unique_text
from xcopilot.x.models import XMetrics, XPost


async def test_link_saves_public_metrics_signal(stand: Stand) -> None:
    stand.x.add_post(
        XPost(
            id="5001",
            text=unique_text(5001),
            author_handle="alice",
            created_at=stand.clock.now() - timedelta(days=3),
            source="oembed",
            metrics=XMetrics(
                likes=1200, reposts=80, replies=45, quotes=6, bookmarks=90, impressions=60000
            ),
            author_followers=25000,
        )
    )
    await stand.accept("https://x.com/alice/status/5001")
    await stand.run_jobs()
    rows = await stand.rows(
        "SELECT origin, views, likes, replies, reposts, quotes, bookmarks, author_followers,"
        " post_age_hours::float AS age FROM material_signals"
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.origin == "public_metrics"
    assert (row.views, row.likes, row.replies, row.reposts, row.quotes, row.bookmarks) == (
        60000,
        1200,
        45,
        80,
        6,
        90,
    )
    assert row.author_followers == 25000
    assert row.age == 72.0


async def test_link_without_numbers_has_no_signal(stand: Stand) -> None:
    stand.x.add_post(
        XPost(
            id="5002",
            text=unique_text(5002),
            author_handle="alice",
            created_at=None,
            source="oembed",
        )
    )
    await stand.accept("https://x.com/alice/status/5002")
    await stand.run_jobs()
    assert await stand.count("material_signals") == 0
