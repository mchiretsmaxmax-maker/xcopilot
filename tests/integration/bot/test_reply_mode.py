"""Режим «💬 Ответы»: ссылка → чтение поста без ключа → 3 варианта через писателя и ворота.

Полный путь на стенде этапа 12: бот → `writer.generate` → `quality.gate` → лёгкие карточки
с кнопками «↩️ Ответить в X» (intent) и «📋 Скопировать».
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from aiogram.types import InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.cards_stand import CardsStand, make_cards_stand
from tests.fakes import FakeTelegram
from tests.fakes.telegram import SentMessage
from tests.integration.bot.conftest import OWNER
from tests.quality_stand import good
from tests.writer_stand import REPLY_BODIES, draft_reply
from xcopilot.bot import texts
from xcopilot.bot.deps import BotDeps
from xcopilot.x.errors import XNotFound, XUnavailable
from xcopilot.x.intents import build_reply_intent
from xcopilot.x.models import PostRef, XPost

SF = async_sessionmaker[AsyncSession]
POST_ID = "1900000000000000001"
LINK = f"https://x.com/vitalik/status/{POST_ID}"
POST_TEXT = "Роллапы скоро умрут, вот увидите: всё уйдёт в один быстрый L1."
IN_MODE = "ReplyFlow:link"
BODIES = REPLY_BODIES  # длины: короткий / средний / длинный (feat/reply-lengths)


class FakeReader:
    """PostFetcher: посты по id; error — бросить при чтении."""

    def __init__(self) -> None:
        self.posts: dict[str, XPost] = {}
        self.calls: list[str] = []
        self.error: Exception | None = None

    def add(self, post_id: str, text: str, handle: str = "vitalik") -> None:
        self.posts[post_id] = XPost(
            id=post_id, text=text, author_handle=handle, created_at=None, source="fxtwitter"
        )

    async def fetch(self, ref: PostRef) -> XPost:
        self.calls.append(ref.id)
        if self.error is not None:
            raise self.error
        if ref.id not in self.posts:
            raise XNotFound(ref.id)
        return self.posts[ref.id]

    get_post = fetch


@pytest.fixture
async def st(session_factory: SF, telegram: FakeTelegram, deps: BotDeps) -> CardsStand:
    return await make_cards_stand(session_factory, telegram, deps)


@pytest.fixture
def reader(st: CardsStand) -> FakeReader:
    r = FakeReader()
    r.add(POST_ID, POST_TEXT)
    st.deps.post_reader = r
    return r


def _push(st: CardsStand) -> None:
    st.gs.ws.writer.push(draft_reply(*BODIES))
    st.gs.judge.push(good(), good(), good())


async def _generate_payloads(st: CardsStand) -> list[dict[str, Any]]:
    rows = await st.rows(
        "SELECT payload FROM jobs WHERE kind = 'writer.generate' ORDER BY created_at, id"
    )
    return [r[0] if isinstance(r[0], dict) else json.loads(r[0]) for r in rows]


def _variants(st: CardsStand) -> list[SentMessage]:
    return [
        m
        for m in st.tg.sent
        if m.chat_id == OWNER and any(b.startswith(("↩️", "📋")) for b in m.buttons)
    ]


def _all_buttons(m: SentMessage) -> list[InlineKeyboardButton]:
    return [b for row in m.reply_markup.inline_keyboard for b in row]


async def test_button_enters_mode(st: CardsStand, reader: FakeReader) -> None:
    sent = await st.owner.say("💬 Ответы")
    assert sent[-1].text == texts.REPLY_MODE_ENTER
    assert await st.owner.state() == IN_MODE


async def test_text_without_link_asks_for_link(st: CardsStand, reader: FakeReader) -> None:
    await st.owner.say("💬 Ответы")
    await st.owner.say("просто мысль без ссылки")
    assert st.owner.last.text == texts.REPLY_NEED_LINK
    assert await st.count("jobs") == 0
    assert await st.owner.state() == IN_MODE


async def test_link_gives_three_reply_variants(st: CardsStand, reader: FakeReader) -> None:
    await st.owner.say("💬 Ответы")
    _push(st)
    await st.owner.say(LINK)
    assert reader.calls == [POST_ID]
    [payload] = await _generate_payloads(st)
    assert payload["task"]["format"] == "reply"
    assert payload["target"]["text"] == POST_TEXT
    assert payload["target"]["url"] == LINK
    assert payload["view"] == {"kind": "reply", "post_id": POST_ID}

    await st.drain()
    assert POST_TEXT in st.gs.ws.prompt()
    variants = _variants(st)
    assert len(variants) == 3
    for body, msg in zip(BODIES, variants, strict=True):
        assert body in msg.text
        intent, copy = _all_buttons(msg)
        assert intent.text == "↩️ Ответить в X"
        assert intent.url == build_reply_intent(body, POST_ID)
        assert copy.copy_text is not None and copy.copy_text.text == body
    assert not st.cards()  # не большая карточка с «✅ Одобрить»
    assert [c.split(":")[1] for c in st.owner.last.callbacks] == ["more", "nope", "exit"]
    statuses = await st.rows("SELECT DISTINCT status FROM drafts")
    assert statuses == [("shown",)]
    assert await st.owner.state() == IN_MODE  # режим держится


async def test_links_one_after_another(st: CardsStand, reader: FakeReader) -> None:
    reader.add("1900000000000000002", "Второй пост про газ")
    await st.owner.say("💬 Ответы")
    _push(st)
    await st.owner.say(LINK)
    await st.drain()
    _push(st)
    await st.owner.say("https://twitter.com/vitalik/status/1900000000000000002")
    await st.drain()
    payloads = await _generate_payloads(st)
    assert [p["target"]["text"] for p in payloads] == [POST_TEXT, "Второй пост про газ"]
    assert len(_variants(st)) == 6


async def test_own_post_is_accepted(st: CardsStand, reader: FakeReader) -> None:
    reader.add("1900000000000000003", "мой собственный пост", handle="acc")
    await st.owner.say("💬 Ответы")
    await st.owner.say("https://x.com/acc/status/1900000000000000003")
    [payload] = await _generate_payloads(st)
    assert payload["target"]["text"] == "мой собственный пост"


async def test_read_failure_then_manual_text(st: CardsStand, reader: FakeReader) -> None:
    reader.error = XUnavailable("сеть")
    await st.owner.say("💬 Ответы")
    await st.owner.say(LINK)
    assert st.owner.last.text == texts.REPLY_READ_FAILED
    assert await st.count("jobs") == 0
    await st.owner.say("Текст поста, который я скопировал руками")
    [payload] = await _generate_payloads(st)
    assert payload["target"]["text"] == "Текст поста, который я скопировал руками"
    assert payload["target"]["url"] == LINK
    assert payload["view"] == {"kind": "reply", "post_id": POST_ID}
    assert await st.owner.state() == IN_MODE


async def test_deleted_post_also_offers_manual_text(st: CardsStand, reader: FakeReader) -> None:
    await st.owner.say("💬 Ответы")
    await st.owner.say("https://x.com/vitalik/status/1900000000000000099")
    assert st.owner.last.text == texts.REPLY_READ_FAILED


async def test_no_reader_offers_manual_text(st: CardsStand) -> None:
    st.deps.post_reader = None
    await st.owner.say("💬 Ответы")
    await st.owner.say(LINK)
    assert st.owner.last.text == texts.REPLY_READ_FAILED


async def test_more_variants_reuses_last_post(st: CardsStand, reader: FakeReader) -> None:
    await st.owner.say("💬 Ответы")
    _push(st)
    await st.owner.say(LINK)
    await st.drain()
    await st.owner.press("🔁 Ещё варианты")
    payloads = await _generate_payloads(st)
    assert len(payloads) == 2 and payloads[1]["target"] == payloads[0]["target"]
    assert reader.calls == [POST_ID]  # второй раз не читаем


async def test_more_without_post(st: CardsStand, reader: FakeReader) -> None:
    await st.owner.say("💬 Ответы")
    await st.owner.press_data("rpl:more")
    assert st.owner.last.text == texts.REPLY_NO_TARGET
    assert await st.count("jobs") == 0


async def test_exit_button(st: CardsStand, reader: FakeReader) -> None:
    await st.owner.say("💬 Ответы")
    await st.owner.press_data("rpl:exit")
    assert st.owner.last.text == texts.REPLY_MODE_EXIT
    assert await st.owner.state() is None


async def test_cancel_exits(st: CardsStand, reader: FakeReader) -> None:
    await st.owner.say("💬 Ответы")
    await st.owner.say("/cancel")
    assert await st.owner.state() is None


async def test_menu_button_exits_and_still_works(st: CardsStand, reader: FakeReader) -> None:
    await st.owner.say("💬 Ответы")
    await st.owner.say("💡 Темы")
    assert await st.owner.state() is None
    assert await st.count("jobs", "kind = 'writer.topics'") == 1
