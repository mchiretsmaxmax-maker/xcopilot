"""Воркер переживает сбой очереди (БД недоступна) и продолжает работу."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any, cast

from xcopilot.jobs.queue import Job
from xcopilot.jobs.worker import Worker


class FlakyQueue:
    def __init__(self) -> None:
        self.claims = 0
        self.stale_calls = 0
        self.recovered = asyncio.Event()

    async def claim(self, worker_id: str, limit: int) -> list[Job]:
        self.claims += 1
        if self.claims == 1:
            raise ConnectionError("PostgreSQL недоступен")
        self.recovered.set()
        return []

    async def release_stale(self, older_than: timedelta) -> int:
        self.stale_calls += 1
        raise ConnectionError("PostgreSQL недоступен")


async def test_worker_survives_queue_errors() -> None:
    queue = FlakyQueue()
    worker = Worker(cast(Any, queue), {}, poll_interval=0.01, stale_check_every=1)
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run(stop))
    await asyncio.wait_for(queue.recovered.wait(), timeout=5)
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    assert queue.claims >= 2
    assert queue.stale_calls >= 1
