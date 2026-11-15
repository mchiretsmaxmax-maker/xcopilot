"""Дополнительные проверки 9A: паспорт, решения по черновикам, задачи, настройки."""

from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.brain_stand import (
    NOW,
    FakeBrainLLM,
    candidate_judge,
    knowledge,
    make_ctx,
    material,
    new_account,
    new_author,
)
from tests.fakes import FakeClock
from tests.fakes.embedder import FakeEmbedder
from tests.integration.db.brain_helpers import draft, insert, pack, user
from xcopilot.brain.compat import voice_compat
from xcopilot.brain.config import read_brain_config
from xcopilot.brain.evidence import record_from_review, revoke
from xcopilot.brain.jobs import BrainDeps, handle_detect, handle_nightly
from xcopilot.db.repos.passports import PassportsRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.errors import ConfigError
from xcopilot.jobs.queue import Job


async def passport(session: AsyncSession, acc: UUID) -> None:
    await PassportsRepo(session, acc).add(
        version=1,
        status="active",
        body={"summary": "спокойно, коротко, без восклицаний"},
        hard_rules=[{"name": "emoji", "value": "none"}],
        created_by="llm",
    )


async def test_voice_compat_uses_passport(session: AsyncSession) -> None:
    acc = await new_account(session)
    llm = FakeBrainLLM(judge=candidate_judge(allowed=0.8, natural=1.0))
    ctx = make_ctx(session, acc, llm=llm)
    assert await voice_compat(ctx, "Начни с цифры", None) == 1.0
    await passport(session, acc)
    p = {"summary": "спокойно", "hard_rules": [{"name": "emoji", "value": "none"}]}
    assert await voice_compat(ctx, "Начни с цифры", p) == 0.4
    [call] = llm.judge_calls
    assert call.purpose == "brain.voice_compat" and "hard_rules" in call.state


async def review_case(
    session: AsyncSession, action: str, *, reason: str | None = None, distance: float | None = None
) -> list[tuple[str, int, float]]:
    acc = await new_account(session)
    iid = await knowledge(session, acc)
    d = await draft(session, acc)
    p = await pack(session, acc)
    await insert(
        session,
        "knowledge_applications",
        account_id=acc,
        pack_id=p,
        item_id=iid,
        draft_id=d,
        slot="core",
    )
    rid = await insert(
        session,
        "reviews",
        account_id=acc,
        draft_id=d,
        user_id=await user(session),
        action=action,
        reason_code=reason,
        text_before="t",
        edit_distance=distance,
    )
    ctx = make_ctx(session, acc)
    await record_from_review(ctx, rid)
    assert await record_from_review(ctx, rid) == 0  # повтор не пишет
    rows = await session.execute(
        text("SELECT basis, polarity, w_source::float FROM knowledge_evidence WHERE item_id = :i"),
        {"i": iid},
    )
    return [(r[0], r[1], r[2]) for r in rows.all()]


async def test_record_from_review_weights(session: AsyncSession) -> None:
    assert await review_case(session, "approve") == [("review", 1, 1.0)]
    assert await review_case(session, "edit", distance=0.7) == [("review", -1, 0.5)]
    assert await review_case(session, "reject", reason="not_my_voice") == [("review", -1, 1.5)]
    assert await review_case(session, "reject", reason="topic") == []
    acc = await new_account(session)
    assert await record_from_review(make_ctx(session, acc), acc) == 0


async def test_revoke_needs_target(session: AsyncSession) -> None:
    acc = await new_account(session)
    with pytest.raises(ValueError):
        await revoke(make_ctx(session, acc), reason="owner")


def job(acc: UUID, kind: str, payload: dict[str, str] | None = None) -> Job:
    return Job(
        id=acc,
        account_id=acc,
        kind=kind,
        payload=payload or {},
        status="running",
        attempts=1,
        max_attempts=5,
        run_at=NOW,
        locked_by=None,
        last_error=None,
    )


async def test_detect_and_nightly_jobs(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with UnitOfWork(session_factory) as uow:
        acc = await new_account(uow.session)
        iid = await knowledge(uow.session, acc, provenance={"voice_compat": 0.9})
        sources = [
            await material(uow.session, acc, author_id=await new_author(uow.session, acc))
            for _ in range(4)
        ]
    clock = FakeClock(NOW + timedelta(minutes=5))
    deps = BrainDeps(
        uow_factory=lambda: UnitOfWork(session_factory),
        clock=clock,
        llm=FakeBrainLLM(),
        embedder=FakeEmbedder(),
    )
    for sid in sources:
        assert (
            await handle_detect(job(acc, "brain.detect", {"source_id": str(sid)}), deps=deps) == 1
        )
    assert await handle_nightly(job(acc, "brain.nightly"), deps=deps) == 1
    async with UnitOfWork(session_factory) as uow:
        status = (
            await uow.session.execute(
                text("SELECT status FROM knowledge_items WHERE id = :i"), {"i": iid}
            )
        ).scalar_one()
        snaps = (
            await uow.session.execute(
                text("SELECT count(*) FROM knowledge_snapshots WHERE account_id = :a"), {"a": acc}
            )
        ).scalar_one()
    assert (status, snaps) == ("probation", 1)
    with pytest.raises(Exception, match="не найден"):
        await handle_nightly(job(UUID(int=1), "brain.nightly"), deps=deps)


def test_brain_config_errors(tmp_path: Path) -> None:
    bad = tmp_path / "brain.toml"
    bad.write_text("[priors]\neff = [1, 1]\n")
    with pytest.raises(ConfigError):
        read_brain_config(bad)
    with pytest.raises(ConfigError):
        read_brain_config(tmp_path / "missing.toml")
    cfg = read_brain_config()
    assert cfg.decay_half_life_days.for_layer("voice") == 365
    assert cfg.decay_half_life_days.for_layer("craft") == 180
