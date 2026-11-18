"""R4 (ADR 0034, п. 8.2): «👎 Не то» — все 3 варианта мимо → отказ off_topic и новые варианты."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.cards_stand import CardsStand, make_cards_stand
from tests.fakes import FakeTelegram
from tests.integration.bot.test_reply_mode import LINK, POST_ID, POST_TEXT, FakeReader, _push
from tests.integration.brain.test_harvest_job import CFG, FakeReplies, parent, reply
from tests.writer_stand import DISTINCT
from xcopilot.bot import texts
from xcopilot.bot.deps import BotDeps
from xcopilot.brain.harvest import Harvester, harvest_payload

SF = async_sessionmaker[AsyncSession]


@pytest.fixture
async def st(session_factory: SF, telegram: FakeTelegram, deps: BotDeps) -> CardsStand:
    stand = await make_cards_stand(session_factory, telegram, deps)
    reader = FakeReader()
    reader.add(POST_ID, POST_TEXT)
    stand.deps.post_reader = reader
    return stand


async def shown(st: CardsStand) -> tuple[str, list[UUID]]:
    """Режим ответов: ссылка → 3 варианта; вернуть колбэк «Не то» и id черновиков."""
    await st.owner.say("💬 Ответы")
    _push(st)
    await st.owner.say(LINK)
    await st.drain()
    footer = st.owner.last
    assert "👎 Не то" in footer.buttons
    nope = footer.callbacks[footer.buttons.index("👎 Не то")]
    assert nope.startswith("rpl:nope:")
    return nope, await st.finals(UUID(nope.rsplit(":", 1)[1]))


async def generate_payloads(st: CardsStand) -> list[dict[str, Any]]:
    rows = await st.rows(
        "SELECT payload FROM jobs WHERE kind = 'writer.generate' ORDER BY created_at, id"
    )
    return [r[0] if isinstance(r[0], dict) else json.loads(r[0]) for r in rows]


async def rejects(st: CardsStand) -> list[Any]:
    return await st.rows(
        "SELECT draft_id, reason_code, comment FROM reviews WHERE account_id = :a"
        " AND action = 'reject'",
        a=st.account_id,
    )


async def test_nope_rejects_three_and_regenerates(st: CardsStand) -> None:
    nope, ids = await shown(st)
    _push(st)
    await st.owner.press_data("rpl:more")  # прошлые варианты ещё готовятся: режим занят
    await st.owner.press_data(nope)
    assert st.owner.last.text == texts.REPLY_BUSY
    assert [await st.status(i) for i in ids] == ["shown"] * 3 and not await rejects(st)
    await st.drain()

    await st.owner.press_data(nope)
    assert {r.draft_id for r in await rejects(st)} == set(ids)
    assert {(r.reason_code, r.comment) for r in await rejects(st)} == {("off_topic", "reply_nope")}
    first, more, again = await generate_payloads(st)
    assert again["target"] == first["target"] and again["view"] == first["view"]
    assert "не подошли" in again["hint"]
    assert all(body[:25] in again["hint"] for body in DISTINCT)  # первые строки прошлых
    assert first.get("hint") is None and more.get("hint") is None


async def test_nope_then_match_skipped(st: CardsStand) -> None:
    nope, ids = await shown(st)
    await st.owner.press_data(nope)
    reader = FakeReplies()
    at = st.clock.now() - timedelta(hours=2)
    mine = replace(reply(900, 7, at=at, text=DISTINCT[1]), author_handle="acc", author_id="777")
    reader.replies = [mine]
    h = Harvester(st.gs.ws.uow, st.clock, reader, config=CFG, decisions=st.kit.decisions)
    await h.run(st.account_id, harvest_payload(parent(at), LINK))
    assert [await st.status(i) for i in ids] == ["rejected"] * 3
    assert await st.count("reviews", f"account_id = '{st.account_id}'") == 3  # только «Не то»


async def test_nope_twice_idempotent(st: CardsStand) -> None:
    nope, _ = await shown(st)
    await st.owner.press_data(nope)
    await st.owner.press_data(nope)
    assert st.owner.last.text == texts.REPLY_NOPE_DONE
    assert len(await generate_payloads(st)) == 2  # исходная и одна новая
    assert len(await rejects(st)) == 3
