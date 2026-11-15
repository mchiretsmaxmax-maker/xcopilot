"""Извлечение знаний (этап 9A, задача 4): пачки, контрастная выборка, приём кандидатов."""

from datetime import timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.brain_stand import (
    NOW,
    FakeBrainLLM,
    analysis_fields,
    candidate_judge,
    knowledge,
    make_ctx,
    material,
    new_account,
    new_author,
)
from tests.fakes import FakeClock
from tests.fakes.embedder import FakeEmbedder
from tests.integration.brain.extract_helpers import candidate, count, items_of, result
from xcopilot.brain import extract
from xcopilot.brain.jobs import BrainDeps, handle_extract
from xcopilot.db.uow import UnitOfWork
from xcopilot.jobs.queue import Job


async def materials(
    session: AsyncSession, acc: UUID, n: int, *, kind: str = "post", stance: str = "inspiration"
) -> list[UUID]:
    out = []
    for _ in range(n):
        auth = await new_author(session, acc)
        out.append(
            await material(
                session,
                acc,
                kind=kind,
                stance=stance,
                author_id=auth,
                posted_at=NOW - timedelta(hours=1),
            )
        )
    return out


async def test_batches_per_format_max_25(session: AsyncSession) -> None:
    acc = await new_account(session)
    posts = await materials(session, acc, 30)
    threads = await materials(session, acc, 12, kind="thread")
    await materials(session, acc, 3, kind="reply")
    clock = FakeClock(NOW)
    batches = await extract.build_batches(make_ctx(session, acc, clock=clock))
    shape = sorted((b.format, len(b.materials)) for b in batches)
    assert shape == [("post", 5), ("post", 25), ("thread", 12)]
    assert {m for b in batches if b.format == "post" for m in b.materials} == set(posts)
    assert {m for b in batches if b.format == "thread" for m in b.materials} == set(threads)
    # мало ответов: ждут сутки, потом идут отдельной пачкой
    clock.advance(timedelta(days=2).total_seconds())
    later = await extract.build_batches(make_ctx(session, acc, clock=clock))
    assert ("reply", 3) in {(b.format, len(b.materials)) for b in later}


async def test_own_history_uses_contrast_sample(session: AsyncSession) -> None:
    acc = await new_account(session)
    own: list[UUID] = []
    for i in range(10):
        own.append(
            await material(
                session,
                acc,
                stance="own",
                origin="history_backfill",
                tier="normal",
                signal_origin="own_sync",
                outcome_basis="own_baseline",
                pct_engagement=(i + 0.5) / 10,
            )
        )
    ctx = make_ctx(session, acc)
    sample = await extract.contrast_sample(ctx, share=0.2)
    assert set(sample) == {own[0], own[1], own[8], own[9]}
    batches = await extract.build_batches(ctx)
    history = [b for b in batches if b.own_history]
    assert len(history) == 1 and set(history[0].materials) == set(sample)
    assert not ({own[2], own[5]} & {m for b in batches for m in b.materials})


async def run_batch(
    session: AsyncSession, acc: UUID, llm: FakeBrainLLM, n: int = 3, stance: str = "inspiration"
) -> list[UUID]:
    srcs = await materials(session, acc, n, stance=stance)
    ctx = make_ctx(session, acc, llm=llm)
    batch = extract.Batch(account_id=acc, format="post", materials=srcs)
    res = await extract.extract(ctx, batch)
    return await extract.ingest_candidates(ctx, res, batch)


async def test_candidate_needs_two_supporting_materials(session: AsyncSession) -> None:
    acc = await new_account(session)
    llm = FakeBrainLLM(judge=candidate_judge()).push(result(candidate(supporting=["M1"])))
    assert await run_batch(session, acc, llm) == []
    assert await items_of(session, acc) == []


async def test_voice_candidate_from_foreign_material_rejected(session: AsyncSession) -> None:
    voice = candidate(
        kind="thesis",
        layer="voice",
        element="tone",
        target_metric="quality",
        statement="Короткие спокойные фразы без восклицаний звучат как я",
        guidance="Пиши короткими спокойными фразами без восклицаний",
        detector={"type": "none"},
    )
    acc = await new_account(session)
    llm = FakeBrainLLM(judge=candidate_judge(layer="voice")).push(result(voice))
    assert await run_batch(session, acc, llm) == []
    acc2 = await new_account(session)
    llm2 = FakeBrainLLM(judge=candidate_judge(layer="voice")).push(result(voice))
    [iid] = await run_batch(session, acc2, llm2, stance="own")
    [row] = await items_of(session, acc2)
    assert (row["id"], row["layer"], row["risk_class"]) == (iid, "voice", "A")


async def test_same_as_existing_adds_evidence_not_item(session: AsyncSession) -> None:
    acc = await new_account(session)
    old = await knowledge(session, acc, status="active")
    llm = FakeBrainLLM(judge=candidate_judge()).push(result(candidate(same_as_existing="K1")))
    touched = await run_batch(session, acc, llm)
    assert touched == [old]
    assert [r["id"] for r in await items_of(session, acc)] == [old]
    assert (
        await count(session, "SELECT count(*) FROM knowledge_evidence WHERE item_id = :i", i=old)
        == 2
    )


async def test_extraction_prompt_contains_analyses_not_raw_text(session: AsyncSession) -> None:
    acc = await new_account(session)
    raw = "СЕКРЕТНАЯФРАЗА: 12 провалов за год"
    srcs = [
        await material(
            session,
            acc,
            text=raw,
            author_id=await new_author(session, acc),
            analysis=analysis_fields(hook_text=raw, human_markers=[raw], hook_type="number"),
        )
        for _ in range(2)
    ]
    llm = FakeBrainLLM().push(result())
    await extract.extract(
        make_ctx(session, acc, llm=llm),
        extract.Batch(account_id=acc, format="post", materials=srcs),
    )
    [call] = llm.gen_calls
    assert call.prompt_key == "brain.extract_knowledge.v1"
    assert call.role == "writer"
    prompt = call.system + call.user
    assert "СЕКРЕТНАЯФРАЗА" not in prompt
    assert "M1" in call.user and "M2" in call.user
    assert "hook_type" in call.user and "number" in call.user
    assert "features.has_number_first_line" in call.user  # разрешённые поля детектора


async def test_extraction_job_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with UnitOfWork(session_factory) as uow:
        acc = await new_account(uow.session)
        await materials(uow.session, acc, 12)
    llm = FakeBrainLLM(judge=candidate_judge()).push(result(candidate()))
    deps = BrainDeps(
        uow_factory=lambda: UnitOfWork(session_factory),
        clock=FakeClock(NOW),
        llm=llm,
        embedder=FakeEmbedder(),
    )
    job = Job(
        id=acc,
        account_id=acc,
        kind="brain.extract",
        payload={},
        status="running",
        attempts=1,
        max_attempts=5,
        run_at=NOW,
        locked_by=None,
        last_error=None,
    )
    first = await handle_extract(job, deps=deps)
    assert first == 1 and len(llm.gen_calls) == 1

    async def totals() -> tuple[int, int]:
        async with UnitOfWork(session_factory) as uow:
            s = uow.session
            return (
                await count(s, "SELECT count(*) FROM knowledge_items WHERE account_id = :a", a=acc),
                await count(
                    s, "SELECT count(*) FROM knowledge_evidence WHERE account_id = :a", a=acc
                ),
            )

    before = await totals()
    assert await handle_extract(job, deps=deps) == 0
    assert len(llm.gen_calls) == 1  # повторный прогон не зовёт модель
    assert await totals() == before
