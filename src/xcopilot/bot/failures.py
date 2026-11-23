"""Понятные владельцу сообщения о сорвавшихся задачах (постоянная ошибка или конец повторов)."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

import structlog

from xcopilot.bot.progress import SOURCE_PREFIX, ProgressSink
from xcopilot.jobs.queue import Job
from xcopilot.jobs.worker import JobDeadlineExceeded
from xcopilot.llm.errors import InteractiveTimeoutError, PermanentProviderError

log = structlog.get_logger()

# вид задачи → («недоступен» при постоянной ошибке, «не справился» после повторов)
SUBJECTS: dict[str, tuple[str, str]] = {
    "writer.generate": ("Писатель недоступен", "Писатель не справился"),
    "writer.topics": ("Писатель недоступен", "Подбор тем не удался"),
    "quality.gate": ("Проверка качества недоступна", "Проверка качества не удалась"),
    "ingest.process": ("Разбор материала недоступен", "Разбор материала не удался"),
    "brain.analyze": ("Разбор материала недоступен", "Разбор материала не удался"),
    "brain.history_import": ("Загрузка истории недоступна", "Загрузка истории не удалась"),
}


class OwnerNotifier(Protocol):
    async def notify_owner(self, account_id: UUID, text: str) -> None: ...


def reason(exc: BaseException) -> str:
    """Причина по-русски для владельца."""
    if isinstance(exc, JobDeadlineExceeded | InteractiveTimeoutError):
        return "истекло время ожидания. Попробуй запрос ещё раз"
    if isinstance(exc, PermanentProviderError):
        if exc.kind == "cli_not_found":
            return "Codex CLI не найден"
        if exc.provider == "codex_cli" and exc.kind == "not_logged_in":
            return "нужен вход в ChatGPT через codex login"
        if exc.status in (401, 403):
            return "ключ модели не принят"
        if exc.status is not None:
            return f"запрос отклонён (HTTP {exc.status})"
        return f"ошибка провайдера ({exc.kind or exc.provider})"
    return "не получилось после нескольких попыток"


def reason_from_error(last_error: str | None) -> str:
    """То же по сохранённой в очереди строке `Тип: текст`."""
    text = last_error or ""
    if "JobDeadlineExceeded" in text or "InteractiveTimeoutError" in text:
        return "истекло время ожидания"
    if "cli_not_found" in text:
        return "Codex CLI не найден"
    if "codex_cli: not_logged_in" in text:
        return "нужен вход в ChatGPT через codex login"
    if "HTTP 401" in text or "HTTP 403" in text:
        return "ключ модели не принят"
    return "не получилось после нескольких попыток"


def failure_text(kind: str, exc: BaseException) -> str:
    if isinstance(exc, JobDeadlineExceeded | InteractiveTimeoutError):
        return "Ответ занял слишком много времени. Задача остановлена. Попробуй запрос ещё раз."
    unavailable, failed = SUBJECTS.get(kind, ("Задача недоступна", "Задача не выполнена"))
    if isinstance(exc, PermanentProviderError):
        return f"{unavailable}: {reason(exc)}. Задача остановлена."
    return f"{failed}: {reason(exc)}. Задача остановлена, подробности — /status."


class DeadJobReporter:
    """`Worker(on_dead=…)`: статус правится на «❌», и владельцу уходит сообщение."""

    def __init__(self, notifier: OwnerNotifier, board: ProgressSink | None) -> None:
        self._notifier = notifier
        self._board = board

    async def __call__(self, job: Job, exc: BaseException) -> None:
        if job.kind not in SUBJECTS or job.payload.get("silent"):
            return  # служебные и тихие (сбор ответов) задачи — алерт job_dead (этап 15)
        if self._board is not None:
            key = job.payload.get("progress")
            if key is None and "source_id" in job.payload:
                key = f"{SOURCE_PREFIX}{job.payload['source_id']}"
            await self._board.fail(key, reason(exc))
        await self._notifier.notify_owner(job.account_id, failure_text(job.kind, exc))
        log.info("job_dead_reported", job_kind=job.kind)
