"""Воркер очереди задач: взять задачу → обработчик по `kind` → complete / fail.

Правила (ТЗ P13/P25, ADR 0022):
- задачи берутся по одной (`FOR UPDATE SKIP LOCKED` в `JobQueue.claim`) — перед остановкой
  воркер не держит взятых, но не начатых задач;
- успех → `complete`; исключение из `permanent` обработчика или общих постоянных
  (`NotFound`) → задача сразу `dead`; любое другое → `fail` с паузой по правилам очереди
  (после `max_attempts` — `dead` и событие `job.dead`);
- вид задачи не из реестра → `dead` с ошибкой «неизвестный вид задачи: …»;
- на время задачи в контексте логов — `account_id`, `job_id`, `job_kind`;
- остановка (`stop`) проверяется между задачами: текущая доделывается;
- сбой обработчика или самой очереди (БД недоступна) не роняет цикл.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta

import structlog

from xcopilot.errors import NotFound
from xcopilot.jobs.queue import Job, JobQueue

log = structlog.get_logger()

JobFn = Callable[[Job], Awaitable[object]]
ALWAYS_PERMANENT: tuple[type[BaseException], ...] = (NotFound,)
_ERROR_LIMIT = 500


@dataclass(frozen=True, slots=True)
class HandlerSpec:
    """Обработчик вида задачи и исключения, при которых повтор бесполезен."""

    fn: JobFn
    permanent: tuple[type[BaseException], ...] = ()


Registry = Mapping[str, HandlerSpec]


def describe_error(exc: BaseException) -> str:
    """Короткое описание ошибки для jobs.last_error (тип и начало сообщения)."""
    message = str(exc).strip()
    name = type(exc).__name__
    return f"{name}: {message[:_ERROR_LIMIT]}" if message else name


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
        self.poll_interval = poll_interval
        self.stale_after = stale_after
        self.stale_check_every = max(stale_check_every, 1)

    async def process(self, job: Job) -> str:
        """Выполнить взятую задачу и отметить её; вернуть итог: done / retry / dead."""
        with structlog.contextvars.bound_contextvars(
            account_id=str(job.account_id), job_id=str(job.id), job_kind=job.kind
        ):
            spec = self.registry.get(job.kind)
            if spec is None:
                log.error("job_unknown_kind")
                await self.queue.fail(job.id, f"неизвестный вид задачи: {job.kind}", retry=False)
                return "dead"
            try:
                await spec.fn(job)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                permanent = isinstance(exc, spec.permanent + ALWAYS_PERMANENT)
                log.warning(
                    "job_failed", error=type(exc).__name__, permanent=permanent, exc_info=True
                )
                status = await self.queue.fail(job.id, describe_error(exc), retry=not permanent)
                return "dead" if status == "dead" else "retry"
            await self.queue.complete(job.id)
            log.info("job_done", attempt=job.attempts)
            return "done"

    async def run_once(self) -> bool:
        """Взять одну готовую задачу и выполнить; False — очередь пуста."""
        jobs = await self.queue.claim(self.worker_id, 1)
        for job in jobs:
            await self.process(job)
        return bool(jobs)

    async def drain(self, max_jobs: int = 100) -> int:
        """Выполнять задачи, пока очередь не опустеет (тесты, разовые прогоны); вернуть число."""
        done = 0
        while done < max_jobs and await self.run_once():
            done += 1
        return done

    async def run(self, stop: asyncio.Event) -> None:
        """Цикл до `stop`: задачи по одной, пауза при пустой очереди, возврат зависших."""
        log.info("worker_started", worker_id=self.worker_id)
        tick = 0
        while not stop.is_set():
            if tick % self.stale_check_every == 0:
                await self._release_stale()
            tick += 1
            try:
                busy = await self.run_once()
            except Exception:
                log.exception("worker_iteration_failed")
                busy = False
            if not busy:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=self.poll_interval)
        log.info("worker_stopped", worker_id=self.worker_id)

    async def _release_stale(self) -> None:
        try:
            released = await self.queue.release_stale(self.stale_after)
        except Exception:
            log.exception("worker_release_stale_failed")
            return
        if released:
            log.warning("jobs_released_stale", count=released)
