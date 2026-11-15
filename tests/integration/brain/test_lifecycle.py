"""Переходы в БД (этап 9A): журнал с числами, откат перехода."""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.brain_core_stand import NOW, knowledge, make_ctx, new_account
from tests.integration.db.brain_helpers import author, evidence, source, user
from xcopilot.brain import lifecycle
from xcopilot.brain.scoring import recompute


async def ready_item(session: AsyncSession, acc: UUID) -> UUID:
    """Кандидат класса B: три чужих хита от разных авторов, совместимость 0.8."""
    iid = await knowledge(session, acc, provenance={"voice_compat": 0.8})
    for i in range(3):
        auth = await author(session, acc, f"r{i}_{iid.hex[:6]}")
        sid = await source(session, acc, origin="screenshot", author_id=auth)
        await evidence(
            session, acc, iid, observed_at=NOW, source_id=sid, author_id=auth, w_source=0.7
        )
    return iid


async def test_every_transition_logged_with_numbers(session: AsyncSession) -> None:
    acc = await new_account(session)
    iid = await ready_item(session, acc)
    ctx = make_ctx(session, acc)
    await recompute(ctx, now=NOW)
    [t] = await lifecycle.run(ctx, NOW)
    assert (t.item_id, t.to_status, t.rule) == (iid, "probation", "auto_probation")
    row = (
        await session.execute(
            text(
                "SELECT from_status, to_status, actor, rule, payload FROM knowledge_transitions"
                " WHERE item_id = :i"
            ),
            {"i": iid},
        )
    ).one()
    assert (row.from_status, row.to_status, row.actor, row.rule) == (
        "candidate",
        "probation",
        "system",
        "auto_probation",
    )
    for key in (
        "eff_conf",
        "fit_conf",
        "quality",
        "evidence_n",
        "independent_authors",
        "voice_compat",
    ):
        assert key in row.payload, key
    assert row.payload["evidence_n"] == 3
    assert "thresholds" in row.payload
    status = (
        await session.execute(text("SELECT status FROM knowledge_items WHERE id = :i"), {"i": iid})
    ).scalar_one()
    assert status == "probation"
    events = (
        await session.execute(
            text(
                "SELECT count(*) FROM events WHERE account_id = :a"
                " AND type = 'knowledge.transition'"
            ),
            {"a": acc},
        )
    ).scalar_one()
    assert events == 1
    # повторный проход ничего не меняет
    assert await lifecycle.run(ctx, NOW) == []


async def test_revert_transition_restores_status(session: AsyncSession) -> None:
    acc = await new_account(session)
    iid = await ready_item(session, acc)
    ctx = make_ctx(session, acc)
    await recompute(ctx, now=NOW)
    [t] = await lifecycle.run(ctx, NOW)
    tid = (
        await session.execute(
            text("SELECT id FROM knowledge_transitions WHERE item_id = :i"), {"i": t.item_id}
        )
    ).scalar_one()
    uid = await user(session)
    await lifecycle.revert(ctx, tid, uid)
    row = (
        await session.execute(
            text(
                "SELECT i.status, t.reverted_at IS NOT NULL AS reverted, t.reverted_by"
                " FROM knowledge_items i JOIN knowledge_transitions t ON t.item_id = i.id"
                " WHERE i.id = :i"
            ),
            {"i": iid},
        )
    ).one()
    assert (row.status, row.reverted, row.reverted_by) == ("candidate", True, uid)
    reverted_events = (
        await session.execute(
            text(
                "SELECT count(*) FROM events WHERE account_id = :a AND type = 'knowledge.reverted'"
            ),
            {"a": acc},
        )
    ).scalar_one()
    assert reverted_events == 1
