"""R4 (ADR 0034, п. 8): автосверка ответа владельца с 3 вариантами при сборе и перепроверке.

Стенд карточек (писатель + ворота + решения) и настоящий Harvester. Ответ владельца ищется
среди ВСЕХ прочитанных ответов до фильтра мусора (фильтр R3 отбрасывает «self»).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.cards_stand import CardsStand, make_cards_stand
from tests.fakes import FakeTelegram
from tests.integration.bot.test_reply_mode import LINK, POST_ID
from tests.integration.brain.test_harvest_job import CFG, FakeReplies, parent, reply
from tests.writer_stand import REPLY_BODIES
from xcopilot.bot.deps import BotDeps
from xcopilot.brain.harvest import Harvester, harvest_payload
from xcopilot.x.models import XPost

SF = async_sessionmaker[AsyncSession]
OWNER_HANDLE = "acc"


@pytest.fixture
async def st(session_factory: SF, telegram: FakeTelegram, deps: BotDeps) -> CardsStand:
    return await make_cards_stand(session_factory, telegram, deps)


async def variants(st: CardsStand) -> list[UUID]:
    """Три показанных варианта ответа на пост LINK (как после «💬 Ответы»)."""
    run_id = await st.generate(*REPLY_BODIES, fmt="reply", reply_to=LINK)
    ids = await st.finals(run_id)
    assert [await st.status(i) for i in ids] == ["shown"] * 3
    return ids


def harvester(st: CardsStand, reader: FakeReplies) -> Harvester:
    return Harvester(st.gs.ws.uow, st.clock, reader, config=CFG, decisions=st.kit.decisions)


def owner_reply(st: CardsStand, body: str, *, at: datetime, likes: int = 7) -> XPost:
    post = reply(900, likes, at=at, text=body)
    return replace(post, author_handle=OWNER_HANDLE, author_id="777")


async def run_harvest(st: CardsStand, replies: list[XPost]) -> FakeReplies:
    reader = FakeReplies()
    reader.replies = replies
    at = st.clock.now() - timedelta(hours=2)
    await harvester(st, reader).run(st.account_id, harvest_payload(parent(at), LINK))
    return reader


def others(st: CardsStand, n: int = 5) -> list[XPost]:
    at = st.clock.now() - timedelta(hours=2)
    return [reply(i, likes=20 + i, at=at) for i in range(n)]


async def reviews(st: CardsStand) -> list[tuple[UUID, str, str | None, str | None]]:
    rows = await st.rows(
        "SELECT draft_id, action, reason_code, comment FROM reviews WHERE account_id = :a"
        " ORDER BY created_at, id",
        a=st.account_id,
    )
    return [tuple(r) for r in rows]


async def test_owner_reply_matches_variant_unchanged(st: CardsStand) -> None:
    ids = await variants(st)
    at = st.clock.now() - timedelta(hours=2)
    await run_harvest(st, [*others(st), owner_reply(st, REPLY_BODIES[1], at=at)])
    assert [await st.status(i) for i in ids] == ["shown", "approved", "shown"]
    assert await reviews(st) == [(ids[1], "approve", None, "harvest_match")]  # 1 и 3 — без решений


async def test_owner_reply_edited_variant(st: CardsStand) -> None:
    ids = await variants(st)
    at = st.clock.now() - timedelta(hours=2)
    edited = REPLY_BODIES[0].replace("видно", "заметно") + ", и газ тоже стал дешевле"
    await run_harvest(st, [owner_reply(st, edited, at=at), *others(st)])
    assert [await st.status(i) for i in ids] == ["edited", "shown", "shown"]
    [(draft_id, action, _, comment)] = await reviews(st)
    assert (draft_id, action, comment) == (ids[0], "edit", "harvest_match")
    [row] = await st.rows(
        "SELECT text_after, edit_distance FROM reviews WHERE account_id = :a", a=st.account_id
    )
    assert row.text_after == edited and 0 < row.edit_distance < 1


async def test_owner_reply_no_match(st: CardsStand) -> None:
    ids = await variants(st)
    at = st.clock.now() - timedelta(hours=2)
    mine = owner_reply(st, "Совсем другая мысль про мосты и ликвидность в новых сетях", at=at)
    reader = FakeReplies()
    h = harvester(st, reader)
    res = await h.match_owner_reply(st.account_id, POST_ID, mine)
    assert res.status == "no_match"
    assert [await st.status(i) for i in ids] == ["shown"] * 3 and await reviews(st) == []
    [own] = await st.rows(
        "SELECT kind, stance, origin FROM sources WHERE account_id = :a AND external_id = :e",
        a=st.account_id,
        e=mine.id,
    )
    assert tuple(own) == ("reply", "own", "reply_harvest")  # ответ всё равно сохранён


async def test_owner_reply_saved_as_own_reply(st: CardsStand) -> None:
    await variants(st)
    at = st.clock.now() - timedelta(hours=2)
    mine = owner_reply(st, REPLY_BODIES[2], at=at)
    await run_harvest(st, [*others(st, 8), mine])
    [own] = await st.rows(
        "SELECT id, kind, stance, parent_source_id, meta FROM sources"
        " WHERE account_id = :a AND stance = 'own' AND origin = 'reply_harvest'",
        a=st.account_id,
    )
    assert own.kind == "reply" and own.meta["silent"] is True
    [par] = await st.rows(
        "SELECT id FROM sources WHERE account_id = :a AND external_id = :e",
        a=st.account_id,
        e=POST_ID,
    )
    assert own.parent_source_id == par.id
    rest = await st.rows(  # чужие ответы по-прежнему inspiration: own — единственный от сбора
        "SELECT DISTINCT stance FROM sources WHERE account_id = :a AND origin = 'reply_harvest'"
        " AND kind = 'reply' AND id <> :o",
        a=st.account_id,
        o=own.id,
    )
    assert [r.stance for r in rest] == ["inspiration"]


async def test_no_owner_reply_nothing_marked(st: CardsStand) -> None:
    ids = await variants(st)
    await run_harvest(st, others(st))
    assert [await st.status(i) for i in ids] == ["shown"] * 3
    assert await reviews(st) == []
    assert (
        await st.count(
            "sources",
            f"account_id = '{st.account_id}' AND stance = 'own' AND origin = 'reply_harvest'",
        )
        == 0
    )
