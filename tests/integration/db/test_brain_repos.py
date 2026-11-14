# ruff: noqa: E501
"""Репозитории «Мозга» (ТЗ v3.1): авторы, материалы, разбор, знания, выдача."""

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.db.brain_helpers import account, scalar, user
from xcopilot.db.models import Source
from xcopilot.db.repos import (
    AuthorsRepo,
    KnowledgeApplicationsRepo,
    KnowledgeEmbeddingsRepo,
    KnowledgeEvidenceRepo,
    KnowledgeItemsRepo,
    KnowledgeLinksRepo,
    KnowledgePacksRepo,
    KnowledgeSnapshotsRepo,
    MaterialAnalysesRepo,
    MaterialOutcomesRepo,
    MaterialSignalsRepo,
    SegmentsRepo,
    SourcesRepo,
    TaxonomyRepo,
)
from xcopilot.db.repos.authors import normalize_handle
from xcopilot.db.repos.sources import MATERIALS, material_kind_stance, own_voice
from xcopilot.errors import NotFound

NOW = datetime(2026, 11, 14, 12, tzinfo=UTC)


async def _source(session: AsyncSession, acc, stance: str = "inspiration", **kw):  # type: ignore[no-untyped-def]
    src, _ = await SourcesRepo(session, acc).add_or_get(
        kind=kw.pop("kind", "post"),
        stance=stance,
        origin="manual_text",
        content_hash=kw.pop("content_hash", None) or uuid4().hex,
        **kw,
    )
    return src


async def _item(session: AsyncSession, acc, **kw):  # type: ignore[no-untyped-def]
    base = {
        "kind": "thesis",
        "layer": "craft",
        "element": "hook",
        "statement": "Личная цифра в первой строке даёт больше ответов",
        "guidance": "Начни с личной цифры",
        "risk_class": "B",
        "origin": "materials",
    }
    return await KnowledgeItemsRepo(session, acc).create(**{**base, **kw})


def test_normalize_handle() -> None:
    assert normalize_handle("@Alice ") == "alice"
    assert normalize_handle("BOB_1") == "bob_1"
    for bad in ("", "@", "a b", "al!ce"):
        with pytest.raises(ValueError):
            normalize_handle(bad)


def test_material_kind_stance_mapping() -> None:
    assert material_kind_stance("own_post") == ("post", "own")
    assert material_kind_stance("liked_sample") == ("post", "inspiration")
    assert material_kind_stance("competitor_post") == ("post", "competitor")
    assert material_kind_stance("note") == ("note", "reference")
    assert set(MATERIALS) == {"own_post", "liked_sample", "competitor_post", "note"}
    with pytest.raises(ValueError):
        material_kind_stance("meme")


async def test_authors_repo(session: AsyncSession) -> None:
    acc, other = await account(session), await account(session)
    repo = AuthorsRepo(session, acc)
    alice = await repo.get_or_create("@Alice")
    assert alice.handle == "alice" and alice.trust == 0 and not alice.is_self
    again = await repo.get_or_create("alice", display_name="ignored")
    assert again.id == alice.id
    assert (await repo.by_handle("ALICE")).id == alice.id  # type: ignore[union-attr]
    assert await repo.by_handle("nobody") is None
    assert (await AuthorsRepo(session, other).get_or_create("alice")).id != alice.id
    me = await repo.set_self("Me")
    assert me.is_self and (await repo.self_author()).id == me.id  # type: ignore[union-attr]
    me2 = await repo.set_self("me_new")
    assert me2.is_self and not (await repo.require(me.id)).is_self
    # блокировка отзывает доказательства автора
    item = await _item(session, acc)
    src = await _source(session, acc, author_id=alice.id)
    ev = await KnowledgeEvidenceRepo(session, acc).add(
        item_id=item.id,
        source_id=src.id,
        author_id=alice.id,
        dimension="eff",
        basis="observed_outcome",
        polarity=1,
        w_source=0.7,
        w_scope=1,
        w_magnitude=1,
        observed_at=NOW,
    )
    blocked = await repo.block(alice.id, "спам")
    assert blocked.trust == -1 and blocked.note == "спам"
    revoked = await KnowledgeEvidenceRepo(session, acc).require(ev.id)
    assert revoked.revoked_at is not None and revoked.revoked_reason == "author_blocked"
    with pytest.raises(NotFound):
        await AuthorsRepo(session, other).block(alice.id, "x")


async def test_sources_repo_kind_stance_parent_removed(session: AsyncSession) -> None:
    acc, other = await account(session), await account(session)
    repo = SourcesRepo(session, acc)
    parent = await _source(session, acc, stance="competitor", content_hash="p")
    reply = await _source(session, acc, stance="own", kind="reply", content_hash="r")
    linked = await repo.set_parent(reply.id, parent.id, thread_root_id=parent.id)
    assert linked.parent_source_id == parent.id and linked.thread_root_id == parent.id
    foreign = await _source(session, other, content_hash="f")
    with pytest.raises(NotFound):
        await repo.set_parent(reply.id, foreign.id)
    with pytest.raises(NotFound):
        await repo.set_parent(foreign.id, parent.id)
    # свой голос — stance = own, в одном месте
    own_ids = (
        (await session.execute(select(Source.id).where(Source.account_id == acc, own_voice())))
        .scalars()
        .all()
    )
    assert list(own_ids) == [reply.id]
    # карантин
    q = await repo.quarantine(parent.id, "injection")
    assert q.analysis_status == "quarantined" and q.quarantine_reason == "injection"
    # удаление: доказательства отзываются, веса пересчитываются
    item = await _item(session, acc)
    ev_repo = KnowledgeEvidenceRepo(session, acc)
    ev = await ev_repo.add(
        item_id=item.id,
        source_id=reply.id,
        dimension="eff",
        basis="own_outcome",
        polarity=1,
        w_source=1,
        w_scope=2,
        w_magnitude=1,
        observed_at=NOW,
    )
    await KnowledgeItemsRepo(session, acc).recompute(item.id, now=NOW)
    assert float((await KnowledgeItemsRepo(session, acc).require(item.id)).eff_alpha) == 3.0
    removed = await repo.mark_removed(reply.id, at=NOW + timedelta(minutes=1))
    assert removed.removed_at == NOW + timedelta(minutes=1)
    assert (await ev_repo.require(ev.id)).revoked_reason == "source_removed"
    refreshed = await KnowledgeItemsRepo(session, acc).require(item.id)
    assert float(refreshed.eff_alpha) == 1.0


async def test_sources_batch_kind_and_counts(session: AsyncSession) -> None:
    acc = await account(session)
    repo = SourcesRepo(session, acc)
    src, created = await repo.add_or_get(
        kind="post",
        stance="inspiration",
        origin="link_oembed",
        content_hash="b1",
        meta={"batch": "B"},
        status="indexed",
    )
    assert created
    await repo.add_document(src.id, text="t")
    assert await repo.set_kind_for_batch("B", kind="post", stance="own") == 1
    assert (await repo.require(src.id)).stance == "own"
    assert await repo.count_documents(status="indexed", stance="own") == 1
    assert await repo.count_documents(status="indexed", stance="competitor") == 0
    assert await repo.count_documents(status="indexed", kind="post") == 1


async def test_material_repos(session: AsyncSession) -> None:
    acc = await account(session)
    src = await _source(session, acc, content_hash="m1")
    tax = TaxonomyRepo(session, acc)
    await tax.add(
        facet="hook_type", key="confession", title="Признание", description="Ошибка с суммой"
    )
    await tax.add(
        facet="hook_type", key="old_one", title="Старое", description="d", status="deprecated"
    )
    assert [t.key for t in await tax.active("hook_type")] == ["confession"]

    analyses = MaterialAnalysesRepo(session, acc)
    first = await analyses.add_current(
        src.id, analyzer_version="a@1", taxonomy_version=1, model="m", format="post"
    )
    second = await analyses.add_current(
        src.id,
        analyzer_version="a@2",
        taxonomy_version=1,
        model="m",
        format="post",
        devices=["specific_number"],
    )
    assert (await analyses.current(src.id)).id == second.id  # type: ignore[union-attr]
    assert not (await analyses.require(first.id)).is_current

    signals = MaterialSignalsRepo(session, acc)
    await signals.add(source_id=src.id, origin="owner_label", owner_label="hit")
    api = await signals.add(source_id=src.id, origin="x_api", views=100, observed_at=NOW)
    assert (await signals.best(src.id)).id == api.id  # type: ignore[union-attr]
    assert await signals.best((await _source(session, acc, content_hash="m2")).id) is None

    outcomes = MaterialOutcomesRepo(session, acc)
    o1 = await outcomes.add_current(
        src.id, method_version=1, basis="rates", signal_origin="x_api", reliability=0.8
    )
    o2 = await outcomes.add_current(
        src.id,
        method_version=2,
        basis="rates",
        signal_origin="x_api",
        reliability=0.8,
        tier_engagement="hit",
    )
    assert (await outcomes.current(src.id)).id == o2.id  # type: ignore[union-attr]
    assert not (await outcomes.require(o1.id)).is_current


async def test_knowledge_items_repo_versions_scopes_transitions(session: AsyncSession) -> None:
    acc = await account(session)
    uid = await user(session)
    repo = KnowledgeItemsRepo(session, acc)
    item = await _item(session, acc, scopes={"format": ["post", "thread"], "lang": ["en"]})
    assert item.version == 1 and item.status == "candidate"
    assert await repo.scopes(item.id) == {"format": ["post", "thread"], "lang": ["en"]}
    await repo.set_scopes(item.id, {"goal": ["replies"]})
    assert await repo.scopes(item.id) == {"goal": ["replies"]}
    edited = await repo.edit(
        item.id,
        changed_by="owner",
        user_id=uid,
        reason="owner_edit",
        guidance="Начни с личной цифры и суммы",
    )
    assert edited.version == 2 and edited.guidance == "Начни с личной цифры и суммы"
    versions = await repo.versions(item.id)
    assert [v.version for v in versions] == [1, 2]
    assert versions[0].guidance == "Начни с личной цифры" and versions[1].changed_by == "owner"

    t1 = await repo.transition(
        item.id,
        "probation",
        actor="system",
        rule="auto_probation",
        payload={"eff_conf": 0.57},
        at=NOW,
    )
    probation = await repo.require(item.id)
    assert probation.status == "probation" and probation.probation_started_at == NOW
    assert t1.from_status == "candidate" and t1.to_status == "probation"
    assert [i.id for i in await repo.by_status("probation")] == [item.id]
    assert [i.id for i in await repo.usable()] == [item.id]
    reverted = await repo.revert_transition(t1.id, user_id=uid, at=NOW + timedelta(hours=1))
    assert reverted.reverted_at is not None and reverted.reverted_by == uid
    assert (await repo.require(item.id)).status == "candidate"
    assert await repo.usable() == []

    target = await _item(session, acc)
    await repo.transition(
        item.id, "merged", actor="system", rule="merge", merged_into=target.id, at=NOW
    )
    merged = await repo.require(item.id)
    assert merged.status == "merged" and merged.merged_into == target.id


async def test_evidence_links_snapshots(session: AsyncSession) -> None:
    acc = await account(session)
    items = KnowledgeItemsRepo(session, acc)
    a, b = await _item(session, acc), await _item(session, acc)
    ev_repo = KnowledgeEvidenceRepo(session, acc)
    src = await _source(session, acc, content_hash="e1")
    ev = await ev_repo.add(
        item_id=a.id,
        source_id=src.id,
        dimension="eff",
        basis="observed_outcome",
        polarity=1,
        w_source=0.7,
        w_scope=1,
        w_magnitude=1,
        observed_at=NOW,
    )
    assert [e.id for e in await ev_repo.for_item(a.id)] == [ev.id]
    await ev_repo.revoke(ev.id, at=NOW, reason="owner")
    assert await ev_repo.for_item(a.id) == []
    assert len(await ev_repo.for_item(a.id, include_revoked=True)) == 1
    assert await items.recompute(now=NOW) == 2

    links = KnowledgeLinksRepo(session, acc)
    link = await links.add(
        from_item_id=a.id,
        to_item_id=b.id,
        relation="contradicts",
        confidence=0.9,
        decided_by="judge",
    )
    assert [x.id for x in await links.open_conflicts()] == [link.id]
    resolved = await links.resolve(link.id, resolution="kept_from", at=NOW)
    assert resolved.status == "resolved" and resolved.resolved_at == NOW
    assert await links.open_conflicts() == []

    snaps = KnowledgeSnapshotsRepo(session, acc)
    assert await snaps.take(date(2026, 11, 13)) == 2
    assert await snaps.take(date(2026, 11, 13)) == 2  # повтор — обновление, не дубль
    await items.transition(a.id, "active", actor="owner", rule="owner_confirm", at=NOW)
    await snaps.take(date(2026, 11, 14))
    on_13 = {s.item_id: s.status for s in await snaps.state_on(date(2026, 11, 13))}
    on_20 = {s.item_id: s.status for s in await snaps.state_on(date(2026, 11, 20))}
    assert on_13 == {a.id: "candidate", b.id: "candidate"}
    assert on_20[a.id] == "active"
    assert await snaps.state_on(date(2026, 1, 1)) == []

    model_id = await scalar(session, "SELECT id FROM embedding_models WHERE active")
    emb = KnowledgeEmbeddingsRepo(session, acc)
    await emb.upsert(a.id, model_id, version=1, vec=[0.1] * 384)
    await emb.upsert(a.id, model_id, version=2, vec=[0.2] * 384)
    assert (
        await scalar(session, "SELECT version FROM knowledge_embeddings WHERE item_id = :i", i=a.id)
        == 2
    )


async def test_packs_applications_segments(session: AsyncSession) -> None:
    acc = await account(session)
    item = await _item(session, acc)
    pack = await KnowledgePacksRepo(session, acc).add(
        consumer="writer",
        task={"format": "post"},
        schema_version=1,
        taxonomy_version=1,
        items=[{"item_id": str(item.id), "slot": "core"}],
        token_count=120,
    )
    run_id = await scalar(
        session,
        "INSERT INTO generation_runs (account_id, kind) VALUES (:a, 'post') RETURNING id",
        a=acc,
    )
    draft_id = await scalar(
        session,
        "INSERT INTO drafts (account_id, run_id, format, text) VALUES (:a, :r, 'post', 't') RETURNING id",
        a=acc,
        r=run_id,
    )
    apps = KnowledgeApplicationsRepo(session, acc)
    app = await apps.add(pack_id=pack.id, item_id=item.id, draft_id=draft_id, slot="core")
    assert app.applied is None
    checked = await apps.mark_checked(app.id, applied=True, score=0.8, checked_by="feature", at=NOW)
    assert checked.applied and float(checked.applied_score) == 0.8 and checked.checked_at == NOW

    segs = SegmentsRepo(session, acc)
    await segs.add(key="hooks", title="Хуки", filter={"element": ["hook"]})
    assert (await segs.by_key("hooks")).title == "Хуки"  # type: ignore[union-attr]
    assert await segs.by_key("none") is None
    assert await segs.items("hooks") == [item.id]
    await session.execute(text("SELECT 1"))
