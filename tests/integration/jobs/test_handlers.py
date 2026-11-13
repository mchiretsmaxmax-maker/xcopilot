"""Обработчик metrics.rollup."""

from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.fakes import FakeClock
from tests.integration.metrics.helpers import at, ev, make_account
from xcopilot.db.uow import UnitOfWork
from xcopilot.jobs.handlers import METRICS_ROLLUP_KIND, handle_metrics_rollup
from xcopilot.jobs.queue import JobQueue
from xcopilot.metrics.events import EventType as T


async def _setup(session: AsyncSession) -> UUID:
    account_id = await make_account(session, "handler", tz="Europe/Minsk")
    await ev(session, account_id, T.REVIEW_APPROVED, at(9, day=date(2026, 3, 10)))
    await ev(session, account_id, T.REVIEW_APPROVED, at(9, day=date(2026, 3, 12)))
    await session.commit()
    return account_id


async def _run(
    factory: async_sessionmaker[AsyncSession],
    account_id: UUID,
    payload: dict[str, str],
    clock: FakeClock,
) -> int:
    queue = JobQueue(lambda: UnitOfWork(factory), clock)
    await queue.enqueue(METRICS_ROLLUP_KIND, payload, account_id=account_id)
    (job,) = await queue.claim("w1", 1)
    return await handle_metrics_rollup(job, uow_factory=lambda: UnitOfWork(factory), clock=clock)


async def _days(session: AsyncSession) -> list[date]:
    rows = await session.execute(
        text(
            "SELECT period_start FROM metric_values WHERE metric_key = 'zero_edit_rate' ORDER BY 1"
        )
    )
    return list(rows.scalars().all())


async def test_rollup_handler_defaults_to_yesterday(
    session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    account_id = await _setup(session)
    clock = FakeClock(datetime(2026, 3, 11, 5, tzinfo=UTC))  # в Минске уже 11 марта
    assert await _run(session_factory, account_id, {}, clock) > 0
    assert await _days(session) == [date(2026, 3, 10)]


async def test_rollup_handler_day_and_backfill(
    session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    account_id = await _setup(session)
    clock = FakeClock(datetime(2026, 3, 20, tzinfo=UTC))
    await _run(session_factory, account_id, {"day": "2026-03-12"}, clock)
    assert await _days(session) == [date(2026, 3, 12)]
    await _run(
        session_factory, account_id, {"date_from": "2026-03-10", "date_to": "2026-03-12"}, clock
    )
    assert await _days(session) == [date(2026, 3, 10), date(2026, 3, 12)]
