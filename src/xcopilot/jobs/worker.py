"""Воркер очереди задач (заглушка red-фазы)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta

from xcopilot.jobs.queue import Job, JobQueue

JobFn = Callable[[Job], Awaitable[object]]


@dataclass(frozen=True, slots=True)
class HandlerSpec:
    """Обработчик вида задачи и исключения, при которых повтор бесполезен."""

    fn: JobFn
    permanent: tuple[type[BaseException], ...] = ()


Registry = Mapping[str, HandlerSpec]


class Worker:
    def __init__(
        self,
        queue: JobQueue,
        registry: Registry,
        *,
        worker_id: str = "worker",
        poll_interval: float = 1.0,
        stale_after: timedelta = timedelta(minutes=30),
        stale_check_every: int = 60,
    ) -> None:
        self.queue = queue
        self.registry = registry
        self.worker_id = worker_id

    async def process(self, job: Job) -> str:
        raise NotImplementedError

    async def run_once(self) -> bool:
        raise NotImplementedError

    async def drain(self, max_jobs: int = 100) -> int:
        raise NotImplementedError

    async def run(self, stop: asyncio.Event) -> None:
        raise NotImplementedError
