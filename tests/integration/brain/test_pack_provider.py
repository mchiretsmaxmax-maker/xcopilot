"""Подключение 9B к этапам 9 и 10: KnowledgeProvider и PackRecorder — одна запись на выдачу."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.brain_core_stand import NOW, new_account
from tests.fakes import FakeClock
from tests.pack_stand import embedder, kitem, task
from xcopilot.brain.provider import BrainKnowledge, make_knowledge
from xcopilot.retrieval.context_pack import KnowledgeProvider
from xcopilot.writer.knowledge import PackRecorder
from xcopilot.writer.probation import ProbationPicker


async def packs(session: AsyncSession, acc: object) -> list[tuple[str, list[dict[str, object]]]]:
    rows = await session.execute(
        text("SELECT consumer, items FROM knowledge_packs WHERE account_id = :a"), {"a": acc}
    )
    return [(r[0], r[1]) for r in rows.all()]


def make() -> BrainKnowledge:
    return make_knowledge(embedder=embedder(), clock=FakeClock(NOW))


async def test_factory_returns_all_three_roles() -> None:
    k = make()
    provider: KnowledgeProvider = k
    recorder: PackRecorder = k
    picker: ProbationPicker = k.probation  # type: ignore[attr-defined]
    assert provider and recorder and picker


async def test_select_returns_pack_items_without_probation(session: AsyncSession) -> None:
    acc = await new_account(session)
    core = await kitem(session, acc)
    anti = await kitem(session, acc, kind="anti_pattern", element="general")
    pinned = await kitem(session, acc, owner_state="pinned")
    await kitem(session, acc, status="probation")
    got = await make().select(session, acc, task())
    assert {g.item_id: g.slot for g in got} == {pinned: "pinned", core: "core", anti: "anti"}
    assert all(g.guidance and g.statement for g in got)


async def test_one_pack_row_per_delivery(session: AsyncSession) -> None:
    acc = await new_account(session)
    for _ in range(3):
        await kitem(session, acc)
    trial = await kitem(session, acc, status="probation")
    k = make()
    t = task()
    items = await k.select(session, acc, t)
    probation = await k.probation.pick(session, acc, t)  # type: ignore[attr-defined]
    assert probation is not None and probation.item_id == trial
    pack_id = await k.record(session, acc, t, items, probation)
    rows = await packs(session, acc)
    assert len(rows) == 1 and rows[0][0] == "writer"
    stored = rows[0][1]
    assert [r["slot"] for r in stored][-1] == "probation"
    assert {r["item_id"] for r in stored} == {str(i.item_id) for i in items} | {str(trial)}
    assert pack_id is not None
    # без испытания — та же единственная строка, без слота испытания
    k2 = make()
    items2 = await k2.select(session, acc, t)
    await k2.record(session, acc, t, items2, None)
    rows = await packs(session, acc)
    assert len(rows) == 2
    assert all(r["slot"] != "probation" for r in rows[1][1])


async def test_record_without_prior_select_still_writes_one_row(session: AsyncSession) -> None:
    acc = await new_account(session)
    item = await kitem(session, acc)
    k = make()
    items = await make().select(session, acc, task())
    await session.execute(text("DELETE FROM knowledge_packs WHERE account_id = :a"), {"a": acc})
    await k.record(session, acc, task(), items, None)
    rows = await packs(session, acc)
    assert len(rows) == 1 and rows[0][1][0]["item_id"] == str(item)


async def test_by_ids_returns_texts_at_stored_versions(session: AsyncSession) -> None:
    acc = await new_account(session)
    a = await kitem(session, acc, guidance="Первый")
    b = await kitem(session, acc, guidance="Второй")
    got = await make().by_ids(session, acc, [b, a])
    assert [g.item_id for g in got] == [b, a]
    assert await make().by_ids(session, acc, []) == []
