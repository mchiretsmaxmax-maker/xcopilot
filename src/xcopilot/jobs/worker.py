"""Воркер очереди задач: взять задачу → обработчик по `kind` → complete / fail.

Правила (ТЗ P13/P25, ADR 0022):
- задачи берутся по одной (`FOR UPDATE SKIP LOCKED` в `JobQueue.claim`) — перед остановкой
  воркер не держит взятых, но не начатых задач;
- успех → `complete`; исключение из `permanent` обработчика или общих постоянных
  (`NotFound`, `PermanentProviderError`) → задача сразу `dead`, владельцу уходит сообщение
  (`on_dead`; оно же — после последней из повторных попыток); любое другое → `fail` с паузой
  по правилам очереди (после `max_attempts` — `dead` и событие `job.dead`);
- вид задачи не из реестра → `dead` с ошибкой «неизвестный вид задачи: …»;
- на время задачи в контексте логов — `account_id`, `job_id`, `job_kind`;
- остановка (`stop`, SIGTERM): текущей задаче даётся `shutdown_grace` секунд, затем она
  отменяется и возвращается в очередь без засчитанной попытки (этап 15); отмена цикла
  (TaskGroup) тоже снимает блокировку;
- при старте `recover()` возвращает задачи, которые держал прошлый процесс этого хоста
  (compose перезапускает тот же контейнер — тот же hostname), не дожидаясь `stale_after`;
- отмена владельцем (статус `cancelled`): пока задача идёт, воркер раз в `cancel_poll` секунд
  смотрит её статус; отменённую прерывает, результат выбрасывается, `complete` статус не
  перетирает; итог `cancelled`, `on_cancel` обновляет статус-сообщение («⛔ Отменено»);
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
from xcopilot.jobs.queue import Job, JobCancelled, JobQueue
from xcopilot.llm.errors import PermanentProviderError

log = structlog.get_logger()

JobFn = Callable[[Job], Awaitable[object]]
ALWAYS_PERMANENT: tuple[type[BaseException], ...] = (NotFound, PermanentProviderError)
DeadHook = Callable[[Job, BaseException], Awaitable[None]]
CancelHook = Callable[[Job], Awaitable[None]]
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


class _CancelFlag:
    """Признак «задачу прервал наблюдатель отмены» (а не остановка процесса)."""

    __slots__ = ("value",)

    def __init__(self) -> None:
        self.value = False


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
        shutdown_grace: float = 20.0,
        on_dead: DeadHook | None = None,
        on_cancel: CancelHook | None = None,
        cancel_poll: float = 1.0,
    ) -> None:
        self.queue = queue
        self.registry = registry
        self.worker_id = worker_id
        self.poll_interval = poll_interval
        self.stale_after = stale_after
        self.stale_check_every = max(stale_check_every, 1)
        self.shutdown_grace = shutdown_grace
        self.on_dead = on_dead
        self.on_cancel = on_cancel
        self.cancel_poll = cancel_poll

    @property
    def host_prefix(self) -> str:
        """Префикс worker_id всех процессов этого хоста: `hostname:`."""
        host, _, _ = self.worker_id.rpartition(":")
        return f"{host or self.worker_id}:"

    async def recover(self) -> int:
        """Вернуть в очередь задачи, взятые этим хостом до рестарта (один процесс на хост)."""
        released = await self.queue.release_host(self.host_prefix)
        if released:
            log.warning("jobs_recovered_after_restart", count=released, worker_id=self.worker_id)
        return released

    async def process(self, job: Job) -> str:
        """Выполнить взятую задачу и отметить её; вернуть итог: done / retry / dead / cancelled."""
        with structlog.contextvars.bound_contextvars(
            account_id=str(job.account_id), job_id=str(job.id), job_kind=job.kind
        ):
            spec = self.registry.get(job.kind)
            if spec is None:
                log.error("job_unknown_kind")
                await self.queue.fail(job.id, f"неизвестный вид задачи: {job.kind}", retry=False)
                return "dead"
            hit = _CancelFlag()
            task = asyncio.ensure_future(spec.fn(job))
            watcher = asyncio.create_task(self._watch_cancel(job, task, hit))
            try:
                await task
            except JobCancelled:
                return await self._cancelled(job)
            except asyncio.CancelledError:
                current = asyncio.current_task()
                if hit.value and not (current is not None and current.cancelling()):
                    return await self._cancelled(job)  # отменил владелец, не остановка процесса
                await self._release(job)
                raise
            except Exception as exc:
                permanent = isinstance(exc, spec.permanent + ALWAYS_PERMANENT)
                log.warning(
                    "job_failed", error=type(exc).__name__, permanent=permanent, exc_info=True
                )
                status = await self.queue.fail(job.id, describe_error(exc), retry=not permanent)
                if status == "cancelled":
                    return await self._cancelled(job)
                if status == "dead":
                    await self._report_dead(job, exc)
                    return "dead"
                return "retry"
            finally:
                watcher.cancel()
            if await self.queue.complete(job.id) is False:  # отменили, пока шёл последний шаг
                return await self._cancelled(job)
            log.info("job_done", attempt=job.attempts)
            return "done"

    async def _watch_cancel(self, job: Job, task: asyncio.Future[object], hit: _CancelFlag) -> None:
        """Пока задача идёт: раз в cancel_poll секунд смотреть, не отменил ли её владелец."""
        while not task.done():
            await asyncio.sleep(self.cancel_poll)
            try:
                cancelled = await self.queue.is_cancelled(job.id)
            except Exception:
                log.warning("job_cancel_check_failed", exc_info=True)
                continue
            if cancelled and not task.done():
                hit.value = True
                task.cancel()
                return

    async def _cancelled(self, job: Job) -> str:
        """Итог «отменена»: результат выброшен; обновить статус-сообщение владельцу."""
        log.info("job_cancelled", job_kind=job.kind)
        if self.on_cancel is not None:
            try:
                await self.on_cancel(job)
            except Exception:
                log.exception("job_cancel_report_failed", job_id=str(job.id))
        return "cancelled"

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
        try:
            await self.recover()
        except Exception:
            log.exception("worker_recover_failed")
        tick = 0
        while not stop.is_set():
            if tick % self.stale_check_every == 0:
                await self._release_stale()
            tick += 1
            try:
                busy = await self._run_once_until(stop)
            except Exception:
                log.exception("worker_iteration_failed")
                busy = False
            if not busy:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=self.poll_interval)
        log.info("worker_stopped", worker_id=self.worker_id)

    async def _run_once_until(self, stop: asyncio.Event) -> bool:
        """run_once, но при `stop` посреди задачи ждать не дольше `shutdown_grace`."""
        work = asyncio.create_task(self.run_once())
        waiter = asyncio.create_task(stop.wait())
        try:
            await asyncio.wait({work, waiter}, return_when=asyncio.FIRST_COMPLETED)
            if not work.done():
                log.info("worker_stopping", grace=self.shutdown_grace)
                try:
                    await asyncio.wait_for(asyncio.shield(work), timeout=self.shutdown_grace)
                except TimeoutError:
                    log.warning("worker_job_interrupted", worker_id=self.worker_id)
                    work.cancel()
                    await asyncio.gather(work, return_exceptions=True)
                    return True
            return work.result()
        finally:
            waiter.cancel()
            if not work.done():  # отменили сам цикл (TaskGroup): отменить и задачу
                work.cancel()
                await asyncio.gather(work, return_exceptions=True)

    async def _report_dead(self, job: Job, exc: BaseException) -> None:
        """Сообщить, что задача остановлена; сбой сообщения не мешает воркеру."""
        if self.on_dead is None:
            return
        try:
            await self.on_dead(job, exc)
        except Exception:
            log.exception("job_dead_report_failed", job_id=str(job.id))

    async def _release(self, job: Job) -> None:
        """Снять блокировку прерванной задачи; ошибка БД не мешает остановке."""
        try:
            await asyncio.shield(self.queue.release(job.id))
            log.info("job_released", job_id=str(job.id), job_kind=job.kind)
        except Exception:
            log.exception("job_release_failed", job_id=str(job.id))

    async def _release_stale(self) -> None:
        try:
            released = await self.queue.release_stale(self.stale_after)
        except Exception:
            log.exception("worker_release_stale_failed")
            return
        if released:
            log.warning("jobs_released_stale", count=released)
