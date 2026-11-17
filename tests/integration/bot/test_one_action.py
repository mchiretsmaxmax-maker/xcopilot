"""fix/one-action: одно действие за раз.

Новое действие (нижняя клавиатура, menu:/more:, команда, «✖️ Отменить») снимает кнопки
прошлого действия, чистит состояние и коротко пишет «Закрыл «…»». Оставшаяся кнопка
закрытого действия отвечает «Это уже закрыто». На каждом шаге ввода есть «✖️ Отменить».
Черновики, чьи кнопки сняты, доступны в «🗂 Очередь».
"""

from __future__ import annotations

import itertools

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.cards_stand import make_cards_stand
from tests.fakes import FakeTelegram
from tests.fakes.telegram import SentMessage, callback_update
from tests.integration.bot.conftest import OWNER, Client, make_onboarded
from xcopilot.bot import keyboards, texts
from xcopilot.bot.deps import BotDeps
from xcopilot.review.callbacks import APPROVE

SF = async_sessionmaker[AsyncSession]
CANCEL = "act:cancel"
_ids = itertools.count(900_000)


def stripped(tg: FakeTelegram) -> set[int]:
    """id сообщений, с которых бот снял клавиатуру."""
    return {
        int(e.message_id or 0)
        for e in tg.markup_edits()
        if e.reply_markup is None and e.chat_id == OWNER
    }


def closed_notes(sent: list[SentMessage]) -> list[str]:
    return [m.text for m in sent if m.text.startswith("Закрыл")]


def with_cb(tg: FakeTelegram, prefix: str) -> SentMessage:
    """Последнее сообщение владельцу с кнопкой, чьи данные начинаются с prefix."""
    for m in reversed(tg.sent):
        if m.chat_id == OWNER and any(c.startswith(prefix) for c in m.callbacks):
            return m
    raise AssertionError(f"нет сообщения с кнопкой {prefix}")


# --- смена раздела ----------------------------------------------------------------------


async def test_switch_section_strips_old_buttons_and_says_closed(
    owner: Client, telegram: FakeTelegram, session_factory: SF
) -> None:
    await make_onboarded(session_factory)
    await owner.say(keyboards.BTN_POST)
    formats = with_cb(telegram, "wfmt:")
    sent = await owner.say(keyboards.BTN_REPLIES)
    assert formats.message_id in stripped(telegram)
    assert closed_notes(sent) == [texts.action_closed(keyboards.BTN_POST)]
    assert sent[-1].text == texts.REPLY_MODE_ENTER  # новое действие началось


async def test_closing_input_step_clears_state(
    owner: Client, telegram: FakeTelegram, session_factory: SF
) -> None:
    await make_onboarded(session_factory)
    await owner.say(keyboards.BTN_POST)
    await owner.press("Пост")
    question = owner.last
    assert await owner.state() is not None
    sent = await owner.say(keyboards.BTN_QUEUE)
    assert await owner.state() is None
    assert closed_notes(sent) == [texts.action_closed(keyboards.BTN_POST)]
    assert question.message_id in stripped(telegram)


async def test_command_also_closes_previous_action(
    owner: Client, telegram: FakeTelegram, session_factory: SF
) -> None:
    await make_onboarded(session_factory)
    await owner.say(keyboards.BTN_POST)
    formats = with_cb(telegram, "wfmt:")
    sent = await owner.say("/status")
    assert formats.message_id in stripped(telegram)
    assert len(closed_notes(sent)) == 1


async def test_nothing_open_means_no_closed_note(owner: Client, session_factory: SF) -> None:
    await make_onboarded(session_factory)
    sent = await owner.say(keyboards.BTN_QUEUE)
    sent += await owner.say(keyboards.BTN_MORE)
    sent += await owner.press_data("more:help")  # навигация «Ещё» снимается молча
    assert closed_notes(sent) == []


async def test_style_screen_closed_by_new_section(
    owner: Client, telegram: FakeTelegram, session_factory: SF
) -> None:
    await make_onboarded(session_factory)
    await owner.press_data("menu:style")
    screen = owner.last
    assert screen.callbacks
    sent = await owner.say(keyboards.BTN_TOPICS)
    assert screen.message_id in stripped(telegram)
    assert len(closed_notes(sent)) == 1


# --- кнопка закрытого действия ------------------------------------------------------------


async def test_stale_button_says_already_closed(
    owner: Client, telegram: FakeTelegram, session_factory: SF
) -> None:
    await make_onboarded(session_factory)
    await owner.say(keyboards.BTN_POST)
    formats = with_cb(telegram, "wfmt:")
    await owner.say(keyboards.BTN_REPLIES)
    edits_before = len(telegram.markup_edits())
    sent = await owner.feed(
        callback_update(next(_ids), OWNER, "wfmt:post", message_id=formats.message_id)
    )
    assert sent == []  # шаг закрытого действия не выполнился
    assert texts.ALREADY_CLOSED in [a.text for a in telegram.callback_answers()]
    assert len(telegram.markup_edits()) > edits_before  # кнопки сняты ещё раз
    assert await owner.state() is not None  # режим «Ответы» не сброшен


# --- «✖️ Отменить» на шагах ввода ---------------------------------------------------------


async def _cancel_works(owner: Client, telegram: FakeTelegram) -> None:
    question = owner.last
    assert CANCEL in question.callbacks
    assert any(b == texts.CANCEL_BUTTON for b in question.buttons)
    sent = await owner.press_data(CANCEL)
    assert await owner.state() is None
    assert texts.CANCELLED in [m.text for m in sent]
    assert question.message_id in stripped(telegram)


async def test_cancel_button_in_write_topic_step(
    owner: Client, telegram: FakeTelegram, session_factory: SF
) -> None:
    await make_onboarded(session_factory)
    await owner.say(keyboards.BTN_POST)
    await owner.press("Пост")
    await _cancel_works(owner, telegram)


async def test_cancel_button_in_replies_link_step(
    owner: Client, telegram: FakeTelegram, session_factory: SF
) -> None:
    await make_onboarded(session_factory)
    await owner.say(keyboards.BTN_REPLIES)
    await _cancel_works(owner, telegram)


async def test_cancel_button_in_settings_steps(
    owner: Client, telegram: FakeTelegram, session_factory: SF
) -> None:
    await make_onboarded(session_factory)
    await owner.say("/settings")
    await owner.press("Часовой пояс")
    await _cancel_works(owner, telegram)
    await owner.say("/settings")
    await owner.press("Мой аккаунт X")
    await _cancel_works(owner, telegram)
    await owner.say("/settings")
    await owner.press("Дневные лимиты")
    await owner.press("Лимит моделей")
    await _cancel_works(owner, telegram)


async def test_cancel_button_in_trend_step(
    owner: Client, telegram: FakeTelegram, session_factory: SF
) -> None:
    await make_onboarded(session_factory)
    await owner.press_data("menu:trend")
    await _cancel_works(owner, telegram)


async def test_cancel_button_on_learn_hint(owner: Client, session_factory: SF) -> None:
    await make_onboarded(session_factory)
    await owner.press_data("menu:learn")
    assert owner.last.text == texts.LEARN_HINT
    assert CANCEL in owner.last.callbacks
    sent = await owner.press_data(CANCEL)
    assert sent and sent[-1].text in (texts.CANCELLED, texts.NOTHING_TO_CANCEL)


async def test_cancel_without_open_action(owner: Client, session_factory: SF) -> None:
    await make_onboarded(session_factory)
    sent = await owner.press_data(CANCEL)
    assert [m.text for m in sent] == [texts.NOTHING_TO_CANCEL]


async def test_cancel_command_still_works(
    owner: Client, telegram: FakeTelegram, session_factory: SF
) -> None:
    await make_onboarded(session_factory)
    await owner.say(keyboards.BTN_POST)
    await owner.press("Пост")
    question = owner.last
    sent = await owner.say("/cancel")
    assert await owner.state() is None
    assert [m.text for m in sent] == [texts.CANCELLED]
    assert question.message_id in stripped(telegram)


# --- черновики после смены раздела ----------------------------------------------------------


async def test_waiting_drafts_reachable_via_queue_after_switch(
    session_factory: SF, telegram: FakeTelegram, deps: BotDeps
) -> None:
    st = await make_cards_stand(session_factory, telegram, deps)
    run_id = await st.generate()
    card = st.cards()[-1]
    sent = await st.owner.say(keyboards.BTN_REPLIES)
    assert card.message_id in stripped(telegram)
    notes = closed_notes(sent)
    assert len(notes) == 1 and texts.DRAFTS_IN_QUEUE in notes[0]

    listing = await st.owner.say(keyboards.BTN_QUEUE)
    assert texts.queue_title(1) in listing[-1].text
    await st.owner.press("Показать")
    assert len(st.cards()) == 2
    fresh = st.cards()[-1]
    assert fresh.message_id != card.message_id

    # карточку снова можно решать
    first = (await st.finals(run_id))[0]
    await st.press(first, APPROVE, message_id=fresh.message_id)
    assert await st.status(first) == "approved"
