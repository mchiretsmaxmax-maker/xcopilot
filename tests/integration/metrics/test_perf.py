"""Критерий готовности: свёртка дня одного аккаунта на 100 тыс. событий быстрее 5 секунд."""

import time
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.fakes import FakeClock
from tests.integration.metrics.helpers import D, make_account
from xcopilot.metrics.catalog import sync_catalog
from xcopilot.metrics.rollup import run_rollups

SEED = """
INSERT INTO events (account_id, ts, type, actor, entity_type, entity_id, props)
SELECT :a, :base + (g % 86400) * interval '1 second',
       (ARRAY['review.approved','review.edited','review.rejected','draft.generated',
              'draft.judged','card.shown','draft.lint_failed','publication.confirmed'])[1 + g % 8],
       'system', 'draft', uuidv7(), '{}'::jsonb
FROM generate_series(1, 100000) g
"""


async def test_rollup_of_one_day_with_100k_events_under_5_seconds(session: AsyncSession) -> None:
    await sync_catalog(session)
    acc = await make_account(session, "perf")
    await session.execute(
        text(SEED), {"a": acc, "base": datetime(D.year, D.month, D.day, tzinfo=UTC)}
    )
    await session.execute(text("ANALYZE events"))
    clock = FakeClock(datetime(2026, 3, 11, 3, tzinfo=UTC))
    started = time.perf_counter()
    await run_rollups(session, acc, D, clock=clock)
    elapsed = time.perf_counter() - started
    print(f"rollup 100k events: {elapsed:.2f}s")
    assert elapsed < 5.0
