"""Репозитории: обязательный account_id, изоляция тенантов, дедуп, ревизии."""

import uuid
from typing import Any, cast

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

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
    TenantRepo,
    TopicsRepo,
)
from xcopilot.errors import NotFound

TENANT_REPOS: list[type[TenantRepo[Any]]] = [
    SecretsRepo,
    SourcesRepo,
    ChunksRepo,
    PassportsRepo,
    KnowledgeItemsRepo,
    FactsRepo,
    TopicsRepo,
    RunsRepo,
    DraftsRepo,
    ReviewsRepo,
    PublicationsRepo,
    BudgetsRepo,
]


async def two_accounts(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    accounts = AccountsRepo(session)
    a = await accounts.create(handle=f"a_{uuid.uuid4().hex[:8]}")
    b = await accounts.create(handle=f"b_{uuid.uuid4().hex[:8]}")
    return a.id, b.id


@pytest.mark.parametrize("repo_cls", TENANT_REPOS, ids=lambda c: c.__name__)
def test_repo_requires_account_id(repo_cls: type[TenantRepo[Any]]) -> None:
    session = cast(AsyncSession, object())
    with pytest.raises(TypeError):
        repo_cls(session)  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        repo_cls(session, None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        repo_cls(session, "00000000-0000-0000-0000-000000000000")  # type: ignore[arg-type]


async def test_repo_cannot_read_other_account(session: AsyncSession) -> None:
    a, b = await two_accounts(session)
    src_b, _ = await SourcesRepo(session, b).add_or_get(
        kind="note", stance="reference", origin="manual_text", content_hash="h-b"
    )
    assert await SourcesRepo(session, a).get(src_b.id) is None
    assert await SourcesRepo(session, a).list() == []
    found = await SourcesRepo(session, b).get(src_b.id)
    assert found is not None and found.id == src_b.id


async def test_repo_cannot_update_other_account(session: AsyncSession) -> None:
    a, b = await two_accounts(session)
    # ТЗ v3.1: lessons удалена, вместо урока — знание (knowledge_items)
    lesson = await KnowledgeItemsRepo(session, b).create(
        kind="anti_pattern",
        layer="craft",
        element="general",
        statement="Длинные абзацы без воздуха читают хуже",
        guidance="пиши коротко",
        risk_class="C",
        origin="edits",
    )
    with pytest.raises(NotFound):
        await KnowledgeItemsRepo(session, a).update(lesson.id, guidance="взлом взлом")
    again = await KnowledgeItemsRepo(session, b).get(lesson.id)
    assert again is not None and again.guidance == "пиши коротко"
    updated = await KnowledgeItemsRepo(session, b).update(lesson.id, guidance="пиши ещё короче")
    assert updated.guidance == "пиши ещё короче"
    with pytest.raises(ValueError):
        await KnowledgeItemsRepo(session, b).update(lesson.id, account_id=a)


async def test_source_dedupe_by_content_hash(session: AsyncSession) -> None:
    a, b = await two_accounts(session)
    repo = SourcesRepo(session, a)
    s1, created1 = await repo.add_or_get(
        kind="post",
        stance="own",
        origin="manual_text",
        content_hash="abc",
        url="https://x.com/a/status/1",
    )
    s2, created2 = await repo.add_or_get(
        kind="post", stance="own", origin="manual_text", content_hash="abc"
    )
    assert created1 and not created2
    assert s1.id == s2.id and s2.url == "https://x.com/a/status/1"
    assert len(await repo.list()) == 1
    s3, created3 = await SourcesRepo(session, b).add_or_get(
        kind="post", stance="own", origin="manual_text", content_hash="abc"
    )
    assert created3 and s3.id != s1.id


async def test_only_one_active_passport_per_account(session: AsyncSession) -> None:
    a, b = await two_accounts(session)
    await PassportsRepo(session, a).add(version=1, status="active", body={}, created_by="llm")
    await PassportsRepo(session, b).add(version=1, status="active", body={}, created_by="llm")
    await PassportsRepo(session, a).add(version=2, status="draft", body={}, created_by="llm")
    with pytest.raises(IntegrityError):
        await PassportsRepo(session, a).add(version=3, status="active", body={}, created_by="user")


async def test_draft_revision_links_to_parent(session: AsyncSession) -> None:
    a, b = await two_accounts(session)
    run = await RunsRepo(session, a).add(kind="post")
    drafts = DraftsRepo(session, a)
    d1 = await drafts.add(run_id=run.id, text="первый вариант", format="post")
    assert d1.revision == 1 and d1.parent_draft_id is None and d1.status == "generated"
    d2 = await drafts.add_revision(d1.id, text="второй вариант")
    assert d2.parent_draft_id == d1.id
    assert d2.revision == 2
    assert d2.run_id == run.id and d2.variant_no == d1.variant_no
    assert d2.status == "generated" and d2.text == "второй вариант"
    d3 = await drafts.add_revision(d2.id, text="третий")
    assert d3.parent_draft_id == d2.id and d3.revision == 3
    with pytest.raises(NotFound):
        await DraftsRepo(session, b).add_revision(d1.id, text="чужой")
