# ruff: noqa: E501
"""ТЗ v3.1 «Мозг», дельта этапа 2 и приложение A: ограничения новых и изменённых таблиц."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.db.brain_helpers import (
    account,
    author,
    draft,
    evidence,
    insert,
    item,
    pack,
    scalar,
    source,
    user,
)

NOW = datetime(2026, 11, 14, 12, tzinfo=UTC)


async def rejected(session: AsyncSession, action: Callable[[], Awaitable[Any]]) -> str:
    """Действие нарушает ограничение БД; вернуть текст ошибки (сессия остаётся рабочей)."""
    with pytest.raises(DBAPIError) as exc:
        async with session.begin_nested():
            await action()
    return str(exc.value.orig)


async def ok(session: AsyncSession, action: Callable[[], Awaitable[Any]]) -> Any:
    async with session.begin_nested():
        return await action()


# --- дельта этапа 2 -------------------------------------------------------------------


async def test_sources_kind_stance_origin_checks(session: AsyncSession) -> None:
    acc = await account(session)
    for kind in ("post", "thread", "reply", "quote", "repost", "article", "note", "file", "chat"):
        await ok(session, lambda k=kind: source(session, acc, kind=k))
    for stance in ("own", "inspiration", "competitor", "anti_example", "reference"):
        await ok(session, lambda s=stance: source(session, acc, stance=s))
    for origin in (
        "manual_text",
        "link_oembed",
        "x_api",
        "screenshot",
        "tg_forward",
        "file",
        "url_fetch",
        "history_backfill",
        "radar",
    ):
        await ok(session, lambda o=origin: source(session, acc, origin=o))
    await rejected(session, lambda: source(session, acc, kind="own_post"))
    await rejected(session, lambda: source(session, acc, stance="liked"))
    await rejected(session, lambda: source(session, acc, origin="manual"))
    await rejected(session, lambda: source(session, acc, analysis_status="done"))
    # stance обязателен
    await rejected(
        session,
        lambda: insert(
            session, "sources", account_id=acc, kind="post", origin="x_api", content_hash="h"
        ),
    )


async def test_source_parent_must_belong_to_same_account(session: AsyncSession) -> None:
    a, b = await account(session), await account(session)
    parent_a = await source(session, a)
    parent_b = await source(session, b)
    await ok(session, lambda: source(session, a, kind="reply", parent_source_id=parent_a))
    await ok(session, lambda: source(session, a, kind="thread", thread_root_id=parent_a))
    err = await rejected(
        session, lambda: source(session, a, kind="reply", parent_source_id=parent_b)
    )
    assert "fk_sources_parent" in err
    await rejected(session, lambda: source(session, a, kind="thread", thread_root_id=parent_b))
    author_b = await author(session, b, "bob")
    await rejected(session, lambda: source(session, a, author_id=author_b))


async def test_quarantine_requires_reason(session: AsyncSession) -> None:
    acc = await account(session)
    await rejected(session, lambda: source(session, acc, analysis_status="quarantined"))
    await rejected(
        session, lambda: source(session, acc, analysis_status="pending", quarantine_reason="x")
    )
    sid = await ok(
        session,
        lambda: source(session, acc, analysis_status="quarantined", quarantine_reason="injection"),
    )
    assert await scalar(session, "SELECT analysis_status FROM sources WHERE id = :i", i=sid) == (
        "quarantined"
    )
    fresh = await source(session, acc)
    assert await scalar(session, "SELECT analysis_status FROM sources WHERE id = :i", i=fresh) == (
        "pending"
    )


async def test_author_handle_normalized_and_unique(session: AsyncSession) -> None:
    acc, other = await account(session), await account(session)
    await rejected(session, lambda: author(session, acc, "Alice"))
    await rejected(session, lambda: author(session, acc, "@alice"))
    await ok(session, lambda: author(session, acc, "alice"))
    await rejected(session, lambda: author(session, acc, "alice"))
    await ok(session, lambda: author(session, other, "alice"))  # другой аккаунт — можно
    await rejected(session, lambda: author(session, acc, "carol", trust=2))
    await rejected(session, lambda: author(session, acc, "dave", followers=-1))
    await rejected(session, lambda: author(session, acc, "erin", followers_origin="guess"))


async def test_single_self_author_per_account(session: AsyncSession) -> None:
    acc, other = await account(session), await account(session)
    await ok(session, lambda: author(session, acc, "me", is_self=True))
    await ok(session, lambda: author(session, acc, "friend", is_self=False))
    await rejected(session, lambda: author(session, acc, "me2", is_self=True))
    await ok(session, lambda: author(session, other, "me", is_self=True))


async def test_lessons_table_absent(session: AsyncSession) -> None:
    assert await scalar(session, "SELECT to_regclass('public.lessons')") is None
    import xcopilot.db.models as models
    import xcopilot.db.repos as repos

    assert not hasattr(models, "Lesson")
    assert not hasattr(repos, "LessonsRepo")


# --- приложение A: новые таблицы ------------------------------------------------------


async def test_new_brain_tables_exist(session: AsyncSession) -> None:
    tables = {
        "authors",
        "material_signals",
        "material_outcomes",
        "taxonomy_terms",
        "material_analyses",
        "knowledge_items",
        "knowledge_item_versions",
        "knowledge_item_scopes",
        "knowledge_evidence",
        "knowledge_links",
        "knowledge_transitions",
        "knowledge_embeddings",
        "knowledge_snapshots",
        "knowledge_packs",
        "knowledge_applications",
        "segments",
    }
    for t in tables:
        assert await scalar(session, "SELECT to_regclass(:t)", t=f"public.{t}") is not None, t
    kind = await scalar(
        session, "SELECT relkind FROM pg_class WHERE relname = 'knowledge_snapshots'"
    )
    assert kind == "p"  # партиционирована
    assert await scalar(session, "SELECT to_regclass('public.knowledge_snapshots_default')")


async def test_material_signals_checks(session: AsyncSession) -> None:
    a, b = await account(session), await account(session)
    sid = await source(session, a)
    foreign = await source(session, b)
    await ok(
        session,
        lambda: insert(
            session, "material_signals", account_id=a, source_id=sid, origin="x_api", views=100
        ),
    )
    await ok(
        session,
        lambda: insert(
            session,
            "material_signals",
            account_id=a,
            source_id=sid,
            origin="owner_label",
            owner_label="hit",
        ),
    )
    await rejected(
        session,
        lambda: insert(
            session, "material_signals", account_id=a, source_id=sid, origin="owner_label"
        ),
    )
    await rejected(
        session,
        lambda: insert(
            session,
            "material_signals",
            account_id=a,
            source_id=sid,
            origin="x_api",
            owner_label="hit",
        ),
    )
    await rejected(
        session,
        lambda: insert(session, "material_signals", account_id=a, source_id=sid, origin="tiktok"),
    )
    await rejected(
        session,
        lambda: insert(
            session, "material_signals", account_id=a, source_id=sid, origin="x_api", likes=-1
        ),
    )
    await rejected(
        session,
        lambda: insert(
            session,
            "material_signals",
            account_id=a,
            source_id=sid,
            origin="x_api",
            read_confidence=1.5,
        ),
    )
    await rejected(
        session,
        lambda: insert(
            session, "material_signals", account_id=a, source_id=foreign, origin="x_api"
        ),
    )


async def test_material_outcomes_single_current(session: AsyncSession) -> None:
    acc = await account(session)
    sid = await source(session, acc)

    def outcome(**kw: Any) -> Awaitable[UUID]:
        base: dict[str, Any] = {
            "account_id": acc,
            "source_id": sid,
            "method_version": 1,
            "basis": "rates",
            "signal_origin": "x_api",
            "reliability": 0.8,
        }
        return insert(session, "material_outcomes", **{**base, **kw})

    await ok(session, lambda: outcome(tier_engagement="hit"))
    await rejected(session, lambda: outcome())  # второй текущий исход
    await ok(session, lambda: outcome(is_current=False))
    await rejected(session, lambda: outcome(is_current=False, tier_reach="great"))
    await rejected(session, lambda: outcome(is_current=False, basis="vibes"))
    await rejected(session, lambda: outcome(is_current=False, pct_reach=1.2))
    await rejected(session, lambda: outcome(is_current=False, reliability=2))


async def test_taxonomy_terms_checks(session: AsyncSession) -> None:
    acc = await account(session)

    def term(**kw: Any) -> Awaitable[UUID]:
        base: dict[str, Any] = {
            "account_id": acc,
            "facet": "hook_type",
            "key": "confession",
            "title": "Признание",
            "description": "Начало с признания ошибки",
        }
        return insert(session, "taxonomy_terms", **{**base, **kw})

    await ok(session, lambda: term())
    await rejected(session, lambda: term())  # дубль (account, facet, key)
    await ok(session, lambda: term(facet="tone"))
    await rejected(session, lambda: term(key="Bad-Key"))
    await rejected(session, lambda: term(key="x"))
    await rejected(session, lambda: term(key="ok_key", facet="colour"))
    await rejected(session, lambda: term(key="ok_key2", status="maybe"))
    await rejected(session, lambda: term(key="ok_key3", created_by="robot"))


async def test_material_analyses_single_current_and_format(session: AsyncSession) -> None:
    acc = await account(session)
    sid = await source(session, acc)

    def analysis(**kw: Any) -> Awaitable[UUID]:
        base: dict[str, Any] = {
            "account_id": acc,
            "source_id": sid,
            "analyzer_version": "brain.analyze@1",
            "taxonomy_version": 1,
            "model": "m",
            "format": "post",
        }
        return insert(session, "material_analyses", **{**base, **kw})

    await ok(session, lambda: analysis())
    await rejected(session, lambda: analysis())
    await ok(session, lambda: analysis(is_current=False))
    await rejected(session, lambda: analysis(is_current=False, format="note"))


async def test_knowledge_items_checks(session: AsyncSession) -> None:
    a, b = await account(session), await account(session)
    await ok(session, lambda: item(session, a))
    await rejected(session, lambda: item(session, a, layer="voice", risk_class="B"))
    await ok(session, lambda: item(session, a, layer="voice", kind="lexicon", element="lexicon"))
    await rejected(session, lambda: item(session, a, kind="lexicon", element="lexicon"))
    await rejected(session, lambda: item(session, a, status="merged"))  # без merged_into
    await rejected(session, lambda: item(session, a, status="zombie"))
    await rejected(session, lambda: item(session, a, owner_state="love"))
    await rejected(session, lambda: item(session, a, target_metric="followers"))
    await rejected(session, lambda: item(session, a, risk_class="D"))
    await rejected(
        session,
        lambda: session.execute(
            text(
                "INSERT INTO knowledge_items (account_id, kind, layer, element, statement,"
                " guidance, risk_class, origin, detector) VALUES (:a, 'thesis', 'craft', 'hook',"
                " 'Достаточно длинное утверждение', 'Указание тут', 'B', 'materials',"
                ' \'{"type": "magic"}\')'
            ),
            {"a": a},
        ),
    )
    await rejected(
        session,
        lambda: session.execute(
            text(
                "INSERT INTO knowledge_items (account_id, kind, layer, element, statement,"
                " guidance, risk_class, origin) VALUES (:a, 'thesis', 'craft', 'hook',"
                " 'коротко', 'Указание тут', 'B', 'materials')"
            ),
            {"a": a},
        ),
    )
    target = await item(session, a)
    await ok(session, lambda: item(session, a, status="merged", merged_into=target))
    foreign = await item(session, b)
    await rejected(session, lambda: item(session, a, status="merged", merged_into=foreign))
    defaults = (
        await session.execute(
            text("SELECT eff_conf, fit_conf, quality, version FROM knowledge_items WHERE id = :i"),
            {"i": target},
        )
    ).one()
    assert [float(x) for x in defaults[:3]] == [0.2113, 0.4310, 0.2992]
    assert defaults[3] == 1


async def test_item_children_cannot_point_to_other_account(session: AsyncSession) -> None:
    a, b = await account(session), await account(session)
    item_b = await item(session, b)
    uid = await user(session)
    await rejected(
        session,
        lambda: session.execute(
            text(
                "INSERT INTO knowledge_item_versions (item_id, account_id, version, statement,"
                " guidance, detector, kind, layer, element, target_metric, changed_by, reason)"
                " VALUES (:i, :a, 1, 's', 'g', '{}', 'thesis', 'craft', 'hook', 'engagement',"
                " 'system', 'extracted')"
            ),
            {"i": item_b, "a": a},
        ),
    )
    await rejected(
        session,
        lambda: session.execute(
            text(
                "INSERT INTO knowledge_item_scopes (item_id, account_id, facet, value)"
                " VALUES (:i, :a, 'format', 'post')"
            ),
            {"i": item_b, "a": a},
        ),
    )
    await rejected(
        session,
        lambda: insert(
            session,
            "knowledge_transitions",
            account_id=a,
            item_id=item_b,
            to_status="active",
            actor="owner",
            user_id=uid,
            rule="owner_confirm",
        ),
    )
    await rejected(
        session,
        lambda: evidence(
            session, a, item_b, observed_at=NOW, basis="owner_decision", dimension="fit"
        ),
    )
    item_a = await item(session, a)
    await rejected(
        session,
        lambda: insert(
            session,
            "knowledge_links",
            account_id=a,
            from_item_id=item_a,
            to_item_id=item_b,
            relation="contradicts",
            confidence=0.9,
            decided_by="judge",
        ),
    )


async def test_item_versions_and_scopes(session: AsyncSession) -> None:
    acc = await account(session)
    iid = await item(session, acc)
    sql = (
        "INSERT INTO knowledge_item_versions (item_id, account_id, version, statement,"
        " guidance, detector, kind, layer, element, target_metric, changed_by, reason)"
        " VALUES (:i, :a, :v, 's', 'g', '{}', 'thesis', 'craft', 'hook', 'engagement',"
        " :by, 'extracted')"
    )
    await ok(
        session, lambda: session.execute(text(sql), {"i": iid, "a": acc, "v": 1, "by": "system"})
    )
    await rejected(
        session, lambda: session.execute(text(sql), {"i": iid, "a": acc, "v": 1, "by": "system"})
    )
    await rejected(
        session, lambda: session.execute(text(sql), {"i": iid, "a": acc, "v": 2, "by": "cat"})
    )
    scope = "INSERT INTO knowledge_item_scopes (item_id, account_id, facet, value) VALUES (:i, :a, :f, :v)"
    await ok(
        session,
        lambda: session.execute(text(scope), {"i": iid, "a": acc, "f": "format", "v": "post"}),
    )
    await ok(
        session, lambda: session.execute(text(scope), {"i": iid, "a": acc, "f": "lang", "v": "en"})
    )
    await rejected(
        session,
        lambda: session.execute(text(scope), {"i": iid, "a": acc, "f": "format", "v": "post"}),
    )
    await rejected(
        session,
        lambda: session.execute(text(scope), {"i": iid, "a": acc, "f": "colour", "v": "red"}),
    )


async def test_knowledge_evidence_checks(session: AsyncSession) -> None:
    acc = await account(session)
    iid = await item(session, acc)
    sid = await source(session, acc)
    eid = await ok(
        session,
        lambda: evidence(
            session,
            acc,
            iid,
            observed_at=NOW,
            source_id=sid,
            w_source=0.7,
            w_scope=2,
            w_magnitude=0.5,
        ),
    )
    weight = await scalar(session, "SELECT weight FROM knowledge_evidence WHERE id = :i", i=eid)
    assert float(weight) == pytest.approx(0.7)  # 0.7 × 2 × 0.5 × 1
    # без материала, черновика и не решение владельца — нельзя
    await rejected(session, lambda: evidence(session, acc, iid, observed_at=NOW))
    # решение по черновику и решение владельца — только совместимость
    await rejected(
        session,
        lambda: evidence(
            session, acc, iid, observed_at=NOW, basis="owner_decision", dimension="eff"
        ),
    )
    did = await draft(session, acc)
    await rejected(
        session, lambda: evidence(session, acc, iid, observed_at=NOW, basis="review", draft_id=did)
    )
    await ok(
        session,
        lambda: evidence(
            session, acc, iid, observed_at=NOW, basis="review", dimension="fit", draft_id=did
        ),
    )
    # дубль неотозванного доказательства по тому же материалу
    await rejected(session, lambda: evidence(session, acc, iid, observed_at=NOW, source_id=sid))
    await rejected(
        session,
        lambda: evidence(
            session, acc, iid, observed_at=NOW, basis="review", dimension="fit", draft_id=did
        ),
    )
    # отзыв: время и причина вместе
    await rejected(
        session,
        lambda: session.execute(
            text("UPDATE knowledge_evidence SET revoked_at = now() WHERE id = :i"), {"i": eid}
        ),
    )
    await ok(
        session,
        lambda: session.execute(
            text(
                "UPDATE knowledge_evidence SET revoked_at = now(), revoked_reason = 'owner' WHERE id = :i"
            ),
            {"i": eid},
        ),
    )
    await ok(
        session, lambda: evidence(session, acc, iid, observed_at=NOW, source_id=sid)
    )  # после отзыва можно
    for bad in (
        {"polarity": 2},
        {"w_source": 4},
        {"w_scope": 3},
        {"w_magnitude": 1.5},
        {"w_author": 2},
        {"detector_score": 1.2},
        {"basis": "rumour"},
    ):
        s2 = await source(session, acc)
        await rejected(
            session,
            lambda b=bad, s=s2: evidence(session, acc, iid, observed_at=NOW, source_id=s, **b),
        )


async def test_voice_accepts_evidence_only_from_own_stance(session: AsyncSession) -> None:
    acc = await account(session)
    voice = await item(
        session, acc, layer="voice", kind="lexicon", element="lexicon", target_metric="quality"
    )
    craft = await item(session, acc)
    own = await source(session, acc, stance="own")
    for stance in ("inspiration", "competitor", "anti_example", "reference"):
        foreign = await source(session, acc, stance=stance)
        err = await rejected(
            session, lambda f=foreign: evidence(session, acc, voice, observed_at=NOW, source_id=f)
        )
        assert "only from own" in err
        await ok(
            session, lambda f=foreign: evidence(session, acc, craft, observed_at=NOW, source_id=f)
        )
    await ok(
        session,
        lambda: evidence(session, acc, voice, observed_at=NOW, source_id=own, basis="own_outcome"),
    )
    await ok(
        session,
        lambda: evidence(
            session, acc, voice, observed_at=NOW, basis="owner_decision", dimension="fit"
        ),
    )


async def test_knowledge_links_checks(session: AsyncSession) -> None:
    acc = await account(session)
    x, y = await item(session, acc), await item(session, acc)

    def link(**kw: Any) -> Awaitable[UUID]:
        base: dict[str, Any] = {
            "account_id": acc,
            "from_item_id": x,
            "to_item_id": y,
            "relation": "contradicts",
            "confidence": 0.9,
            "decided_by": "judge",
        }
        return insert(session, "knowledge_links", **{**base, **kw})

    await ok(session, lambda: link())
    await rejected(session, lambda: link())
    await ok(session, lambda: link(relation="related"))
    await rejected(session, lambda: link(to_item_id=x, relation="refines"))
    await rejected(session, lambda: link(relation="hates"))
    await rejected(session, lambda: link(relation="duplicates", confidence=1.1))
    await rejected(session, lambda: link(relation="duplicates", decided_by="cat"))
    await rejected(session, lambda: link(relation="duplicates", status="pending"))


async def test_knowledge_transitions_checks(session: AsyncSession) -> None:
    acc = await account(session)
    iid = await item(session, acc)
    await ok(
        session,
        lambda: insert(
            session,
            "knowledge_transitions",
            account_id=acc,
            item_id=iid,
            to_status="probation",
            actor="system",
            rule="auto_probation",
        ),
    )
    await rejected(
        session,
        lambda: insert(
            session,
            "knowledge_transitions",
            account_id=acc,
            item_id=iid,
            to_status="probation",
            actor="robot",
            rule="auto_probation",
        ),
    )


async def test_knowledge_embeddings_and_threshold_columns(session: AsyncSession) -> None:
    acc = await account(session)
    iid = await item(session, acc)
    model_id = await scalar(session, "SELECT id FROM embedding_models WHERE active")
    thresholds = (
        await session.execute(
            text("SELECT dup_threshold, related_threshold FROM embedding_models WHERE id = :m"),
            {"m": model_id},
        )
    ).one()
    assert [float(t) for t in thresholds] == [0.88, 0.70]
    vec = "[" + ",".join(["0.1"] * 384) + "]"
    sql = "INSERT INTO knowledge_embeddings (item_id, account_id, model_id, version, vec) VALUES (:i, :a, :m, 1, CAST(:v AS vector))"
    await ok(
        session, lambda: session.execute(text(sql), {"i": iid, "a": acc, "m": model_id, "v": vec})
    )
    await rejected(
        session, lambda: session.execute(text(sql), {"i": iid, "a": acc, "m": model_id, "v": vec})
    )
    await rejected(
        session,
        lambda: session.execute(
            text("UPDATE embedding_models SET dup_threshold = 1.5 WHERE id = :m"), {"m": model_id}
        ),
    )


async def test_knowledge_snapshots_primary_key(session: AsyncSession) -> None:
    acc = await account(session)
    iid = await item(session, acc)
    sql = (
        "INSERT INTO knowledge_snapshots (snapshot_date, account_id, item_id, status, owner_state,"
        " version, layer, eff_alpha, eff_beta, fit_alpha, fit_beta, eff_conf, fit_conf, quality,"
        " evidence_n, own_support, own_contra) VALUES ('2026-11-13', :a, :i, 'candidate', 'none',"
        " 1, 'craft', 1, 1, 2, 1, 0.2113, 0.431, 0.2992, 0, 0, 0)"
    )
    await ok(session, lambda: session.execute(text(sql), {"a": acc, "i": iid}))
    await rejected(session, lambda: session.execute(text(sql), {"a": acc, "i": iid}))


async def test_packs_applications_and_draft_links(session: AsyncSession) -> None:
    a, b = await account(session), await account(session)
    pid = await pack(session, a)
    foreign_pack = await pack(session, b)
    iid = await item(session, a, status="probation")
    did = await draft(session, a)
    run_id = await scalar(session, "SELECT run_id FROM drafts WHERE id = :d", d=did)
    await ok(
        session,
        lambda: session.execute(
            text("UPDATE generation_runs SET knowledge_pack_id = :p WHERE id = :r"),
            {"p": pid, "r": run_id},
        ),
    )
    await ok(
        session,
        lambda: session.execute(
            text("UPDATE drafts SET probation_item_id = :i WHERE id = :d"), {"i": iid, "d": did}
        ),
    )
    await rejected(
        session,
        lambda: session.execute(
            text("UPDATE drafts SET probation_item_id = gen_random_uuid() WHERE id = :d"),
            {"d": did},
        ),
    )

    def app(**kw: Any) -> Awaitable[UUID]:
        base: dict[str, Any] = {
            "account_id": a,
            "pack_id": pid,
            "item_id": iid,
            "draft_id": did,
            "slot": "probation",
        }
        return insert(session, "knowledge_applications", **{**base, **kw})

    await rejected(session, lambda: app(slot="extra"))
    await rejected(session, lambda: app(pack_id=foreign_pack))
    await rejected(session, lambda: app(applied_score=1.5))
    await rejected(session, lambda: app(checked_by="vibes"))
    await ok(session, lambda: app(applied=True, applied_score=0.9, checked_by="feature"))
    await rejected(session, lambda: app())  # (draft_id, item_id) уникальны
    await rejected(
        session,
        lambda: session.execute(
            text(
                "INSERT INTO knowledge_packs (account_id, consumer, task, schema_version,"
                " taxonomy_version, items, token_count) VALUES (:a, 'twitter', '{}', 1, 1, '[]', 1)"
            ),
            {"a": a},
        ),
    )


async def test_segments_checks(session: AsyncSession) -> None:
    acc = await account(session)
    sql = "INSERT INTO segments (account_id, key, title, filter) VALUES (:a, :k, 'Хуки', CAST(:f AS jsonb))"
    await ok(
        session,
        lambda: session.execute(text(sql), {"a": acc, "k": "hooks", "f": '{"element": ["hook"]}'}),
    )
    await rejected(session, lambda: session.execute(text(sql), {"a": acc, "k": "hooks", "f": "{}"}))
    await rejected(
        session, lambda: session.execute(text(sql), {"a": acc, "k": "Hooks!", "f": "{}"})
    )


async def test_brain_objects_have_comments(session: AsyncSession) -> None:
    missing = (
        await session.execute(
            text(
                "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
                " WHERE n.nspname = 'public' AND p.proname IN"
                " ('knowledge_recompute_scores', 'knowledge_state_on')"
                " AND obj_description(p.oid, 'pg_proc') IS NULL"
            )
        )
    ).all()
    assert missing == []
    funcs = (
        await session.execute(
            text(
                "SELECT count(*) FROM pg_proc WHERE proname IN"
                " ('knowledge_recompute_scores', 'knowledge_state_on', 'trg_voice_evidence_own_only')"
            )
        )
    ).scalar()
    assert funcs == 3
