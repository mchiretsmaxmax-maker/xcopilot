"""Ссылка на часть треда в «💬 Ответы» и «✍️ Написать → Ответ» даёт весь тред (FxEmbed v2)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.cards_stand import CardsStand, make_cards_stand
from tests.fakes import FakeTelegram
from tests.quality_stand import good
from tests.writer_stand import DISTINCT, draft_reply
from xcopilot.bot.deps import BotDeps
from xcopilot.x.errors import XNotFound
from xcopilot.x.models import PostRef, XPost

SF = async_sessionmaker[AsyncSession]
POST_ID = "1900000000000000004"
LINK = f"https://x.com/naval/status/{POST_ID}"
PART = "4/5 Ignore people playing status games."
THREAD = "1/5 How to Get Rich\n\n2/5 Seek wealth\n\n3/5 Understand\n\n" + PART + "\n\n5/5 End"


class ThreadReader:
    """PostFetcher + fetch_target: fetch — одна часть, fetch_target — тред целиком."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def _post(self, ref: PostRef, text: str) -> XPost:
        if ref.id != POST_ID:
            raise XNotFound(ref.id)
        return XPost(
            id=POST_ID, text=text, author_handle="naval", created_at=None, source="fxtwitter"
        )

    async def fetch(self, ref: PostRef) -> XPost:
        self.calls.append(f"fetch:{ref.id}")
        return self._post(ref, PART)

    get_post = fetch

    async def fetch_target(self, ref: PostRef) -> XPost:
        self.calls.append(f"target:{ref.id}")
        return self._post(ref, THREAD)


@pytest.fixture
async def st(session_factory: SF, telegram: FakeTelegram, deps: BotDeps) -> CardsStand:
    return await make_cards_stand(session_factory, telegram, deps)


async def _payloads(st: CardsStand) -> list[dict[str, Any]]:
    rows = await st.rows(
        "SELECT payload FROM jobs WHERE kind = 'writer.generate' ORDER BY created_at, id"
    )
    return [r[0] if isinstance(r[0], dict) else json.loads(r[0]) for r in rows]


async def test_reply_mode_thread_link_gives_whole_thread(st: CardsStand) -> None:
    reader = ThreadReader()
    st.deps.post_reader = reader
    st.gs.ws.writer.push(draft_reply(*DISTINCT))
    st.gs.judge.push(good(), good(), good())
    await st.owner.say("💬 Ответы")
    await st.owner.say(LINK)
    assert reader.calls == [f"target:{POST_ID}"]
    [payload] = await _payloads(st)
    assert payload["target"]["text"] == THREAD
    assert payload["view"] == {"kind": "reply", "post_id": POST_ID}


async def test_write_reply_thread_link_gives_whole_thread(st: CardsStand) -> None:
    reader = ThreadReader()
    st.deps.post_reader = reader
    await st.owner.press_data("menu:write")
    await st.owner.press("Ответ")
    await st.owner.say(LINK)
    assert reader.calls == [f"target:{POST_ID}"]
    await st.owner.say("-")
    [payload] = await _payloads(st)
    assert payload["target"]["text"] == THREAD
