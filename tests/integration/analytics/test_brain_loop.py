"""Этап 14 (v3.1): свой пост проверяет знания базы — разбор, детекторы, исход, доказательства."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from tests.analytics_stand import AStand
from tests.brain_core_stand import knowledge
from tests.brain_stand import analysis_json
from tests.integration.db.brain_helpers import author, evidence, source

WITH_NUMBER = "12 провалов за год.\nВот что я понял.\nА вы?"
WITHOUT_NUMBER = "Как мы росли без цифр.\nВсё просто.\nА вы?"
HOOK = "Личная цифра в первой строке даёт больше ответов"


async def _item(st: AStand, **extra: object) -> UUID:
    async with st.sf() as s:
        iid = await knowledge(s, st.account_id, statement=HOOK, **extra)  # type: ignore[arg-type]
        await s.commit()
    return iid


async def _post(
    st: AStand, body: str, *, post_id: str, likes: int, views: int, bot: bool = False
) -> UUID:
    """Пост 26 часов назад: синк → разбор моделью → снимок на отметке 24 часа."""
    st.own_post(body, age=timedelta(hours=26), post_id=post_id, likes=likes, views=views)
    if bot:
        await st.publish(body, post_id, st.clock.now() - timedelta(hours=26))
    await st.sync.sync_own_posts(st.account_id)
    [sid] = [
        UUID(str(r[0]))
        for r in await st.rows(
            "SELECT id FROM sources WHERE account_id = :a AND external_id = :x",
            a=st.account_id,
            x=post_id,
        )
    ]
    st.b.llm.push(analysis_json(body))
    assert await st.b.analyzer.analyze(st.account_id, sid) is not None
    await st.sync.take_snapshot(st.account_id, post_id, 1440)
    return sid


async def _evidence(st: AStand, item: UUID) -> list[tuple[str, int, float, str | None]]:
    rows = await st.rows(
        "SELECT basis, polarity, w_scope::float, outcome_tier FROM knowledge_evidence"
        " WHERE item_id = :i AND revoked_at IS NULL",
        i=item,
    )
    return [tuple(r) for r in rows]


async def test_synced_own_post_analyzed_and_detected(st: AStand) -> None:
    item = await _item(st)
    sid = await _post(st, WITH_NUMBER, post_id="701", likes=25, views=1000)
    analysis = await st.rows(
        "SELECT count(*) FROM material_analyses WHERE source_id = :s AND is_current", s=sid
    )
    assert analysis[0][0] == 1
    scores = await st.rows(
        "SELECT detector_score FROM knowledge_evidence WHERE item_id = :i", i=item
    )
    assert len(scores) == 1 and scores[0][0] is not None  # детектор сработал на разборе


async def test_own_outcome_at_24h_checkpoint(st: AStand) -> None:
    await _item(st)
    st.own_post(WITH_NUMBER, age=timedelta(hours=26), post_id="702", likes=25, views=1000)
    await st.sync.sync_own_posts(st.account_id)
    sid = (await st.rows("SELECT id FROM sources WHERE external_id = '702'"))[0][0]
    await st.sync.take_snapshot(st.account_id, "702", 60)  # отметка 1 ч: исхода нет
    assert await st.count("material_outcomes", "source_id = :s", s=sid) == 0
    res = await st.sync.take_snapshot(st.account_id, "702", 1440)
    assert res.outcome_basis is not None
    row = (
        await st.rows(
            "SELECT signal_origin, tier_engagement FROM material_outcomes"
            " WHERE source_id = :s AND is_current",
            s=sid,
        )
    )[0]
    assert (row[0], row[1]) == ("own_sync", "above")


async def test_present_and_above_gives_own_support_scope_2(st: AStand) -> None:
    item = await _item(st)
    await _post(st, WITH_NUMBER, post_id="703", likes=25, views=1000)  # вовлечённость ≈ 2.6%
    assert await _evidence(st, item) == [("own_outcome", 1, 2.0, "above")]


async def test_present_and_flop_gives_own_contra(st: AStand) -> None:
    item = await _item(st)
    await _post(st, WITH_NUMBER, post_id="704", likes=10, views=10_000)
    assert await _evidence(st, item) == [("own_outcome", -1, 2.0, "flop")]


async def test_absent_item_gets_no_evidence(st: AStand) -> None:
    item = await _item(st)
    await _post(st, WITHOUT_NUMBER, post_id="705", likes=25, views=1000)
    assert await _evidence(st, item) == []


async def test_posts_without_bot_also_count(st: AStand) -> None:
    item = await _item(st)
    await _post(st, WITH_NUMBER, post_id="706", likes=25, views=1000, bot=False)
    await _post(st, WITH_NUMBER + " Ещё.", post_id="707", likes=25, views=1000, bot=True)
    assert len(await _evidence(st, item)) == 2
    flags = await st.rows("SELECT meta ->> 'bot_assisted' FROM sources ORDER BY external_id")
    assert [r[0] for r in flags] == ["false", "true"]


async def test_first_own_outcome_lifts_foreign_cap(st: AStand) -> None:
    item = await _item(st)
    async with st.sf() as s:
        for i in range(10):
            auth = await author(s, st.account_id, f"foreign{i}")
            sid = await source(
                s, st.account_id, origin="screenshot", author_id=auth, posted_at=st.clock.now()
            )
            await evidence(
                s, st.account_id, item, observed_at=st.clock.now(), source_id=sid,
                author_id=auth, w_source=0.7,
            )  # fmt: skip
        await s.commit()
    from tests.brain_core_stand import make_ctx
    from xcopilot.brain.scoring import recompute

    async with st.sf() as s:
        await recompute(make_ctx(s, st.account_id, clock=st.clock), item, now=st.clock.now())
        await s.commit()
    before = (await st.rows("SELECT eff_conf FROM knowledge_items WHERE id = :i", i=item))[0][0]
    assert round(float(before), 2) == 0.70  # потолок: исходов на своих постах ещё нет
    await _post(st, WITH_NUMBER, post_id="708", likes=25, views=1000)
    after = (await st.rows("SELECT eff_conf FROM knowledge_items WHERE id = :i", i=item))[0][0]
    assert float(after) > 0.70
