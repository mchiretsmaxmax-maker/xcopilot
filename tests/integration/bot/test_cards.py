"""Этап 12: карточки в боте — доступ, навигация, одобрение, правка, ещё вариант, отказ,
идемпотентность, срок, показ только после отправки, дневной предел, «Почему так»."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.cards_stand import CardsStand, make_cards_stand
from tests.fakes import FakeTelegram
from tests.integration.bot.conftest import EDITOR, OWNER, STRANGER, Client, make_onboarded
from tests.writer_stand import DISTINCT, draft_reply
from xcopilot.bot import texts
from xcopilot.bot.deps import BotDeps
from xcopilot.review.callbacks import (
    APPROVE,
    EDIT,
    MORE,
    NAV,
    REGEN,
    REJECT,
    REJECT_REASON,
    WHY,
    card_cb,
)
from xcopilot.review.decisions import edit_distance
from xcopilot.review.jobs import CARDS_EXPIRE_KIND
from xcopilot.review.settings import ReviewSettings

SF = async_sessionmaker[AsyncSession]
EDITED = "блобы снизили комиссии роллапов втрое, и это видно по моим транзакциям за неделю"


@pytest.fixture
async def st(session_factory: SF, telegram: FakeTelegram, deps: BotDeps) -> CardsStand:
    return await make_cards_stand(session_factory, telegram, deps)


def url_buttons(markup: object) -> dict[str, str]:
    assert isinstance(markup, InlineKeyboardMarkup)
    return {b.text: b.url for row in markup.inline_keyboard for b in row if b.url}


async def reviews(st: CardsStand, draft_id: UUID) -> list[Any]:
    return await st.rows(
        "SELECT action, reason_code, text_before, text_after, edit_distance, comment"
        " FROM reviews WHERE draft_id = :d ORDER BY created_at, id",
        d=draft_id,
    )


async def test_callback_from_non_member_denied(
    st: CardsStand, session_factory: SF, telegram: FakeTelegram
) -> None:
    run = await st.generate()
    d1 = (await st.finals(run))[0]
    await make_onboarded(session_factory, tg_id=EDITOR, handle="other")
    editor = Client(st.owner.dp, telegram, EDITOR)
    sent = await st.press(d1, APPROVE, who=editor)
    assert sent == []
    answer = telegram.callback_answers()[-1]
    assert answer.text == texts.CARD_DENIED and answer.show_alert
    stranger = Client(st.owner.dp, telegram, STRANGER)
    await st.press(d1, APPROVE, who=stranger)
    assert telegram.callback_answers()[-1].text == texts.DENIED
    assert await st.status(d1) == "shown"
    assert await st.count("reviews") == 0


async def test_variant_navigation_edits_same_message(st: CardsStand) -> None:
    run = await st.generate()
    d1, d2, d3 = await st.finals(run)
    [card] = st.cards()
    assert "Вариант 1/3" in card.text and DISTINCT[0] in card.text
    assert card_cb(d1, APPROVE) in card.callbacks

    sent = await st.press(d1, NAV, "2", message_id=card.message_id)
    assert sent == []
    edit = st.tg.edits()[-1]
    assert edit.message_id == card.message_id and int(edit.chat_id or 0) == OWNER
    assert edit.text is not None
    assert "Вариант 2/3" in edit.text and DISTINCT[1] in edit.text
    assert isinstance(edit.reply_markup, InlineKeyboardMarkup)
    callbacks = [b.callback_data for row in edit.reply_markup.inline_keyboard for b in row]
    assert card_cb(d2, APPROVE) in callbacks

    await st.press(d2, NAV, "3", message_id=card.message_id)
    assert DISTINCT[2] in (st.tg.edits()[-1].text or "")
    assert len(st.cards()) == 1  # новых карточек не было
    rows = await st.rows("SELECT variant_no, message_id FROM review_cards WHERE run_id = :r", r=run)
    assert [(r[0], r[1]) for r in rows] == [(3, card.message_id)]
    assert {await st.status(d) for d in (d1, d2, d3)} == {"shown"}


async def test_approve_sets_status_and_shows_intent_link(st: CardsStand) -> None:
    run = await st.generate()
    d1, d2, _ = await st.finals(run)
    sent = await st.press(d1, APPROVE)
    assert await st.status(d1) == "approved"
    assert await st.status(d2) == "shown"
    [(action, *_)] = await reviews(st, d1)
    assert action == "approve"
    block = sent[-1]
    assert texts.READY_TO_PUBLISH in block.text
    assert block.buttons == ["🚀 Открыть в X", "📋 Скопировать", "✔️ Опубликовал"]
    link = url_buttons(block.reply_markup)["🚀 Открыть в X"]
    assert link.startswith("https://x.com/intent/tweet?text=")


async def test_edit_saves_before_after_and_distance(st: CardsStand) -> None:
    run = await st.generate()
    d1 = (await st.finals(run))[0]
    asked = await st.press(d1, EDIT)
    assert asked[-1].text == texts.EDIT_ASK
    sent = await st.owner.say(EDITED)
    [(action, reason, before, after, distance, _)] = await reviews(st, d1)
    assert action == "edit" and reason is None
    assert before == DISTINCT[0] and after == EDITED
    assert distance == pytest.approx(edit_distance(DISTINCT[0], EDITED))
    assert 0 < distance < 0.5
    assert await st.status(d1) == "edited"
    assert await st.owner.state() is None
    link = url_buttons(sent[-1].reply_markup)["🚀 Открыть в X"]
    assert "%D0%B2%D1%82%D1%80%D0%BE%D0%B5" in link  # «втрое» — в ссылке исправленный текст


async def test_edit_relints_and_allows_override(st: CardsStand) -> None:
    run = await st.generate()
    d1 = (await st.finals(run))[0]
    await st.press(d1, EDIT)
    sent = await st.owner.say(EDITED + ", подписывайтесь")
    text = "\n".join(m.text for m in sent)
    assert "⚠️" in text and "подписывайтесь" in text
    # ошибка линтера — только предупреждение: блок публикации всё равно есть
    assert "🚀 Открыть в X" in url_buttons(sent[-1].reply_markup)
    assert await st.status(d1) == "edited"
    [(action, _, _, after, _, _)] = await reviews(st, d1)
    assert action == "edit" and after.endswith("подписывайтесь")


async def test_regenerate_with_hint_starts_new_run(st: CardsStand) -> None:
    run = await st.generate()
    d1 = (await st.finals(run))[0]
    menu = await st.press(d1, MORE)
    assert menu[-1].buttons == ["Короче", "Жёстче", "Другой заход", "Свой текст"]
    st.gs.ws.writer.push(draft_reply(*DISTINCT))
    from tests.quality_stand import good

    st.gs.judge.push(good(), good(), good())
    await st.press(d1, REGEN, "short")
    await st.drain()
    assert await st.count("generation_runs") == 2
    assert "Короче" in st.gs.ws.prompt()
    [(action, reason, *_)] = await reviews(st, d1)
    assert action == "regenerate" and reason == "short"
    assert len(st.cards()) == 2
    # «Свой текст» — бот ждёт подсказку и ставит генерацию с ней
    await st.press(d1, MORE)
    await st.press(d1, REGEN, "own")
    st.gs.ws.writer.push(draft_reply(*DISTINCT))
    st.gs.judge.push(good(), good(), good())
    await st.owner.say("сделай с цифрой в первой строке")
    await st.drain()
    assert await st.count("generation_runs") == 3
    assert "сделай с цифрой в первой строке" in st.gs.ws.prompt()


async def test_reject_requires_reason_and_stores_it(st: CardsStand) -> None:
    run = await st.generate()
    d1 = (await st.finals(run))[0]
    menu = await st.press(d1, REJECT)
    assert menu[-1].buttons == [
        "Не мой голос",
        "Тема мимо",
        "Звучит как AI",
        "Факт неверный",
        "Другое",
    ]
    assert await st.status(d1) == "shown" and await st.count("reviews") == 0
    with pytest.raises(ValueError):
        await st.kit.decisions.reject(st.account_id, d1, st.user_id, "")
    await st.press(d1, REJECT_REASON, "off_topic")
    assert await st.status(d1) == "rejected"
    [(action, reason, *_)] = await reviews(st, d1)
    assert action == "reject" and reason == "off_topic"
    event = await st.rows(
        "SELECT props ->> 'reason_code' FROM events WHERE type = 'review.rejected'"
        " AND account_id = :a",
        a=st.account_id,
    )
    assert [e[0] for e in event] == ["off_topic"]


async def test_double_tap_is_idempotent(st: CardsStand) -> None:
    run = await st.generate()
    d1 = (await st.finals(run))[0]
    await st.press(d1, APPROVE)
    second = await st.press(d1, APPROVE)
    assert second == []
    assert st.tg.callback_answers()[-1].text == texts.CARD_ALREADY
    await st.press(d1, REJECT_REASON, "not_my_voice")  # поздний отказ после одобрения
    assert await st.status(d1) == "approved"
    assert await st.count("reviews", "draft_id = :d", d=d1) == 1
    assert await st.count("events", "type = 'review.approved' AND account_id = :a",
                          a=st.account_id) == 1  # fmt: skip
    again = await st.kit.decisions.approve(st.account_id, d1, st.user_id)
    assert again.repeated and again.review_id is None


async def test_expired_card_buttons_disabled(st: CardsStand) -> None:
    run = await st.generate()
    d1, d2, d3 = await st.finals(run)
    [card] = st.cards()
    st.advance_hours(49)
    sent = await st.press(d1, APPROVE, message_id=card.message_id)
    assert sent == []
    answer = st.tg.callback_answers()[-1]
    assert answer.text == texts.CARD_EXPIRED and answer.show_alert
    removed = st.tg.markup_edits()[-1]
    assert removed.message_id == card.message_id and removed.reply_markup is None
    assert {await st.status(d) for d in (d1, d2, d3)} == {"expired"}
    assert await st.count("reviews") == 0
    # задача cards.expire снимает кнопки сама, без нажатия
    run2 = await st.generate()
    card2 = st.cards()[-1]
    st.advance_hours(49)
    await st.queue.enqueue(CARDS_EXPIRE_KIND, {}, account_id=st.account_id)
    await st.drain()
    assert {await st.status(d) for d in await st.finals(run2)} == {"expired"}
    assert st.tg.markup_edits()[-1].message_id == card2.message_id
    states = await st.rows("SELECT state FROM review_cards WHERE run_id = :r", r=run2)
    assert [s[0] for s in states] == ["expired"]


async def test_card_status_shown_only_after_successful_send(st: CardsStand) -> None:
    st.tg.fail_sends = 1
    run = await st.generate()
    finals = await st.finals(run)
    assert st.cards() == []
    assert {await st.status(d) for d in finals} == {"judged"}
    assert await st.count("events", "type = 'card.shown'") == 0
    assert await st.count("review_cards", "state = 'shown'") == 0

    assert await st.kit.presenter.show_run(st.account_id, run) == 1
    assert len(st.cards()) == 1
    assert {await st.status(d) for d in finals} == {"shown"}
    assert await st.count("events", "type = 'card.shown'") == 1


async def test_daily_card_limit(session_factory: SF, telegram: FakeTelegram, deps: BotDeps) -> None:
    st = await make_cards_stand(
        session_factory, telegram, deps, settings=ReviewSettings(daily_card_limit=1)
    )
    await st.generate()
    run2 = await st.generate(topic="почему мосты стали скучными")
    assert len(st.cards()) == 1
    assert {await st.status(d) for d in await st.finals(run2)} == {"judged"}
    queued = await st.rows("SELECT state FROM review_cards WHERE run_id = :r", r=run2)
    assert [q[0] for q in queued] == ["queued"]

    listing = await st.owner.say("/queue")
    assert texts.queue_title(1) in listing[-1].text
    assert "почему мосты стали скучными" in listing[-1].text
    await st.owner.press("Показать")
    assert len(st.cards()) == 2
    assert {await st.status(d) for d in await st.finals(run2)} == {"shown"}
    empty = await st.owner.say("/queue")
    assert empty[-1].text == texts.QUEUE_EMPTY


async def test_why_lists_pack_knowledge_with_applied_flags(st: CardsStand) -> None:
    used = await st.gs.ws.knowledge_item("Начинай с вывода, детали потом")
    unused = await st.gs.ws.knowledge_item("Одна мысль на строку")
    st.gs.ws.knowledge.items = [used, unused]
    st.gs.detector.applied = {used.item_id}
    run = await st.generate()
    d1 = (await st.finals(run))[0]
    sent = await st.press(d1, WHY)
    text = sent[-1].text
    assert texts.WHY_CARD_TITLE in text
    assert "✅ Начинай с вывода, детали потом" in text
    assert "➖ Одна мысль на строку" in text
