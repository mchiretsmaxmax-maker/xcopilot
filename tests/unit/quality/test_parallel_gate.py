"""Параллельные ворота сохраняют порядок и завершают все дочерние задачи при отмене."""

import asyncio
from typing import Any, cast
from uuid import uuid4

import pytest

from xcopilot.jobs.queue import Job
from xcopilot.quality.jobs import handle_quality_gate
from xcopilot.quality.policy import Decision


async def test_three_gates_overlap_and_results_keep_payload_order() -> None:
    ids = [uuid4() for _ in range(3)]
    entered: set[object] = set()
    ready = asyncio.Event()

    class Gate:
        async def run_gate(self, account: object, draft: object) -> Decision:
            entered.add(draft)
            if len(entered) == 3:
                ready.set()
            await asyncio.wait_for(ready.wait(), 0.3)
            await asyncio.sleep((2 - ids.index(draft)) * 0.005)
            return Decision(outcome="pass", draft_id=cast(Any, draft))

    job = cast(Job, type("J", (), {"account_id": uuid4(), "payload": {
        "draft_ids": [str(i) for i in ids],
    }})())
    decisions = await handle_quality_gate(job, gate=cast(Any, Gate()))
    assert [d.draft_id for d in decisions] == ids


async def test_gate_failure_cancels_and_joins_other_checks() -> None:
    ready = asyncio.Event()
    stopped = asyncio.Event()
    ids = [uuid4(), uuid4()]

    class Gate:
        async def run_gate(self, account: object, draft: object) -> Decision:
            if draft == ids[0]:
                await ready.wait()
                raise ValueError("bad draft")
            ready.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
            return Decision(outcome="pass")

    job = cast(Job, type("J", (), {"account_id": uuid4(), "payload": {
        "draft_ids": [str(i) for i in ids],
    }})())
    with pytest.raises(ValueError, match="bad draft"):
        await asyncio.wait_for(handle_quality_gate(job, gate=cast(Any, Gate())), 0.3)
    assert stopped.is_set()
