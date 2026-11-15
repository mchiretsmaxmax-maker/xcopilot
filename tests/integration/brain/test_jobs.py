"""Задачи и подписчик этапа 7A — дополнительные тесты."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from tests.brain_stand import BrainStand, analysis_json
from tests.ingest_stand import RecordingNotifier
from xcopilot.brain import signals
from xcopilot.brain.analyze import card_view
from xcopilot.brain.jobs import (
    ANALYZE_KIND,
    enqueue_analysis,
    handle_brain_analyze,
    handle_brain_history_import,
    handle_brain_outcome,
)
from xcopilot.jobs.queue import Job

TEXT = "i lost $40k in 3 weeks\nthen i stopped\nnow i sleep fine"


def _job(brain: BrainStand, kind: str, payload: dict[str, object]) -> Job:
    return Job(
        id=uuid4(),
        account_id=brain.account_id,
        kind=kind,
        payload=payload,
        status="running",
        attempts=1,
        max_attempts=5,
        run_at=brain.clock.now(),
        locked_by="test",
        last_error=None,
    )


async def test_ingested_event_enqueues_analysis_once(brain: BrainStand) -> None:
    sid = await brain.material(TEXT)
    async with brain.uow() as uow:
        props = {"source_id": str(sid), "kind": "liked_sample", "chunks": 1, "duplicates": 0}
        await enqueue_analysis(uow.session, brain.account_id, "source.ingested", props)
        await enqueue_analysis(uow.session, brain.account_id, "source.ingested", props)
        await enqueue_analysis(uow.session, brain.account_id, "material.analyzed", props)
    assert await brain.count("jobs", "kind = :k", k=ANALYZE_KIND) == 1


async def test_job_handlers_analyze_outcome_history(brain: BrainStand) -> None:
    sid = await brain.material(TEXT, posted_at=brain.clock.now() - timedelta(days=2))
    brain.llm.push(analysis_json(TEXT))
    assert await handle_brain_analyze(
        _job(brain, ANALYZE_KIND, {"source_id": str(sid)}), analyzer=brain.analyzer
    )
    assert not await handle_brain_analyze(
        _job(brain, ANALYZE_KIND, {"source_id": str(sid)}), analyzer=brain.analyzer
    )  # уже разобран
    async with brain.uow() as uow:
        await signals.add_signal(
            uow.session, brain.account_id, sid, signals.Signal(origin="x_api", views=1000, likes=5)
        )
    basis = await handle_brain_outcome(
        _job(brain, "brain.outcome", {"source_id": str(sid)}),
        uow_factory=brain.uow,
        clock=brain.clock,
    )
    assert basis == "rates"

    note = RecordingNotifier()
    n = await handle_brain_history_import(
        _job(brain, "brain.history_import", {"limit": 10}), importer=brain.importer, notifier=note
    )
    assert n == 0 and note.sent[0][1].startswith("Загрузил 0 твоих постов.")


async def test_card_view_skips_history_and_shows_titles(brain: BrainStand) -> None:
    sid = await brain.material(TEXT)
    brain.llm.push(analysis_json(TEXT))
    await brain.analyzer.analyze(brain.account_id, sid)
    async with brain.uow() as uow:
        view = await card_view(uow.session, brain.account_id, sid)
    assert view is not None
    assert (view.hook_title, view.structure_title) == ("личная цифра", "история с поворотом")
    assert view.device_titles == ["конкретная цифра", "короткие строки"]
    assert view.outcome_known is False

    hist = await brain.material(TEXT + " h", stance="own", origin="history_backfill")
    async with brain.uow() as uow:
        assert await card_view(uow.session, brain.account_id, hist) is None
        assert await card_view(uow.session, brain.account_id, await brain.material("x")) is None
