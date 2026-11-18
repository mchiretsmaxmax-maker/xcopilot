"""«💬 Ответы» ставит тихий сбор ответов под постом (ADR 0034, п. 1); выключатель в настройках."""

from __future__ import annotations

import json
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.cards_stand import CardsStand, make_cards_stand
from tests.fakes import FakeTelegram
from tests.integration.bot.test_reply_mode import LINK, POST_ID, POST_TEXT, FakeReader
from xcopilot.bot.deps import BotDeps
from xcopilot.x.models import XMetrics, XPost

SF = async_sessionmaker[AsyncSession]


class FxReader(FakeReader):
    """Читатель с FxEmbed: умеет ответы и цитаты (сам сбор здесь не запускается)."""

    async def fetch_replies(self, post_id: str, limit: int = 30) -> list[XPost]:
        return []

    async def fetch_quotes(self, post_id: str, limit: int = 30) -> list[XPost]:
        return []


@pytest.fixture
async def st(session_factory: SF, telegram: FakeTelegram, deps: BotDeps) -> CardsStand:
    return await make_cards_stand(session_factory, telegram, deps)


def _fx(st: CardsStand, lang: str | None = "en") -> FxReader:
    r = FxReader()
    r.posts[POST_ID] = XPost(
        id=POST_ID,
        text=POST_TEXT,
        author_handle="vitalik",
        created_at=None,
        source="fxtwitter",
        metrics=XMetrics(likes=1200),
        lang=lang,
    )
    st.deps.post_reader = r
    return r


async def _payloads(st: CardsStand, kind: str) -> list[dict[str, Any]]:
    rows = await st.rows("SELECT payload FROM jobs WHERE kind = :k ORDER BY created_at", k=kind)
    return [r[0] if isinstance(r[0], dict) else json.loads(r[0]) for r in rows]


async def test_link_enqueues_harvest_once_and_reply_task_has_post_lang(st: CardsStand) -> None:
    _fx(st)
    await st.owner.say("💬 Ответы")
    await st.owner.say(LINK)
    [harvest] = await _payloads(st, "brain.harvest_replies")
    assert harvest["post_id"] == POST_ID and harvest["url"] == LINK
    assert harvest["parent_metrics"]["likes"] == 1200
    [gen] = await _payloads(st, "writer.generate")
    assert gen["task"]["format"] == "reply" and gen["task"]["lang"] == "en"


async def test_reader_without_replies_no_harvest(st: CardsStand) -> None:
    r = FakeReader()
    r.add(POST_ID, POST_TEXT)
    st.deps.post_reader = r
    await st.owner.say("💬 Ответы")
    await st.owner.say(LINK)
    assert await _payloads(st, "brain.harvest_replies") == []


async def test_settings_toggle_turns_harvest_off(st: CardsStand) -> None:
    _fx(st)
    await st.owner.press_data("set:reply_learning")
    assert "выключена" in st.owner.last.text
    await st.owner.say("💬 Ответы")
    await st.owner.say(LINK)
    assert await _payloads(st, "brain.harvest_replies") == []
    await st.owner.press_data("set:reply_learning")
    assert "включена" in st.owner.last.text
