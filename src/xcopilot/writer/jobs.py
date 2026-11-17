"""Задачи очереди писателя: `writer.generate`, `writer.topics` (ТЗ, этап 10, задачи 9–10).

Воркера в проекте пока нет: обработчики вызываются напрямую (как ingest/style), а
`process_writer_job` — то, что воркер сделает с задачей:
- успех → `complete`, карточки уходят владельцу;
- модель-автор недоступна → владелец уведомлён (на первой попытке), задача → `fail` и
  повторится с паузой; другие модели черновики не пишут;
- бюджет исчерпан или задача неверна → `complete` без повтора (повтор не поможет).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol
from uuid import UUID

import structlog

from xcopilot.bot import texts
from xcopilot.errors import BudgetExceeded
from xcopilot.jobs.queue import Job, JobCancelled, JobQueue
from xcopilot.llm.errors import ProviderUnavailable
from xcopilot.quality.jobs import QUALITY_GATE_KIND, gate_idempotency_key, gate_payload
from xcopilot.retrieval.context_pack import TaskSpec
from xcopilot.writer.drafts import WriterInputError, WriterService
from xcopilot.writer.schemas import GenerationRun, TargetPost, Topic
from xcopilot.writer.topics import TopicService
from xcopilot.writer.trend_tail import PostNotFound, PostRef, why_summary

log = structlog.get_logger()

WRITER_GENERATE_KIND = "writer.generate"
WRITER_TOPICS_KIND = "writer.topics"


class OwnerNotifier(Protocol):
    async def notify_owner(self, account_id: UUID, text: str) -> None: ...


class TopicsNotifier(Protocol):
    async def notify(
        self,
        account_id: UUID,
        text: str,
        keyboard: Any = None,
        roles: tuple[str, ...] = ("owner",),
    ) -> int: ...


class ProgressSink(Protocol):
    async def step(self, key: str | None, stage: str) -> None: ...

    async def done(self, key: str | None) -> None: ...


class WriterUnavailableError(Exception):
    """Модель-автор недоступна: задачу повторить позже."""


def generate_payload(
    task: TaskSpec,
    *,
    hint: str | None = None,
    topic_id: UUID | None = None,
    target: TargetPost | None = None,
    view: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Payload `writer.generate`; `view` — как показать варианты (режим ответов, ADR 0032)."""
    payload: dict[str, Any] = {
        "mode": "draft",
        "task": task.model_dump(mode="json"),
        "hint": hint,
        "topic_id": str(topic_id) if topic_id else None,
        "target": target.model_dump(mode="json") if target else None,
    }
    if view is not None:
        payload["view"] = view
    return payload


def tail_payload(post_ref: PostRef, *, own_topic: str | None = None) -> dict[str, Any]:
    return {
        "mode": "trend_tail",
        "post_ref": post_ref.model_dump(mode="json"),
        "own_topic": own_topic,
    }


def cards(run: GenerationRun, *, with_drafts: bool = True) -> list[str]:
    """Сообщения владельцу: разбор (для хвоста), предупреждения, карточки вариантов."""
    out: list[str] = []
    if run.analysis is not None:
        summary = why_summary(run.analysis) if run.analysis.available else ""
        out.append(texts.trend_why(summary) if summary else texts.TREND_NO_ANALYSIS)
    out += [w for w in (texts.writer_warning(c) for c in run.warnings) if w]
    if not with_drafts:
        return out
    total = len(run.drafts)
    out += [
        texts.draft_card(d.variant_no, total, d.text, d.parts, d.probation_guidance)
        for d in run.drafts
    ]
    return out


async def handle_writer_generate(
    job: Job,
    *,
    writer: WriterService,
    notifier: OwnerNotifier,
    gate_queue: JobQueue | None = None,
    progress: ProgressSink | None = None,
) -> GenerationRun:
    """Задача `writer.generate`: черновики по задаче или хвост тренда.

    С `gate_queue` (так собирает воркер, этап 12) варианты уходят в задачу `quality.gate`,
    а карточки владелец получит после решения ворот; разбор хвоста и предупреждения писателя
    приходят сразу. Без очереди — старый путь этапа 10: простые карточки сразу.
    """
    payload = job.payload
    pkey = payload.get("progress")
    if progress is not None:
        await progress.step(pkey, "write")
    try:
        if payload.get("mode") == "trend_tail":
            run = await writer.generate_tail(
                job.account_id,
                PostRef.model_validate(payload["post_ref"]),
                payload.get("own_topic"),
                job_id=job.id,
            )
        else:
            target = payload.get("target")
            topic_id = payload.get("topic_id")
            run = await writer.generate(
                job.account_id,
                TaskSpec.model_validate(payload["task"]),
                hint=payload.get("hint"),
                topic_id=UUID(topic_id) if topic_id else None,
                target=TargetPost.model_validate(target) if target else None,
                job_id=job.id,
            )
    except ProviderUnavailable as exc:
        if job.attempts <= 1:
            await notifier.notify_owner(job.account_id, texts.WRITER_UNAVAILABLE)
        raise WriterUnavailableError(str(exc)) from exc
    except BudgetExceeded:
        await notifier.notify_owner(job.account_id, texts.WRITER_BUDGET_STOP)
        raise
    if gate_queue is not None and await gate_queue.is_cancelled(job.id):
        raise JobCancelled(job.id)  # отменили, пока писала модель: результат выбрасывается
    if gate_queue is not None and run.drafts:
        # повтор задачи (run.reused) безопасен: ключ идемпотентности не ставит ворота дважды
        ids = [d.draft_id for d in run.drafts]
        await gate_queue.enqueue(
            QUALITY_GATE_KIND,
            gate_payload(ids, view=payload.get("view")) | ({"progress": pkey} if pkey else {}),
            account_id=job.account_id,
            idempotency_key=gate_idempotency_key(ids),
        )
        if not run.reused:
            for message in cards(run, with_drafts=False):
                await notifier.notify_owner(job.account_id, message)
        if progress is not None:
            await progress.step(pkey, "gate")
        return run
    if not run.reused:
        messages = cards(run) if run.drafts else [texts.WRITER_FAILED]
        for message in messages:
            await notifier.notify_owner(job.account_id, message)
    if progress is not None:
        await progress.done(pkey)
    return run


async def handle_writer_topics(
    job: Job,
    *,
    topics: TopicService,
    notifier: TopicsNotifier,
    progress: ProgressSink | None = None,
) -> list[Topic]:
    """Задача `writer.topics`: пять тем, у каждой кнопки [Писать] и [Не то]."""
    from xcopilot.bot import keyboards  # бот зависит от писателя, не наоборот

    pkey = job.payload.get("progress")
    if progress is not None:
        await progress.step(pkey, "topics")
    try:
        found = await topics.suggest_topics(job.account_id, n=int(job.payload.get("n", 5)))
    except ProviderUnavailable as exc:
        if job.attempts <= 1:
            await notifier.notify(job.account_id, texts.WRITER_UNAVAILABLE)
        raise WriterUnavailableError(str(exc)) from exc
    if not found:
        await notifier.notify(job.account_id, texts.TOPICS_EMPTY)
    for t in found:
        await notifier.notify(
            job.account_id,
            texts.topic_card(t.title, t.angle, t.rationale, t.format),
            keyboard=keyboards.topic_actions(t.id, t.format),
        )
    if progress is not None:
        await progress.done(pkey)
    return found


PERMANENT: Sequence[type[Exception]] = (BudgetExceeded, WriterInputError, PostNotFound)


async def process_writer_job(
    queue: JobQueue, job: Job, handler: Callable[[Job], Awaitable[Any]]
) -> Any:
    """Выполнить задачу писателя и отметить её в очереди (повтор только при недоступном авторе)."""
    try:
        result = await handler(job)
    except WriterUnavailableError:
        await queue.fail(job.id, "writer_unavailable")
        return None
    except tuple(PERMANENT) as exc:
        log.warning("writer_job_stopped", job_id=str(job.id), error=type(exc).__name__)
        await queue.complete(job.id)
        return None
    except Exception as exc:
        log.exception("writer_job_failed", job_id=str(job.id))
        await queue.fail(job.id, type(exc).__name__)
        return None
    await queue.complete(job.id)
    return result
