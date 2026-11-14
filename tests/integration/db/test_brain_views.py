"""Представления приложения B и knowledge_state_on на маленьком наборе."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.integration.db.brain_helpers import account, author, evidence, insert, item, source

NOW = datetime(2026, 11, 14, 12, tzinfo=UTC)


async def ids(session: AsyncSession, sql: str, **params: object) -> set[UUID]:
    return {r[0] for r in (await session.execute(text(sql), params)).all()}


async def scope(session: AsyncSession, acc: UUID, iid: UUID, facet: str, value: str) -> None:
    await session.execute(
        text(
            "INSERT INTO knowledge_item_scopes (item_id, account_id, facet, value)"
            " VALUES (:i, :a, :f, :v)"
        ),
        {"i": iid, "a": acc, "f": facet, "v": value},
    )


async def contradiction(
    session: AsyncSession, acc: UUID, x: UUID, y: UUID, status: str = "open"
) -> None:
    await insert(
        session,
        "knowledge_links",
        account_id=acc,
        from_item_id=x,
        to_item_id=y,
        relation="contradicts",
        confidence=0.9,
        decided_by="judge",
        status=status,
    )


async def test_v_knowledge_usable(session: AsyncSession) -> None:
    acc = await account(session)
    active = await item(session, acc, status="active")
    probation = await item(session, acc, status="probation")
    disabled = await item(session, acc, status="active", owner_state="disabled")
    await item(session, acc, status="candidate")
    await item(session, acc, status="archived")
    left = await item(session, acc, status="active")
    right = await item(session, acc, status="active")
    await contradiction(session, acc, left, right)
    resolved_l = await item(session, acc, status="active")
    resolved_r = await item(session, acc, status="active")
    await contradiction(session, acc, resolved_l, resolved_r, status="resolved")
    usable = await ids(session, "SELECT id FROM v_knowledge_usable WHERE account_id = :a", a=acc)
    assert usable == {active, probation, resolved_l, resolved_r}
    assert disabled not in usable


async def test_v_knowledge_cards(session: AsyncSession) -> None:
    acc = await account(session)
    high = await item(session, acc, status="active", quality=0.65)
    medium = await item(session, acc, status="active", quality=0.5)
    low = await item(session, acc, status="active")
    merged = await item(session, acc, status="merged", merged_into=high)
    await scope(session, acc, high, "format", "thread")
    await scope(session, acc, high, "format", "post")
    await scope(session, acc, high, "lang", "en")
    await contradiction(session, acc, high, low)
    rows = {
        r.id: r
        for r in (
            await session.execute(
                text("SELECT * FROM v_knowledge_cards WHERE account_id = :a"), {"a": acc}
            )
        ).all()
    }
    assert merged not in rows
    assert rows[high].confidence_label == "high"
    assert rows[medium].confidence_label == "medium"
    assert rows[low].confidence_label == "low"
    assert rows[high].scopes == {"format": ["post", "thread"], "lang": ["en"]}
    assert rows[medium].scopes == {}
    assert rows[high].open_conflicts == 1 and rows[medium].open_conflicts == 0


async def test_v_segment_items(session: AsyncSession) -> None:
    acc = await account(session)
    hook_any = await item(session, acc, element="hook")
    hook_thread = await item(session, acc, element="hook")
    await scope(session, acc, hook_thread, "format", "thread")
    hook_post = await item(session, acc, element="hook")
    await scope(session, acc, hook_post, "format", "post")
    voice = await item(session, acc, layer="voice", kind="lexicon", element="lexicon")
    await item(session, acc, element="hook", status="rejected")
    for key, flt in (
        ("hooks", '{"element": ["hook"]}'),
        ("threads", '{"format": ["thread"], "layer": ["craft", "niche", "platform", "humanness"]}'),
        ("voice", '{"layer": ["voice"]}'),
    ):
        await session.execute(
            text(
                "INSERT INTO segments (account_id, key, title, filter)"
                " VALUES (:a, :k, :k, CAST(:f AS jsonb))"
            ),
            {"a": acc, "k": key, "f": flt},
        )

    async def seg(key: str) -> set[UUID]:
        return await ids(
            session,
            "SELECT item_id FROM v_segment_items WHERE account_id = :a AND segment_key = :k",
            a=acc,
            k=key,
        )

    assert await seg("hooks") == {hook_any, hook_thread, hook_post}
    assert await seg("threads") == {hook_any, hook_thread}  # без области — подходит
    assert await seg("voice") == {voice}


async def test_v_knowledge_evidence_summary(session: AsyncSession) -> None:
    acc = await account(session)
    iid = await item(session, acc)
    for polarity, w in ((1, 0.7), (1, 0.7), (-1, 0.5), (0, 1.0)):
        sid = await source(session, acc)
        await evidence(
            session,
            acc,
            iid,
            observed_at=NOW,
            source_id=sid,
            polarity=polarity,
            w_source=w,
            format="post",
        )
    revoked_src = await source(session, acc)
    await evidence(
        session,
        acc,
        iid,
        observed_at=NOW,
        source_id=revoked_src,
        format="post",
        revoked_at=NOW,
        revoked_reason="owner",
    )
    row = (
        await session.execute(
            text("SELECT * FROM v_knowledge_evidence_summary WHERE item_id = :i"), {"i": iid}
        )
    ).one()
    assert (row.supports, row.contradicts, row.neutral) == (2, 1, 1)
    assert float(row.support_weight) == 1.4 and float(row.contra_weight) == 0.5
    assert row.dimension == "eff" and row.basis == "observed_outcome" and row.format == "post"


async def test_v_material_best_signal(session: AsyncSession) -> None:
    acc = await account(session)
    sid = await source(session, acc)
    await insert(
        session,
        "material_signals",
        account_id=acc,
        source_id=sid,
        origin="owner_label",
        owner_label="hit",
        observed_at=NOW,
    )
    shot_old = await insert(
        session,
        "material_signals",
        account_id=acc,
        source_id=sid,
        origin="screenshot",
        views=10,
        observed_at=NOW - timedelta(days=2),
    )
    shot_new = await insert(
        session,
        "material_signals",
        account_id=acc,
        source_id=sid,
        origin="screenshot",
        views=20,
        observed_at=NOW - timedelta(days=1),
    )
    best = await ids(session, "SELECT id FROM v_material_best_signal WHERE source_id = :s", s=sid)
    assert best == {shot_new} and shot_old not in best
    api = await insert(
        session,
        "material_signals",
        account_id=acc,
        source_id=sid,
        origin="x_api",
        views=30,
        observed_at=NOW - timedelta(days=5),
    )
    assert await ids(
        session, "SELECT id FROM v_material_best_signal WHERE source_id = :s", s=sid
    ) == {api}


async def test_knowledge_state_on(session: AsyncSession) -> None:
    acc = await account(session)
    iid = await item(session, acc)
    await author(session, acc, "x")
    sql = (
        "INSERT INTO knowledge_snapshots (snapshot_date, account_id, item_id, status, owner_state,"
        " version, layer, eff_alpha, eff_beta, fit_alpha, fit_beta, eff_conf, fit_conf, quality,"
        " evidence_n, own_support, own_contra) VALUES (:d, :a, :i, :s, 'none', 1, 'craft',"
        " 1, 1, 2, 1, 0.2, 0.4, 0.3, 0, 0, 0)"
    )
    for d, s in (("2026-11-01", "candidate"), ("2026-11-05", "probation")):
        await session.execute(text(sql), {"d": d, "a": acc, "i": iid, "s": s})

    async def state(day: str) -> list[str]:
        rows = await session.execute(
            text("SELECT status FROM knowledge_state_on(:a, CAST(:d AS date))"),
            {"a": acc, "d": day},
        )
        return [r[0] for r in rows.all()]

    assert await state("2026-10-31") == []
    assert await state("2026-11-03") == ["candidate"]
    assert await state("2026-11-05") == ["probation"]
    assert await state("2026-12-01") == ["probation"]
