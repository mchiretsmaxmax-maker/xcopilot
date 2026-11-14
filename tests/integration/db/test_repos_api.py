"""Методы репозиториев по агрегатам (контракт для этапов 5–14)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from xcopilot.db.models.content import DraftStatus
from xcopilot.db.repos import (
    AccountsRepo,
    BudgetsRepo,
    ChunksRepo,
    DraftsRepo,
    FactsRepo,
    KnowledgeItemsRepo,
    PassportsRepo,
    PublicationsRepo,
    ReviewsRepo,
    RunsRepo,
    SecretsRepo,
    SourcesRepo,
    TopicsRepo,
    UsersRepo,
)
from xcopilot.errors import InvalidTransition, NotFound
from xcopilot.security.crypto import SecretBox

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


async def _account(session: AsyncSession) -> uuid.UUID:
    acc = await AccountsRepo(session).create(handle=f"t_{uuid.uuid4().hex[:8]}")
    return acc.id


async def test_accounts_and_users(session: AsyncSession) -> None:
    accounts, users = AccountsRepo(session), UsersRepo(session)
    glob = await accounts.get_global()
    assert glob.kind == "global"
    acc = await accounts.create(handle="max", tz="UTC", lang="en")
    assert acc.kind == "x" and acc.status == "active" and acc.x_read_mode == "owned"
    assert acc.settings == {}
    assert await accounts.get(acc.id) is not None
    assert await accounts.get(uuid.uuid4()) is None
    by_handle = await accounts.get_by_handle("max")
    assert by_handle is not None and by_handle.id == acc.id
    user, created = await users.get_or_create(42, name="Макс")
    again, created2 = await users.get_or_create(42, name="другое")
    assert created and not created2 and again.id == user.id and again.name == "Макс"
    found = await users.get_by_tg_id(42)
    assert found is not None and found.id == user.id
    assert await users.get_by_tg_id(43) is None
    got = await users.get(user.id)
    assert got is not None and got.tg_user_id == 42
    member = await accounts.add_member(acc.id, user.id, role="owner")
    assert member.role == "owner"
    assert [a.id for a in await accounts.list_for_user(user.id)] == [acc.id]


async def test_secrets_repo_stores_only_ciphertext(session: AsyncSession) -> None:
    acc = await _account(session)
    box = SecretBox({1: Fernet.generate_key().decode()})
    repo = SecretsRepo(session, acc)
    assert await repo.get_plain("x_oauth", box) is None
    row = await repo.put("x_oauth", "tok-1", box)
    assert row.key_version == 1 and b"tok-1" not in row.ciphertext
    await repo.put("x_oauth", "tok-2", box)
    assert await repo.get_plain("x_oauth", box) == "tok-2"
    raw = await session.execute(
        text(
            "SELECT count(*), bool_and(position('tok' in encode(ciphertext, 'escape')) = 0)"
            " FROM secrets WHERE account_id = :a"
        ),
        {"a": acc},
    )
    assert tuple(raw.one()) == (1, True)


async def test_sources_documents_chunks_embeddings(session: AsyncSession) -> None:
    acc = await _account(session)
    sources = SourcesRepo(session, acc)
    src, _ = await sources.add_or_get(kind="post", stance="own", origin="x_api", content_hash="c1")
    assert src.status == "new" and src.meta == {}
    doc = await sources.add_document(src.id, text="Привет, мир", token_count=3)
    assert doc.source_id == src.id and doc.account_id == acc
    with pytest.raises(NotFound):
        await SourcesRepo(session, await _account(session)).add_document(src.id, text="x")
    updated = await sources.set_status(src.id, "indexed")
    assert updated.status == "indexed"
    chunks = ChunksRepo(session, acc)
    c2 = await chunks.add(document_id=doc.id, idx=1, text="мир", tags=["b"])
    c1 = await chunks.add(document_id=doc.id, idx=0, text="Привет", lang="ru")
    assert [c.id for c in await chunks.list_by_document(doc.id)] == [c1.id, c2.id]
    model_id = (
        await session.execute(text("SELECT id FROM embedding_models WHERE active"))
    ).scalar_one()
    emb = await chunks.add_embedding(c1.id, model_id=model_id, vec=[0.1] * 384)
    assert emb.chunk_id == c1.id and emb.account_id == acc
    with pytest.raises(NotFound):
        await ChunksRepo(session, await _account(session)).add_embedding(
            c1.id, model_id=model_id, vec=[0.1] * 384
        )


async def test_passports_activate(session: AsyncSession) -> None:
    acc = await _account(session)
    repo = PassportsRepo(session, acc)
    assert await repo.get_active() is None
    assert await repo.next_version() == 1
    p1 = await repo.add(version=1, body={"tone": "dry"}, created_by="llm")
    assert p1.status == "draft"
    await repo.activate(p1.id)
    p2 = await repo.add(version=await repo.next_version(), body={}, created_by="user")
    assert p2.version == 2
    active = await repo.activate(p2.id)
    assert active.id == p2.id and active.status == "active"
    old = await repo.get(p1.id)
    assert old is not None and old.status == "archived"
    current = await repo.get_active()
    assert current is not None and current.id == p2.id


async def test_lessons_facts_topics(session: AsyncSession) -> None:
    acc = await _account(session)
    user, _ = await UsersRepo(session).get_or_create(7)
    # ТЗ v3.1: lessons удалена — урок стал знанием, решение владельца — переходом статуса
    lessons = KnowledgeItemsRepo(session, acc)
    lesson = await lessons.create(
        kind="anti_pattern",
        layer="humanness",
        element="tone",
        statement="Эмодзи в конце поста звучат как AI",
        guidance="без эмодзи",
        risk_class="C",
        origin="edits",
    )
    assert lesson.status == "candidate"
    decided = await lessons.transition(
        lesson.id, "active", actor="owner", rule="owner_confirm", user_id=user.id, at=NOW
    )
    assert decided.to_status == "active" and decided.ts == NOW
    assert [x.id for x in await lessons.by_status("active")] == [lesson.id]
    assert await lessons.by_status("candidate") == []

    facts = FactsRepo(session, acc)
    keep = await facts.add(statement="a", source_url="https://a", observed_at=NOW)
    await facts.add(
        statement="b", source_url="https://b", observed_at=NOW, expires_at=NOW - timedelta(days=1)
    )
    later = await facts.add(
        statement="c", source_url="https://c", observed_at=NOW, expires_at=NOW + timedelta(days=1)
    )
    assert {f.id for f in await facts.list_valid(NOW)} == {keep.id, later.id}

    topics = TopicsRepo(session, acc)
    topic = await topics.add(title="Тема", origin="manual")
    assert topic.status == "new"
    await topics.set_status(topic.id, "planned")
    assert [t.id for t in await topics.list_by_status("planned")] == [topic.id]


async def test_runs_drafts_reviews_publications(session: AsyncSession) -> None:
    acc = await _account(session)
    user, _ = await UsersRepo(session).get_or_create(8)
    topic = await TopicsRepo(session, acc).add(title="t", origin="manual")
    runs = RunsRepo(session, acc)
    run = await runs.add(topic_id=topic.id, kind="post", context_pack={"chunks": []})
    assert run.status == "running"
    finished = await runs.finish(run.id, status="done", finished_at=NOW)
    assert finished.status == "done" and finished.finished_at == NOW

    drafts = DraftsRepo(session, acc)
    d1 = await drafts.add(run_id=run.id, text="v1", format="post")
    d2 = await drafts.add(run_id=run.id, variant_no=2, text="v2", format="post")
    assert [d.id for d in await drafts.list_by_run(run.id)] == [d1.id, d2.id]
    for status in (DraftStatus.LINTED, DraftStatus.JUDGED, DraftStatus.SHOWN):
        d1 = await drafts.set_status(d1.id, status)
    assert d1.status == "shown"
    with pytest.raises(InvalidTransition):
        await drafts.set_status(d2.id, DraftStatus.PUBLISHED)
    with pytest.raises(NotFound):
        await DraftsRepo(session, await _account(session)).set_status(d1.id, DraftStatus.EDITED)

    reviews = ReviewsRepo(session, acc)
    review = await reviews.add(
        draft_id=d1.id, user_id=user.id, action="edit", text_before="v1", text_after="v1!"
    )
    assert [r.id for r in await reviews.list_by_draft(d1.id)] == [review.id]
    await drafts.set_status(d1.id, DraftStatus.EDITED)

    pubs = PublicationsRepo(session, acc)
    pub = await pubs.add(
        draft_id=d1.id,
        x_post_id="1900000000000000001",
        url="https://x.com/max/status/1900000000000000001",
        published_at=NOW,
        matched_by="user_link",
        final_text="v1!",
    )
    by_post = await pubs.get_by_x_post_id("1900000000000000001")
    assert by_post is not None and by_post.id == pub.id
    by_draft = await pubs.get_by_draft(d1.id)
    assert by_draft is not None and by_draft.id == pub.id
    assert await PublicationsRepo(session, await _account(session)).get_by_draft(d1.id) is None


async def test_budgets(session: AsyncSession) -> None:
    acc = await _account(session)
    repo = BudgetsRepo(session, acc)
    assert await repo.get_limit("llm", "day") is None
    b = await repo.upsert("llm", "day", limit_micro_usd=2_500_000)
    assert b.hard is True and b.limit_micro_usd == 2_500_000
    b2 = await repo.upsert("llm", "day", limit_micro_usd=3_000_000, hard=False)
    assert b2.id == b.id and b2.limit_micro_usd == 3_000_000 and b2.hard is False
    got = await repo.get_limit("llm", "day")
    assert got is not None and got.limit_micro_usd == 3_000_000
    assert len(await repo.list()) == 1
