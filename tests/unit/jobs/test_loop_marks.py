"""Аудит I: отметки времени для сторожа — last_tick_at и busy_since."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any, cast

from tests.fakes.clock import FakeClock
from xcopilot.jobs.queue import Job
from xcopilot.jobs.worker import HandlerSpec, Worker


class EmptyQueue:
    async def claim(self, worker_id: str, limit: int, **kw: Any) -> list[Job]:
        return []

    async def release_stale(self, older_than: timedelta) -> int:
        return 0


async def test_worker_marks_tick_each_iteration() -> None:
    clock = FakeClock()
    worker = Worker(cast(Any, EmptyQueue()), {}, poll_interval=0.01, clock=clock)
    assert worker.last_tick_at is None and worker.busy_since is None
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run(stop, recover=False))
    await asyncio.sleep(0.05)
    first = worker.last_tick_at
    assert first == clock.now()
    clock.advance(30)
    await asyncio.sleep(0.05)
    assert worker.last_tick_at == clock.now() and worker.last_tick_at != first
    stop.set()
    await asyncio.wait_for(task, timeout=2)


async def test_worker_busy_since_set_during_job_only() -> None:
    clock = FakeClock()
    seen: list[Any] = []

    class OneJob(EmptyQueue):
        def __init__(self) -> None:
            self.given = False

        async def claim(self, worker_id: str, limit: int, **kw: Any) -> list[Job]:
            return []

    worker = Worker(cast(Any, OneJob()), {}, clock=clock)

    async def fn(job: Job) -> None:
        seen.append(worker.busy_since)

    worker.registry = {"k": HandlerSpec(fn)}

    class Q(OneJob):
        async def claim(self, worker_id: str, limit: int, **kw: Any) -> list[Job]:
            if self.given:
                return []
            self.given = True
            return [cast(Job, _FakeJob())]

        async def complete(self, job_id: Any) -> bool:
            return True

    worker.queue = cast(Any, Q())
    await worker.run_once()
    assert seen == [clock.now()]
    assert worker.busy_since is None


class _FakeJob:
    import uuid as _u

    id = _u.uuid4()
    account_id = _u.uuid4()
    kind = "k"
    attempts = 1
