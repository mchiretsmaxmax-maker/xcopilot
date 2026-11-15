"""Задачи очереди писателя: Claude недоступен — повтор и уведомление; повтор задачи идемпотентен."""

from __future__ import annotations

from tests.writer_stand import DISTINCT, WriterStand, draft_reply, post_task
from xcopilot.bot import texts
from xcopilot.jobs.queue import Job
from xcopilot.llm.errors import TransientProviderError
from xcopilot.writer.jobs import (
    WRITER_GENERATE_KIND,
    generate_payload,
    handle_writer_generate,
    process_writer_job,
)
from xcopilot.writer.schemas import GenerationRun


async def _claim(ws: WriterStand) -> Job:
    await ws.queue.enqueue(
        WRITER_GENERATE_KIND, generate_payload(post_task()), account_id=ws.account_id
    )
    jobs = await ws.queue.claim("w1", 1)
    assert len(jobs) == 1
    return jobs[0]


async def test_writer_unavailable_requeues_and_notifies(ws: WriterStand) -> None:
    ws.writer.push(*[TransientProviderError("anthropic", status=529) for _ in range(10)])
    job = await _claim(ws)

    async def handler(j: Job) -> GenerationRun:
        return await handle_writer_generate(j, writer=ws.service, notifier=ws.notifier)

    await process_writer_job(ws.queue, job, handler)

    after = await ws.queue.get(job.id)
    assert after is not None
    assert after.status == "queued" and after.run_at > ws.clock.now()  # повтор позже
    assert ws.notifier.sent == [(ws.account_id, texts.WRITER_UNAVAILABLE)]
    assert ws.cheap.calls == [] and ws.judge.calls == []  # другие модели не пишут
    assert await ws.count("drafts") == 0


async def test_generation_job_is_idempotent(ws: WriterStand) -> None:
    ws.writer.push(draft_reply(*DISTINCT))
    job = await _claim(ws)

    first = await handle_writer_generate(job, writer=ws.service, notifier=ws.notifier)
    second = await handle_writer_generate(job, writer=ws.service, notifier=ws.notifier)

    assert len(ws.writer.calls) == 1
    assert second.reused and second.run_id == first.run_id
    assert [d.draft_id for d in second.drafts] == [d.draft_id for d in first.drafts]
    assert await ws.count("generation_runs") == 1
    assert await ws.count("drafts") == 3
