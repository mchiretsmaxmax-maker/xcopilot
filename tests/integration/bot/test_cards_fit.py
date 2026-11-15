"""Этап 12 (v3.1): решения владельца по черновику → доказательства совместимости (fit, Б2.4)."""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.cards_stand import CardsStand, make_cards_stand
from tests.fakes import FakeTelegram
from xcopilot.bot.deps import BotDeps
from xcopilot.review.callbacks import APPROVE, EDIT, REJECT_REASON

SF = async_sessionmaker[AsyncSession]
HEAVY = "перенёс кошелёк на l2 и забыл про газ, мосты теперь скучные и тихие"


@pytest.fixture
async def st(session_factory: SF, telegram: FakeTelegram, deps: BotDeps) -> CardsStand:
    return await make_cards_stand(session_factory, telegram, deps)


async def setup(st: CardsStand) -> tuple[UUID, UUID, UUID]:
    """Два знания в пакете: первое применено (детектор ворот), второе — нет."""
    used = await st.gs.ws.knowledge_item("Начинай с вывода, детали потом")
    unused = await st.gs.ws.knowledge_item("Одна мысль на строку")
    st.gs.ws.knowledge.items = [used, unused]
    st.gs.detector.applied = {used.item_id}
    run = await st.generate()
    return (await st.finals(run))[0], used.item_id, unused.item_id


async def evidence(st: CardsStand, item_id: UUID) -> list[tuple[str, str, int, float]]:
    rows = await st.rows(
        "SELECT dimension, basis, polarity, w_source::float FROM knowledge_evidence"
        " WHERE item_id = :i ORDER BY created_at, id",
        i=item_id,
    )
    return [(r[0], r[1], r[2], r[3]) for r in rows]


async def test_approve_adds_fit_support_for_applied_items(st: CardsStand) -> None:
    d1, used, _ = await setup(st)
    await st.press(d1, APPROVE)
    assert await evidence(st, used) == [("fit", "review", 1, 1.0)]
    row = await st.rows("SELECT draft_id FROM knowledge_evidence WHERE item_id = :i", i=used)
    assert row[0][0] == d1


async def test_heavy_edit_adds_half_contra(st: CardsStand) -> None:
    d1, used, _ = await setup(st)
    await st.press(d1, EDIT)
    await st.owner.say(HEAVY)
    assert await evidence(st, used) == [("fit", "review", -1, 0.5)]


async def test_reject_not_my_voice_adds_fit_contra_15(st: CardsStand) -> None:
    d1, used, _ = await setup(st)
    await st.press(d1, REJECT_REASON, "not_my_voice")
    assert await evidence(st, used) == [("fit", "review", -1, 1.5)]


async def test_reject_off_topic_adds_no_fit_evidence(st: CardsStand) -> None:
    d1, used, unused = await setup(st)
    await st.press(d1, REJECT_REASON, "off_topic")
    assert await evidence(st, used) == []
    assert await evidence(st, unused) == []
    assert await st.status(d1) == "rejected"


async def test_not_applied_items_get_no_evidence(st: CardsStand) -> None:
    d1, used, unused = await setup(st)
    await st.press(d1, APPROVE)
    assert await evidence(st, unused) == []
    assert len(await evidence(st, used)) == 1
