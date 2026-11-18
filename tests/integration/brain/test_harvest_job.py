"""R3 (ADR 0034, пп. 1–5, 7, 9): сбор ответов и цитат под постом из «💬 Ответы».

Стенд ингеста на настоящей БД: Harvester пишет материалы сбора, ingest.process их индексирует,
Analyzer разбирает. Владелец не получает ни одного сообщения от сбора.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.brain_stand import analysis_json
from tests.fakes import FakeClock
from tests.fakes.llm import FakeLLM
from tests.ingest_stand import Stand, build_gateway, make_stand
from tests.pack_stand import build as build_knowledge_pack
from tests.pack_stand import kitem
from tests.pack_stand import task as pack_task
from xcopilot.app import SLOW_KINDS
from xcopilot.bot.failures import DeadJobReporter
from xcopilot.brain import outcomes
from xcopilot.brain.analyze import Analyzer
from xcopilot.brain.harvest import HarvestConfig, Harvester, harvest_payload
from xcopilot.brain.jobs import HARVEST_KIND, RECHECK_KIND, enqueue_harvest, handle_harvest
from xcopilot.db.repos import SourcesRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.jobs.handlers import KNOWN_KINDS
from xcopilot.jobs.worker import HandlerSpec, Worker
from xcopilot.retrieval.context_pack import TaskSpec, build_pack
from xcopilot.x.errors import XUnavailable
from xcopilot.x.models import XMetrics, XPost

SF = async_sessionmaker[AsyncSession]
POST_ID = "1900000000000000001"
URL = f"https://x.com/vitalik/status/{POST_ID}"
PARENT_TEXT = "Rollups will die soon, everything moves to one fast L1 chain."
CFG = HarvestConfig.load()
WORDS = (
    "fees",
    "blobs",
    "sequencers",
    "bridges",
    "validators",
    "liquidity",
    "wallets",
    "proofs",
    "latency",
    "settlement",
)


class FakeReplies:
    """Ответы и цитаты под постом (контракт PublicPostReader.fetch_replies/fetch_quotes)."""

    def __init__(self) -> None:
        self.replies: list[XPost] = []
        self.quotes: list[XPost] = []
        self.error: Exception | None = None
        self.calls: list[tuple[str, str, int]] = []

    async def fetch_replies(self, post_id: str, limit: int = 30) -> list[XPost]:
        self.calls.append(("replies", post_id, limit))
        if self.error is not None:
            raise self.error
        return list(self.replies)[:limit]

    async def fetch_quotes(self, post_id: str, limit: int = 30) -> list[XPost]:
        self.calls.append(("quotes", post_id, limit))
        if self.error is not None:
            raise self.error
        return list(self.quotes)[:limit]


class OwnerSink:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def notify_owner(self, account_id: UUID, text: str) -> None:
        self.sent.append(text)


def text_for(i: int) -> str:
    w = WORDS[i % len(WORDS)]
    return f"Reply {i}: honestly the real story here is {w}, and nobody prices that in yet"


def reply(i: int, likes: int, *, at: datetime, minutes: int = 30, text: str | None = None) -> XPost:
    return XPost(
        id=str(2_000_000 + i),
        text=text or text_for(i),
        author_handle=f"user{i}",
        author_id=str(900 + i),
        created_at=at + timedelta(minutes=minutes),
        source="fxtwitter",
        metrics=XMetrics(likes=likes, replies=1, reposts=0, impressions=likes * 40 or None),
        author_followers=1000,
        lang="en",
        in_reply_to_id=POST_ID,
    )


def parent(posted_at: datetime, likes: int = 1000) -> XPost:
    return XPost(
        id=POST_ID,
        text=PARENT_TEXT,
        author_handle="vitalik",
        author_id="555",
        created_at=posted_at,
        source="fxtwitter",
        metrics=XMetrics(likes=likes, replies=300),
        author_followers=5_000_000,
        lang="en",
    )


def harvester(stand: Stand, reader: FakeReplies, config: HarvestConfig = CFG) -> Harvester:
    return Harvester(lambda: UnitOfWork(stand.sf), stand.clock, reader, config=config)


async def harvest_rows(stand: Stand) -> list[Any]:
    return await stand.rows(
        "SELECT id, kind, stance, origin, parent_source_id, external_id, meta FROM sources"
        " WHERE account_id = :a AND origin = 'reply_harvest' AND kind IN ('reply', 'quote')"
        " ORDER BY (meta->>'rank')::int",
        a=stand.account_id,
    )


async def jobs_of(stand: Stand, kind: str) -> list[Any]:
    return await stand.rows(
        "SELECT id, run_at, status, payload FROM jobs WHERE account_id = :a AND kind = :k",
        a=stand.account_id,
        k=kind,
    )


async def old_post(stand: Stand, reader: FakeReplies, n: int = 30) -> dict[str, Any]:
    at = stand.clock.now() - timedelta(days=3)
    reader.replies = [reply(i, likes=10 + i, at=at) for i in range(n)]
    return harvest_payload(parent(at), URL)


# --- хранение и отделение голоса ---------------------------------------------------------


async def test_harvest_stores_inspiration_with_parent(
    session_factory: SF, clock: FakeClock
) -> None:
    stand = await make_stand(session_factory, clock)
    reader = FakeReplies()
    payload = await old_post(stand, reader)
    result = await harvester(stand, reader).run(stand.account_id, payload)
    assert result.stored == 20
    [par] = await stand.rows(
        "SELECT id, kind, stance, origin FROM sources WHERE account_id = :a AND external_id = :e",
        a=stand.account_id,
        e=POST_ID,
    )
    assert (par.kind, par.stance, par.origin) == ("post", "reference", "reply_harvest")
    rows = await harvest_rows(stand)
    assert len(rows) == 20
    for r in rows:
        assert (r.kind, r.stance, r.origin) == ("reply", "inspiration", "reply_harvest")
        assert r.parent_source_id == par.id
        assert r.meta["harvest"] == POST_ID and r.meta["silent"] is True
    assert (
        await stand.count("sources", f"account_id = '{stand.account_id}' AND stance = 'own'") == 0
    )


async def test_harvest_uses_own_parent_if_exists(session_factory: SF, clock: FakeClock) -> None:
    stand = await make_stand(session_factory, clock)
    async with UnitOfWork(stand.sf) as uow:
        own, _ = await SourcesRepo(uow.session, stand.account_id).add_or_get(
            kind="post",
            stance="own",
            origin="manual_text",
            content_hash="own-parent",
            external_id=POST_ID,
            status="indexed",
        )
    reader = FakeReplies()
    payload = await old_post(stand, reader, n=5)
    await harvester(stand, reader).run(stand.account_id, payload)
    posts = await stand.rows(
        "SELECT id, stance FROM sources WHERE account_id = :a AND kind = 'post'", a=stand.account_id
    )
    assert posts == [(own.id, "own")]
    assert {r.parent_source_id for r in await harvest_rows(stand)} == {own.id}


async def test_harvest_never_in_voice_examples(session_factory: SF, clock: FakeClock) -> None:
    stand = await make_stand(session_factory, clock)
    await stand.accept("My own take: rollups keep winning because fees drop every quarter.")
    await stand.run_jobs()
    reader = FakeReplies()
    payload = await old_post(stand, reader, n=10)
    await harvester(stand, reader).run(stand.account_id, payload)
    await stand.run_jobs()
    harvested = {
        r[0]
        for r in await stand.rows(
            "SELECT c.id FROM chunks c JOIN documents d ON d.id = c.document_id"
            " JOIN sources s ON s.id = d.source_id WHERE s.origin = 'reply_harvest'"
            " AND s.account_id = :a",
            a=stand.account_id,
        )
    }
    assert harvested  # материалы сбора проиндексированы
    task = TaskSpec(format="reply", lang="en", topic="rollups fees and sequencers")
    async with UnitOfWork(stand.sf) as uow:
        pack = await build_pack(
            uow.session, stand.account_id, task, embedder=stand.embedder, now=stand.clock.now()
        )
    assert pack.examples
    assert not {c.chunk_id for c in pack.examples} & harvested


async def test_harvest_does_not_touch_passport(session_factory: SF, clock: FakeClock) -> None:
    stand = await make_stand(session_factory, clock)
    sql = "SELECT id, version, status FROM style_passports WHERE account_id = :a ORDER BY id"
    before = await stand.rows(sql, a=stand.account_id)
    reader = FakeReplies()
    await harvester(stand, reader).run(stand.account_id, await old_post(stand, reader, n=8))
    await stand.run_jobs()
    assert await stand.rows(sql, a=stand.account_id) == before
    assert not await stand.rows(
        "SELECT id FROM jobs WHERE account_id = :a AND kind LIKE 'style%'", a=stand.account_id
    )


async def test_guess_kind_does_not_override_harvest(session_factory: SF, clock: FakeClock) -> None:
    stand = await make_stand(session_factory, clock)
    stand.llm.material = "own_post"  # тегер «угадывает» свой пост
    reader = FakeReplies()
    await harvester(stand, reader).run(stand.account_id, await old_post(stand, reader, n=6))
    async with stand.sf() as s:  # даже с просьбой угадать вид — сбор остаётся вдохновением
        await s.execute(
            text(
                "UPDATE sources SET meta = meta || '{\"ask_kind\": true}'::jsonb"
                " WHERE account_id = :a AND origin = 'reply_harvest'"
            ),
            {"a": stand.account_id},
        )
        await s.commit()
    await stand.run_jobs()
    rows = await harvest_rows(stand)
    assert rows and all((r.kind, r.stance) == ("reply", "inspiration") for r in rows)
    indexed = await stand.rows(
        "SELECT status FROM sources WHERE account_id = :a AND origin = 'reply_harvest'"
        " AND kind = 'reply'",
        a=stand.account_id,
    )
    assert {r.status for r in indexed} == {"indexed"}


# --- дубли, лимиты, тишина ---------------------------------------------------------------


async def test_same_post_enqueued_once(session_factory: SF, clock: FakeClock) -> None:
    stand = await make_stand(session_factory, clock)
    payload = harvest_payload(parent(clock.now() - timedelta(hours=2)), URL)
    async with UnitOfWork(stand.sf) as uow:
        first = await enqueue_harvest(uow.session, stand.account_id, payload, now=clock.now())
    async with UnitOfWork(stand.sf) as uow:  # вторая ссылка / «🔁 Ещё варианты»
        second = await enqueue_harvest(uow.session, stand.account_id, payload, now=clock.now())
    assert first is not None and first == second
    assert len(await jobs_of(stand, HARVEST_KIND)) == 1


async def test_existing_reply_gets_signal_not_new_source(
    session_factory: SF, clock: FakeClock
) -> None:
    stand = await make_stand(session_factory, clock)
    reader = FakeReplies()
    payload = await old_post(stand, reader, n=5)
    await harvester(stand, reader).run(stand.account_id, payload)
    n_sources = await stand.count("sources")
    clock.advance(3600)
    reader.replies = [replace(r, metrics=XMetrics(likes=999)) for r in reader.replies]
    result = await harvester(stand, reader).run(stand.account_id, payload)
    assert result.stored == 0 and result.refreshed == 5
    assert await stand.count("sources") == n_sources
    [first] = (await harvest_rows(stand))[:1]
    signals = await stand.rows(
        "SELECT likes FROM material_signals WHERE source_id = :s AND origin = 'public_metrics'"
        " ORDER BY observed_at",
        s=first.id,
    )
    assert len(signals) == 2 and signals[-1].likes == 999


async def test_harvest_runs_now_takes_what_exists(session_factory: SF, clock: FakeClock) -> None:
    stand = await make_stand(session_factory, clock)
    at = clock.now() - timedelta(minutes=10)
    reader = FakeReplies()
    reader.replies = [reply(i, likes=i % 2, at=at, minutes=5) for i in range(4)]
    payload = harvest_payload(parent(at, likes=3), URL)
    async with UnitOfWork(stand.sf) as uow:
        await enqueue_harvest(uow.session, stand.account_id, payload, now=clock.now())
    [job] = await jobs_of(stand, HARVEST_KIND)
    assert job.run_at <= clock.now()
    result = await harvester(stand, reader).run(stand.account_id, payload)
    assert result.stored == 4
    rows = await harvest_rows(stand)
    assert len(rows) == 4 and not any(r.meta["contrast"] for r in rows)

    empty = await make_stand(session_factory, clock, handle="empty")
    nothing = FakeReplies()
    res = await harvester(empty, nothing).run(empty.account_id, payload)
    assert res.stored == 0 and res.status == "done"
    assert await empty.count("sources", f"account_id = '{empty.account_id}'") == 0


async def test_recheck_scheduled_for_fresh_post(session_factory: SF, clock: FakeClock) -> None:
    stand = await make_stand(session_factory, clock)
    at = clock.now() - timedelta(hours=1)
    reader = FakeReplies()
    reader.replies = [reply(i, likes=5, at=at) for i in range(3)]
    await harvester(stand, reader).run(stand.account_id, harvest_payload(parent(at), URL))
    [job] = await jobs_of(stand, RECHECK_KIND)
    assert job.run_at == clock.now() + timedelta(hours=24)

    old = await make_stand(session_factory, clock, handle="old")
    reader2 = FakeReplies()
    await harvester(old, reader2).run(old.account_id, await old_post(old, reader2, n=3))
    assert await jobs_of(old, RECHECK_KIND) == []


async def test_recheck_updates_and_tops_up(session_factory: SF, clock: FakeClock) -> None:
    stand = await make_stand(session_factory, clock)
    at = clock.now() - timedelta(minutes=30)
    reader = FakeReplies()
    reader.replies = [reply(i, likes=1, at=at) for i in range(4)]
    payload = harvest_payload(parent(at, likes=1000), URL)
    h = harvester(stand, reader)
    await h.run(stand.account_id, payload)
    first = await harvest_rows(stand)
    async with UnitOfWork(stand.sf) as uow:  # исход первого сбора (как после разбора)
        for r in first:
            await outcomes.compute_outcome(uow.session, stand.account_id, r.id, now=clock.now())
    clock.advance(24 * 3600)
    reader.replies = [reply(i, likes=300, at=at) for i in range(4)] + [
        reply(10 + i, likes=100 + i, at=at) for i in range(30)
    ]
    result = await h.recheck(stand.account_id, payload)
    assert result.refreshed == 4
    rows = await harvest_rows(stand)
    tops = [r for r in rows if not r.meta["contrast"]]
    assert len(tops) == CFG.per_post
    assert len({r.external_id for r in rows}) == len(rows)  # дублей нет
    for r in first:
        assert await stand.count("material_signals", f"source_id = '{r.id}'") == 2
    revise = await stand.rows(
        "SELECT payload FROM jobs WHERE account_id = :a AND kind = 'brain.detect'",
        a=stand.account_id,
    )
    assert {str(r.id) for r in first} <= {
        p.payload["source_id"] for p in revise if p.payload.get("revise")
    }
    assert stand.notifier.sent == []  # type: ignore[attr-defined]


async def test_daily_cap_reschedules(session_factory: SF, clock: FakeClock) -> None:
    stand = await make_stand(session_factory, clock)
    cfg = replace(CFG, daily_max_materials=5)
    reader = FakeReplies()
    await harvester(stand, reader, cfg).run(stand.account_id, await old_post(stand, reader, n=5))
    before = await stand.count("sources")
    other = harvest_payload(
        replace(parent(clock.now() - timedelta(days=3)), id="1900000000000000009"), URL
    )
    result = await harvester(stand, reader, cfg).run(stand.account_id, other)
    assert result.status == "capped" and result.stored == 0
    assert await stand.count("sources") == before
    moved = [
        j for j in await jobs_of(stand, HARVEST_KIND) if j.payload["post_id"] == other["post_id"]
    ]
    assert len(moved) == 1 and moved[0].run_at == clock.now() + timedelta(days=1)
    assert stand.notifier.sent == []  # type: ignore[attr-defined]


async def test_fxembed_down_no_error_to_owner(session_factory: SF, clock: FakeClock) -> None:
    stand = await make_stand(session_factory, clock)
    reader = FakeReplies()
    reader.error = XUnavailable("fxtwitter вернул 503")
    h = harvester(stand, reader)
    payload = harvest_payload(parent(clock.now() - timedelta(hours=2)), URL)
    async with UnitOfWork(stand.sf) as uow:
        await enqueue_harvest(uow.session, stand.account_id, payload, now=clock.now())

    async def run(job: Any) -> object:
        return await handle_harvest(job, harvester=h)

    sink = OwnerSink()
    worker = Worker(
        stand.queue,
        {HARVEST_KIND: HandlerSpec(run)},
        on_dead=DeadJobReporter(sink, None),
        clock=clock,
    )
    for _ in range(3):
        await worker.run_once()
        clock.advance(3600)
    [job] = await jobs_of(stand, HARVEST_KIND)
    assert job.status == "dead"
    assert sink.sent == [] and stand.notifier.sent == []  # type: ignore[attr-defined]
    assert await harvest_rows(stand) == []


async def test_harvest_silent_no_messages(session_factory: SF, clock: FakeClock) -> None:
    stand = await make_stand(session_factory, clock)
    reader = FakeReplies()
    await harvester(stand, reader).run(stand.account_id, await old_post(stand, reader, n=3))
    await stand.run_jobs()
    cheap = FakeLLM(name="deepseek", clock=clock)
    analyzer = Analyzer(
        lambda: UnitOfWork(stand.sf),
        build_gateway(stand.sf, clock, cheap),
        clock,
        notifier=stand.notifier,
    )
    rows = await stand.rows(
        "SELECT s.id, d.text FROM sources s JOIN documents d ON d.source_id = s.id"
        " WHERE s.account_id = :a AND s.origin = 'reply_harvest' AND s.status = 'indexed'",
        a=stand.account_id,
    )
    assert rows
    for r in rows:
        cheap.push(analysis_json(r.text, format="reply"))
        await analyzer.analyze(stand.account_id, r.id)
    sent = [t for _, t, _ in stand.notifier.sent]  # type: ignore[attr-defined]
    assert all(t.startswith("🧠 Выучила") for t in sent)


async def test_toggle_off_no_harvest(session_factory: SF, clock: FakeClock) -> None:
    stand = await make_stand(session_factory, clock)
    async with stand.sf() as s:
        await s.execute(
            text(
                "UPDATE accounts SET settings = settings || '{\"reply_learning\": false}'"
                " WHERE id = :a"
            ),
            {"a": stand.account_id},
        )
        await s.commit()
    payload = harvest_payload(parent(clock.now()), URL)
    async with UnitOfWork(stand.sf) as uow:
        assert (
            await enqueue_harvest(uow.session, stand.account_id, payload, now=clock.now()) is None
        )
    assert await jobs_of(stand, HARVEST_KIND) == []


def test_harvest_kind_in_fast_lane() -> None:
    assert HARVEST_KIND == "brain.harvest_replies" and RECHECK_KIND == "brain.harvest_recheck"
    assert HARVEST_KIND not in SLOW_KINDS and RECHECK_KIND not in SLOW_KINDS
    assert {HARVEST_KIND, RECHECK_KIND} <= KNOWN_KINDS


# --- пакет писателя ----------------------------------------------------------------------


async def test_reply_task_pack_gets_reply_knowledge(session: AsyncSession) -> None:
    from tests.brain_core_stand import new_account

    acc = await new_account(session)
    reply_item = await kitem(session, acc, scopes={"format": ["reply"], "lang": ["en"]})
    thread_item = await kitem(session, acc, scopes={"format": ["thread"]})
    pack = await build_knowledge_pack(session, acc, pack_task("reply", lang="en"))
    ids = {i.item_id for i in pack.items}
    assert reply_item in ids and thread_item not in ids
