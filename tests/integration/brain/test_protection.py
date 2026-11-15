"""Защита (этап 9A, задачи 11–12): отзыв доказательств, откат на дату и его отмена."""

from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.brain_stand import knowledge, make_ctx, material, new_account, new_author
from tests.integration.db.brain_helpers import user
from xcopilot.brain import lifecycle, rollback
from xcopilot.brain.context import BrainContext
from xcopilot.brain.evidence import record_from_material, revoke
from xcopilot.brain.scoring import recompute
from xcopilot.clock import SystemClock
from xcopilot.db.repos.knowledge import KnowledgeItemsRepo
from xcopilot.db.uow import UnitOfWork

SF = async_sessionmaker[AsyncSession]


async def one(session: AsyncSession, sql: str, **params: Any) -> Any:
    return (await session.execute(text(sql), params)).one()


async def test_remove_material_revokes_evidence_and_recomputes(session: AsyncSession) -> None:
    acc = await new_account(session)
    iid = await knowledge(session, acc)
    ctx = make_ctx(session, acc)
    sid = await material(session, acc, author_id=await new_author(session, acc))
    await record_from_material(ctx, sid)
    await recompute(ctx)
    assert (
        float((await one(session, "SELECT eff_alpha FROM knowledge_items WHERE id = :i", i=iid))[0])
        > 1
    )
    assert await revoke(ctx, source_id=sid, reason="source_removed") == 1
    row = await one(
        session, "SELECT revoked_reason FROM knowledge_evidence WHERE item_id = :i", i=iid
    )
    assert row.revoked_reason == "source_removed"
    scores = await one(
        session, "SELECT eff_alpha, evidence_n FROM knowledge_items WHERE id = :i", i=iid
    )
    assert (float(scores.eff_alpha), scores.evidence_n) == (1.0, 0)
    removed = await one(session, "SELECT removed_at FROM sources WHERE id = :s", s=sid)
    assert removed.removed_at is not None


async def test_block_author_revokes_his_evidence(session: AsyncSession) -> None:
    acc = await new_account(session)
    iid = await knowledge(session, acc)
    ctx = make_ctx(session, acc)
    bad, good = await new_author(session, acc), await new_author(session, acc)
    for a in (bad, bad, good):
        await record_from_material(ctx, await material(session, acc, author_id=a))
    assert await revoke(ctx, author_id=bad, reason="author_blocked") == 2
    left = await one(
        session,
        "SELECT count(*) FILTER (WHERE revoked_at IS NULL) AS live,"
        " count(*) FILTER (WHERE revoked_reason = 'author_blocked') AS gone"
        " FROM knowledge_evidence WHERE item_id = :i",
        i=iid,
    )
    assert (left.live, left.gone) == (1, 2)
    assert (await one(session, "SELECT trust FROM authors WHERE id = :a", a=bad)).trust == -1
    assert (
        await one(session, "SELECT evidence_n FROM knowledge_items WHERE id = :i", i=iid)
    ).evidence_n == 1
    # новые материалы заблокированного автора не учатся
    assert await record_from_material(ctx, await material(session, acc, author_id=bad)) == 0


# ---------------------------------------------------------------- откат на дату
class World:
    """База до и после момента T (каждый шаг — своя транзакция, чтобы now() различался)."""

    def __init__(self, sf: SF) -> None:
        self.sf = sf
        self.clock = SystemClock()
        self.acc: UUID
        self.a: UUID  # знание, созданное до T
        self.b: UUID  # знание, созданное после T
        self.t: datetime

    def ctx(self, session: AsyncSession) -> BrainContext:
        return make_ctx(session, self.acc, clock=self.clock)  # type: ignore[arg-type]

    async def step[T](self, fn: Callable[[AsyncSession], Any]) -> Any:
        async with UnitOfWork(self.sf) as uow:
            return await fn(uow.session)

    async def build(self) -> None:
        async def before(s: AsyncSession) -> None:
            self.acc = await new_account(s)
            self.a = await knowledge(s, self.acc, statement="Цифра в первой строке даёт ответы")
            ctx = self.ctx(s)
            await record_from_material(
                ctx, await material(s, self.acc, author_id=await new_author(s, self.acc))
            )
            await recompute(ctx)

        await self.step(before)
        self.t = (
            await self.step(lambda s: s.execute(text("SELECT clock_timestamp()")))
        ).scalar_one()

        async def after(s: AsyncSession) -> None:
            ctx = self.ctx(s)
            # детектор B не срабатывает на материалах стенда: доказательства после T — только у A
            self.b = await knowledge(
                s,
                self.acc,
                statement="Призыв в конце поста даёт ответы",
                detector={
                    "type": "feature",
                    "all": [{"path": "closer", "op": "eq", "value": "cta"}],
                },
            )
            for _ in range(2):
                await record_from_material(
                    ctx, await material(s, self.acc, author_id=await new_author(s, self.acc))
                )
            await KnowledgeItemsRepo(s, self.acc).edit(
                self.a,
                changed_by="system",
                reason="merged",
                statement="Личная цифра в первой строке даёт ответы",
            )
            await lifecycle.apply(
                ctx,
                lifecycle.Transition(
                    item_id=self.a,
                    from_status="candidate",
                    to_status="probation",
                    rule="auto_probation",
                    reason="тест",
                ),
            )
            await recompute(ctx)

        await self.step(after)

    async def state(self) -> dict[str, Any]:
        async def read(s: AsyncSession) -> dict[str, Any]:
            items = {
                r.id: (r.status, r.statement, round(float(r.eff_alpha), 4))
                for r in (
                    await s.execute(
                        text(
                            "SELECT id, status, statement, eff_alpha FROM knowledge_items"
                            " WHERE account_id = :a"
                        ),
                        {"a": self.acc},
                    )
                ).all()
            }
            live = (
                await s.execute(
                    text(
                        "SELECT count(*) FROM knowledge_evidence"
                        " WHERE account_id = :a AND revoked_at IS NULL"
                    ),
                    {"a": self.acc},
                )
            ).scalar_one()
            return {"items": items, "live_evidence": live}

        result: dict[str, Any] = await self.step(read)
        return result


async def test_rollback_plan_counts_affected(session_factory: SF) -> None:
    w = World(session_factory)
    await w.build()
    p = await w.step(lambda s: rollback.plan(w.ctx(s), w.t))
    assert p.created_items == [w.b]
    assert p.changed_items == [w.a]
    assert p.evidence_added == 2


async def test_rollback_restores_statuses_texts_and_scores(session_factory: SF) -> None:
    w = World(session_factory)
    await w.build()
    after = await w.state()
    p = await w.step(lambda s: rollback.plan(w.ctx(s), w.t))
    uid = await w.step(user)
    await w.step(lambda s: rollback.execute(w.ctx(s), p, uid))
    state = await w.state()
    a_status, a_text, a_alpha = state["items"][w.a]
    assert a_status == "candidate"
    assert a_text == "Цифра в первой строке даёт ответы"
    assert state["items"][w.b][0] == "archived"
    assert state["live_evidence"] == 1
    assert a_alpha < after["items"][w.a][2]
    events = await w.step(
        lambda s: s.execute(
            text(
                "SELECT count(*) FROM events WHERE account_id = :a AND type = 'knowledge.rollback'"
            ),
            {"a": w.acc},
        )
    )
    assert events.scalar_one() == 1


async def test_rollback_keeps_materials_and_analyses(session_factory: SF) -> None:
    w = World(session_factory)
    await w.build()

    async def counts(s: AsyncSession) -> tuple[int, int, int]:
        row = (
            await s.execute(
                text(
                    "SELECT (SELECT count(*) FROM sources"
                    " WHERE account_id = :a AND removed_at IS NULL),"
                    " (SELECT count(*) FROM material_analyses WHERE account_id = :a),"
                    " (SELECT count(*) FROM material_outcomes WHERE account_id = :a)"
                ),
                {"a": w.acc},
            )
        ).one()
        return int(row[0]), int(row[1]), int(row[2])

    before = await w.step(counts)
    p = await w.step(lambda s: rollback.plan(w.ctx(s), w.t))
    await w.step(lambda s: rollback.execute(w.ctx(s), p, None))
    assert await w.step(counts) == before == (3, 3, 3)


async def test_rollback_undo_restores_everything(session_factory: SF) -> None:
    w = World(session_factory)
    await w.build()
    after = await w.state()
    p = await w.step(lambda s: rollback.plan(w.ctx(s), w.t))
    rid = await w.step(lambda s: rollback.execute(w.ctx(s), p, None))
    await w.step(lambda s: rollback.undo(w.ctx(s), rid, None))
    assert await w.state() == after
