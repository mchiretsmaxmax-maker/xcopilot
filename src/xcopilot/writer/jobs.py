"""Задачи очереди писателя: `writer.generate`, `writer.topics` (ТЗ, этап 10, задачи 9–10).

Воркера в проекте пока нет: обработчики вызываются напрямую (как ingest/style), а
`process_writer_job` — то, что воркер сделает с задачей (завершить или вернуть в очередь).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from uuid import UUID

from xcopilot.jobs.queue import Job, JobQueue
from xcopilot.retrieval.context_pack import TaskSpec
from xcopilot.writer.drafts import WriterService
from xcopilot.writer.schemas import GenerationRun, TargetPost, Topic
from xcopilot.writer.topics import TopicService
from xcopilot.writer.trend_tail import PostRef

WRITER_GENERATE_KIND = "writer.generate"
WRITER_TOPICS_KIND = "writer.topics"


class OwnerNotifier(Protocol):
    async def notify_owner(self, account_id: UUID, text: str) -> None: ...


class WriterUnavailableError(Exception):
    """Модель-автор недоступна: задачу повторить позже."""


def generate_payload(
    task: TaskSpec,
    *,
    hint: str | None = None,
    topic_id: UUID | None = None,
    target: TargetPost | None = None,
) -> dict[str, Any]:
    raise NotImplementedError


def tail_payload(post_ref: PostRef, *, own_topic: str | None = None) -> dict[str, Any]:
    raise NotImplementedError


async def handle_writer_generate(
    job: Job, *, writer: WriterService, notifier: OwnerNotifier
) -> GenerationRun:
    raise NotImplementedError


async def handle_writer_topics(
    job: Job, *, topics: TopicService, notifier: OwnerNotifier
) -> list[Topic]:
    raise NotImplementedError


async def process_writer_job(
    queue: JobQueue, job: Job, handler: Callable[[Job], Awaitable[Any]]
) -> Any:
    raise NotImplementedError
