"""Параллельные ворота сохраняют порядок и завершают все дочерние задачи при отмене."""

import asyncio
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from xcopilot.jobs.queue import Job
from xcopilot.quality.jobs import handle_quality_gate
from xcopilot.quality.policy import Decision


async def test_three_gates_overlap_and_results_keep_payload_order() -> None:
    ids = [uuid4() for _ in range(3)]
    entered: set[object] = set()
    ready = asyncio.Event()

    class Gate:
        async def run_gate(self, account: UUID, draft: UUID) -> Decision:
            entered.add(draft)
            if len(entered) == 3:
                ready.set()
            await asyncio.wait_for(ready.wait(), 0.3)
            await asyncio.sleep((2 - ids.index(draft)) * 0.005)
            return Decision(outcome="pass", draft_id=cast(Any, draft))

    job = cast(
        Job,
        type(
            "J",
            (),
            {
                "account_id": uuid4(),
                "payload": {
                    "draft_ids": [str(i) for i in ids],
                },
            },
        )(),
    )
    decisions = await handle_quality_gate(job, gate=cast(Any, Gate()))
    assert [d.draft_id for d in decisions] == ids


async def test_gate_failure_cancels_and_joins_other_checks() -> None:
    ready = asyncio.Event()
    stopped = asyncio.Event()
    ids = [uuid4(), uuid4()]

    class Gate:
        async def run_gate(self, account: UUID, draft: UUID) -> Decision:
            if draft == ids[0]:
                await ready.wait()
                raise ValueError("bad draft")
            ready.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
            return Decision(outcome="pass")

    job = cast(
        Job,
        type(
            "J",
            (),
            {
                "account_id": uuid4(),
                "payload": {
                    "draft_ids": [str(i) for i in ids],
                },
            },
        )(),
    )
    with pytest.raises(ValueError, match="bad draft"):
        await asyncio.wait_for(handle_quality_gate(job, gate=cast(Any, Gate())), 0.3)
    assert stopped.is_set()


async def test_batch_is_bounded_and_duplicate_draft_runs_once() -> None:
    ids = [uuid4() for _ in range(5)]
    active = peak = calls = 0

    class Gate:
        async def run_gate(self, account: UUID, draft: UUID) -> Decision:
            nonlocal active, peak, calls
            calls += 1
            active += 1
            peak = max(active, peak)
            await asyncio.sleep(0)
            active -= 1
            return Decision(outcome="pass", draft_id=draft)

    job = cast(
        Job,
        type(
            "J",
            (),
            {
                "account_id": uuid4(),
                "payload": {
                    "draft_ids": [str(i) for i in [*ids, ids[0]]],
                },
            },
        )(),
    )
    results = await handle_quality_gate(job, gate=cast(Any, Gate()))
    assert peak == 3 and calls == 5
    assert [r.draft_id for r in results] == [*ids, ids[0]]


async def test_cancelling_batch_waits_for_all_checks_to_clean_up() -> None:
    started = asyncio.Event()
    entered = cleaned = 0
    ids = [uuid4() for _ in range(3)]

    class Gate:
        async def run_gate(self, account: UUID, draft: UUID) -> Decision:
            nonlocal entered, cleaned
            entered += 1
            if entered == 3:
                started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0.01)
                cleaned += 1
            return Decision(outcome="pass")

    job = cast(
        Job,
        type(
            "J",
            (),
            {
                "account_id": uuid4(),
                "payload": {
                    "draft_ids": [str(i) for i in ids],
                },
            },
        )(),
    )
    task = asyncio.create_task(handle_quality_gate(job, gate=cast(Any, Gate())))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned == 3
