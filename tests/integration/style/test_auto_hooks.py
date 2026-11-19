"""Автопересборку ставит детект своего материала (приём «это моё», «Всё моё» через revise)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.clean_voice_stand import source_ids_of
from tests.fakes import FakeClock
from tests.fakes.embedder import FakeEmbedder
from tests.style_stand import new_account, seed_chunks
from xcopilot.brain.jobs import BrainDeps, handle_detect
from xcopilot.db.uow import UnitOfWork
from xcopilot.jobs.queue import Job
from xcopilot.style.auto import AUTO_REBUILD_KIND

SF = async_sessionmaker[AsyncSession]


def _job(acc: UUID, source_id: UUID, **extra: object) -> Job:
    return Job(
        id=uuid4(),
        account_id=acc,
        kind="brain.detect",
        payload={"source_id": str(source_id), **extra},
        status="running",
        attempts=1,
        max_attempts=3,
        run_at=datetime.now(UTC),
        locked_by=None,
        last_error=None,
    )


async def _queued(sf: SF, acc: UUID) -> int:
    async with sf() as s:
        return int(
            (
                await s.execute(
                    text(
                        "SELECT count(*) FROM jobs WHERE account_id=:a AND kind=:k"
                        " AND status='queued'"
                    ),
                    {"a": acc, "k": AUTO_REBUILD_KIND},
                )
            ).scalar_one()
        )


async def test_detect_on_own_material_requests_rebuild(session_factory: SF) -> None:
    acc = await new_account(session_factory)
    own = await source_ids_of(session_factory, await seed_chunks(session_factory, acc, ["mine"]))
    rival = await source_ids_of(
        session_factory,
        await seed_chunks(session_factory, acc, ["theirs"], kind="competitor_post"),
    )
    deps = BrainDeps(
        uow_factory=lambda: UnitOfWork(session_factory),
        clock=FakeClock(datetime.now(UTC)),
        embedder=FakeEmbedder(),
    )
    await handle_detect(_job(acc, rival[0]), deps=deps)
    assert await _queued(session_factory, acc) == 0
    await handle_detect(_job(acc, own[0], revise=True), deps=deps)
    await handle_detect(_job(acc, own[0]), deps=deps)
    assert await _queued(session_factory, acc) == 1
