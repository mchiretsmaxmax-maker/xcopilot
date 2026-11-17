"""Режим «💬 Ответы»: одна активная генерация на аккаунт и длинный вариант блоком <code>."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.cards_stand import CardsStand, make_cards_stand
from tests.fakes import FakeTelegram
from tests.integration.bot.test_reply_mode import LINK, POST_ID, FakeReader, _push
from xcopilot.bot import texts
from xcopilot.bot.deps import BotDeps
from xcopilot.review.cards import CardDraft, render_reply_variant

SF = async_sessionmaker[AsyncSession]
SECOND = "https://x.com/vitalik/status/1900000000000000002"


@pytest.fixture
async def st(session_factory: SF, telegram: FakeTelegram, deps: BotDeps) -> CardsStand:
    stand = await make_cards_stand(session_factory, telegram, deps)
    reader = FakeReader()
    reader.add(POST_ID, "Пост про роллапы")
    reader.add("1900000000000000002", "Второй пост про газ")
    stand.deps.post_reader = reader
    return stand


async def test_second_link_waits_for_first_generation(st: CardsStand) -> None:
    await st.owner.say("💬 Ответы")
    _push(st)
    await st.owner.say(LINK)
    await st.owner.say(SECOND)
    assert st.owner.last.text == texts.REPLY_BUSY
    await st.owner.press_data("rpl:more")
    assert st.owner.last.text == texts.REPLY_BUSY
    assert await st.count("jobs", "kind = 'writer.generate'") == 1

    await st.drain()  # писатель → ворота → варианты: задачи закончены
    await st.owner.say(SECOND)
    assert await st.count("jobs", "kind = 'writer.generate'") == 2


async def test_busy_check_ignores_other_generations(st: CardsStand) -> None:
    await st.owner.press_data("menu:write")
    await st.owner.press("Пост")
    await st.owner.say("комиссии в l2 падают")
    await st.owner.say("💬 Ответы")
    await st.owner.say(LINK)
    assert await st.count("jobs", "kind = 'writer.generate'") == 2


def _draft(text: str) -> CardDraft:
    return CardDraft(draft_id=uuid4(), variant_no=1, total=3, format="reply", text=text)


def test_long_variant_is_code_block_without_copy_button() -> None:
    text = "а" * 270
    view = render_reply_variant(_draft(text), None, "123")
    assert f"<code>{text}</code>" in view.text
    assert view.keyboard is not None
    assert [b.text for row in view.keyboard.inline_keyboard for b in row] == ["↩️ Ответить в X"]


def test_short_variant_is_plain_text() -> None:
    view = render_reply_variant(_draft("коротко"), None, "123")
    assert "<code>" not in view.text and "коротко" in view.text
