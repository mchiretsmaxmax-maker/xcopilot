"""R5 (ADR 0034, п. 8): «✅ Выбрать» под вариантами «💬 Ответов», учёба на выборе.

Нажатие → `Decisions.approve` выбранного, два других сообщения удаляются (или становятся «—»),
у выбранного кнопки «↩️ Ответить в X» и «📋 Скопировать». Автосверка R4 остаётся.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from aiogram.methods import DeleteMessage, EditMessageReplyMarkup, EditMessageText
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.cards_stand import CardsStand, make_cards_stand
from tests.fakes import FakeTelegram
from tests.fakes.telegram import SentMessage, callback_update
from tests.integration.bot.conftest import OWNER
from tests.integration.bot.test_reply_mode import LINK, POST_ID, FakeReader, _push, _variants
from tests.integration.bot.test_reply_pick import (
    others,
    owner_reply,
    reviews,
    run_harvest,
    variants,
)
from tests.writer_stand import DISTINCT
from xcopilot.bot import keyboards
from xcopilot.bot.deps import BotDeps
from xcopilot.x.intents import build_reply_intent

SF = async_sessionmaker[AsyncSession]
DASH = "—"


@pytest.fixture
async def st(session_factory: SF, telegram: FakeTelegram, deps: BotDeps) -> CardsStand:
    return await make_cards_stand(session_factory, telegram, deps)


@pytest.fixture
def reader(st: CardsStand) -> FakeReader:
    r = FakeReader()
    r.add(POST_ID, "Роллапы скоро умрут, вот увидите: всё уйдёт в один быстрый L1.")
    st.deps.post_reader = r
    return r


async def _shown(st: CardsStand) -> list[SentMessage]:
    """Показ из ссылки: три сообщения-варианта (после drain)."""
    await st.owner.say("💬 Ответы")
    _push(st)
    await st.owner.say(LINK)
    await st.drain()
    msgs = [m for m in st.tg.sent if m.chat_id == OWNER and "✅ Выбрать" in m.buttons]
    assert len(msgs) == 3
    return msgs


def _draft_id(m: SentMessage) -> UUID:
    return UUID(m.callbacks[0].rsplit(":", 1)[1])


async def _pick(st: CardsStand, m: SentMessage) -> None:
    await st.owner.feed(callback_update(9000 + m.message_id, OWNER, m.callbacks[0], m.message_id))


def _calls(st: CardsStand, kind: type) -> list[Any]:
    return [c for c in st.tg.calls if isinstance(c, kind)]


async def test_variants_have_pick_button_instead_of_intent(
    st: CardsStand, reader: FakeReader
) -> None:
    msgs = await _shown(st)
    for body, m in zip(DISTINCT, msgs, strict=True):
        assert body in m.text
        assert m.buttons == ["✅ Выбрать"]
        assert m.callbacks[0].startswith("rpl:pick:")
    assert [c.split(":")[1] for c in st.owner.last.callbacks] == ["more", "nope", "exit"]
    assert not _variants(st)  # URL-кнопок «↩️ Ответить» до выбора нет


async def test_pick_approves_and_shows_url(st: CardsStand, reader: FakeReader) -> None:
    msgs = await _shown(st)
    ids = [_draft_id(m) for m in msgs]
    await _pick(st, msgs[1])
    assert [await st.status(i) for i in ids] == ["shown", "approved", "shown"]
    assert [(r[0], r[1]) for r in await reviews(st)] == [(ids[1], "approve")]
    [edit] = [
        c
        for c in _calls(st, EditMessageReplyMarkup)
        if c.message_id == msgs[1].message_id and c.reply_markup is not None
    ]
    intent, copy = [b for row in edit.reply_markup.inline_keyboard for b in row]
    assert intent.text == "↩️ Ответить в X"
    assert intent.url == build_reply_intent(DISTINCT[1], POST_ID)
    assert copy.text == "📋 Скопировать" and copy.copy_text is not None
    assert copy.copy_text.text == DISTINCT[1]


async def test_pick_deletes_the_other_two_messages(st: CardsStand, reader: FakeReader) -> None:
    msgs = await _shown(st)
    await _pick(st, msgs[1])
    deleted = {c.message_id for c in _calls(st, DeleteMessage)}
    assert deleted == {msgs[0].message_id, msgs[2].message_id}


async def test_delete_failure_edits_to_dash(st: CardsStand, reader: FakeReader) -> None:
    msgs = await _shown(st)
    st.tg.fail_deletes = True
    await _pick(st, msgs[0])
    dashed = {c.message_id: c for c in _calls(st, EditMessageText) if c.text == DASH}
    assert set(dashed) == {msgs[1].message_id, msgs[2].message_id}
    assert all(c.reply_markup is None for c in dashed.values())
    assert await st.status(_draft_id(msgs[0])) == "approved"  # выбор учтён, ошибки не мешают


async def test_footer_loses_nope_after_pick(st: CardsStand, reader: FakeReader) -> None:
    msgs = await _shown(st)
    footer = st.owner.last
    await _pick(st, msgs[2])
    [edit] = [c for c in _calls(st, EditMessageReplyMarkup) if c.message_id == footer.message_id]
    left = [b.callback_data for row in edit.reply_markup.inline_keyboard for b in row]
    assert "rpl:more" in left and not any((d or "").startswith("rpl:nope") for d in left)


async def test_pick_twice_idempotent(st: CardsStand, reader: FakeReader) -> None:
    msgs = await _shown(st)
    ids = [_draft_id(m) for m in msgs]
    await _pick(st, msgs[1])
    await _pick(st, msgs[1])
    assert [await st.status(i) for i in ids] == ["shown", "approved", "shown"]
    assert len(await reviews(st)) == 1
    marks = [
        c
        for c in _calls(st, EditMessageReplyMarkup)
        if c.message_id == msgs[1].message_id and c.reply_markup is not None
    ]
    assert marks and all(
        [b.text for row in c.reply_markup.inline_keyboard for b in row]
        == ["↩️ Ответить в X", "📋 Скопировать"]
        for c in marks
    )


async def test_pick_in_older_show_after_more(st: CardsStand, reader: FakeReader) -> None:
    first = await _shown(st)
    _push(st)
    await st.owner.press("🔁 Ещё варианты")
    await st.drain()
    second = [m for m in st.tg.sent if "✅ Выбрать" in m.buttons][3:]
    assert len(second) == 3
    await _pick(st, first[2])
    assert await st.status(_draft_id(first[2])) == "approved"
    assert [await st.status(_draft_id(m)) for m in second] == ["shown"] * 3  # второй показ цел
    deleted = {c.message_id for c in _calls(st, DeleteMessage)}
    assert deleted == {first[0].message_id, first[1].message_id}


async def test_match_after_pick_records_edit(st: CardsStand) -> None:
    ids = await variants(st)
    await st.kit.decisions.approve(st.account_id, ids[1], st.user_id)  # как нажатие «✅ Выбрать»
    at = st.clock.now() - timedelta(hours=2)
    edited = DISTINCT[1].replace("видно", "заметно") + ", и газ тоже стал дешевле"
    await run_harvest(st, [owner_reply(st, edited, at=at), *others(st)])
    assert [await st.status(i) for i in ids] == ["shown", "edited", "shown"]
    assert [(r[0], r[1]) for r in await reviews(st)] == [(ids[1], "approve"), (ids[1], "edit")]


async def test_x_text_of_other_variant_wins_over_pick(st: CardsStand) -> None:
    ids = await variants(st)
    await st.kit.decisions.approve(st.account_id, ids[1], st.user_id)  # выбран вариант 2
    at = st.clock.now() - timedelta(hours=2)
    await run_harvest(st, [*others(st), owner_reply(st, DISTINCT[0], at=at)])  # а ушёл вариант 1
    assert await st.status(ids[0]) == "approved"
    assert (ids[0], "approve", None, "harvest_match") in await reviews(st)


def test_pick_keyboard() -> None:
    markup = keyboards.reply_pick(UUID(int=7))
    [button] = [b for row in markup.inline_keyboard for b in row]
    assert (button.text, button.callback_data) == ("✅ Выбрать", f"rpl:pick:{UUID(int=7)}")
