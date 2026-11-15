"""Задачи писателя: бюджет, неверная задача, сбой, хвост тренда и темы через обработчики."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.writer_stand import DISTINCT, WriterStand, draft_reply, make_writer_stand, post_task
from xcopilot.bot import texts
from xcopilot.jobs.queue import Job
from xcopilot.llm.errors import TransientProviderError
from xcopilot.retrieval.context_pack import TaskSpec
from xcopilot.writer.drafts import WriterInputError
from xcopilot.writer.jobs import (
    WRITER_GENERATE_KIND,
    WRITER_TOPICS_KIND,
    generate_payload,
    handle_writer_generate,
    handle_writer_topics,
    process_writer_job,
    tail_payload,
)
from xcopilot.writer.probation import NoProbation
from xcopilot.writer.trend_tail import PostNotFound, PostRef

SF = async_sessionmaker[AsyncSession]


@dataclass
class TopicsSpy:
    sent: list[tuple[str, Any]] = field(default_factory=list)

    async def notify(
        self, account_id: UUID, text: str, keyboard: Any = None, roles: tuple[str, ...] = ("owner",)
    ) -> int:
        self.sent.append((text, keyboard))
        return 1


async def _job(ws: WriterStand, kind: str, payload: dict[str, Any]) -> Job:
    await ws.queue.enqueue(kind, payload, account_id=ws.account_id)
    return (await ws.queue.claim("w", 1))[0]


async def test_budget_job_completes_without_retry(session_factory: SF) -> None:
    ws = await make_writer_stand(session_factory, day_limit=1)
    job = await _job(ws, WRITER_GENERATE_KIND, generate_payload(post_task()))

    async def handler(j: Job) -> object:
        return await handle_writer_generate(j, writer=ws.service, notifier=ws.notifier)

    assert await process_writer_job(ws.queue, job, handler) is None
    after = await ws.queue.get(job.id)
    assert after is not None and after.status == "done"
    assert ws.notifier.sent == [(ws.account_id, texts.WRITER_BUDGET_STOP)]


async def test_unexpected_error_fails_job(ws: WriterStand) -> None:
    job = await _job(ws, WRITER_GENERATE_KIND, {"mode": "draft"})

    async def handler(j: Job) -> object:
        return await handle_writer_generate(j, writer=ws.service, notifier=ws.notifier)

    await process_writer_job(ws.queue, job, handler)
    after = await ws.queue.get(job.id)
    assert after is not None and after.status == "queued" and after.last_error == "KeyError"


async def test_tail_job_sends_analysis_and_cards(ws: WriterStand) -> None:
    source = await ws.foreign_post("За 30 дней я потерял 12% на мостах. Вывод один.")
    ws.writer.push(draft_reply(*DISTINCT))
    job = await _job(ws, WRITER_GENERATE_KIND, tail_payload(PostRef(source_id=source)))

    async def handler(j: Job) -> object:
        return await handle_writer_generate(j, writer=ws.service, notifier=ws.notifier)

    await process_writer_job(ws.queue, job, handler)
    sent = [t for _, t in ws.notifier.sent]
    assert sent[0] == texts.trend_why("короткий зачин с цифрой цепляет")
    assert sum(t.startswith("Вариант ") for t in sent) == 3


async def test_missing_post_stops_tail_job(ws: WriterStand) -> None:
    job = await _job(
        ws, WRITER_GENERATE_KIND, tail_payload(PostRef(url="https://x.com/a/status/5"))
    )

    async def handler(j: Job) -> object:
        return await handle_writer_generate(j, writer=ws.service, notifier=ws.notifier)

    await process_writer_job(ws.queue, job, handler)
    after = await ws.queue.get(job.id)
    assert after is not None and after.status == "done"
    with pytest.raises(PostNotFound):
        await ws.service.generate_tail(ws.account_id, PostRef())


async def test_topics_job_unavailable_and_empty(ws: WriterStand) -> None:
    spy = TopicsSpy()
    ws.writer.push(*[TransientProviderError("anthropic", status=503) for _ in range(10)])
    job = await _job(ws, WRITER_TOPICS_KIND, {"n": 5})

    async def handler(j: Job) -> object:
        return await handle_writer_topics(j, topics=ws.topics, notifier=spy)

    await process_writer_job(ws.queue, job, handler)
    assert spy.sent == [(texts.WRITER_UNAVAILABLE, None)]
    after = await ws.queue.get(job.id)
    assert after is not None and after.status == "queued"


async def test_invalid_inputs(ws: WriterStand) -> None:
    with pytest.raises(WriterInputError):
        await ws.service.generate(ws.account_id, post_task(fmt="story"))
    with pytest.raises(WriterInputError):
        await ws.service.generate(ws.account_id, post_task(), n=0)
    with pytest.raises(ValueError):
        await ws.topics.add_manual_topic(ws.account_id, " a ")
    assert await NoProbation().pick(None, ws.account_id, post_task()) is None  # type: ignore[arg-type]
    assert isinstance(TaskSpec.model_validate(generate_payload(post_task())["task"]), TaskSpec)


async def test_writer_error_marks_run_failed(ws: WriterStand) -> None:
    ws.writer.push("не json", "снова не json")
    with pytest.raises(Exception, match="writer"):
        await ws.service.generate(ws.account_id, post_task())
    rows = await ws.rows("SELECT status, error FROM generation_runs")
    assert rows == [("failed", "writer_error")]
