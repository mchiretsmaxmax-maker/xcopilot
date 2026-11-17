"""Режим «💬 Ответы»: кнопки варианта, кнопки режима, постоянное меню, payload задач."""

from __future__ import annotations

from uuid import uuid4

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from xcopilot.bot import keyboards
from xcopilot.quality.jobs import gate_payload
from xcopilot.retrieval.context_pack import TaskSpec
from xcopilot.writer.jobs import generate_payload
from xcopilot.writer.schemas import TargetPost
from xcopilot.x.intents import build_reply_intent

VIEW = {"kind": "reply", "post_id": "1900000000000000001"}


def _buttons(markup: InlineKeyboardMarkup | None) -> list[InlineKeyboardButton]:
    assert markup is not None
    return [b for row in markup.inline_keyboard for b in row]


def test_short_variant_has_intent_and_copy() -> None:
    text = "блобы и правда снизили комиссии"
    buttons = _buttons(keyboards.reply_variant(text, "123"))
    assert [b.text for b in buttons] == ["↩️ Ответить в X", "📋 Скопировать"]
    assert buttons[0].url == build_reply_intent(text, "123")
    assert buttons[0].url is not None and "in_reply_to=123" in buttons[0].url
    assert buttons[1].copy_text is not None and buttons[1].copy_text.text == text


def test_variant_over_copy_limit_keeps_only_intent() -> None:
    text = "а" * 270  # ≤ 280 для X, но больше 256 — предела copy_text в Telegram
    buttons = _buttons(keyboards.reply_variant(text, "123"))
    assert [b.text for b in buttons] == ["↩️ Ответить в X"]


def test_variant_over_280_has_no_intent_button() -> None:
    assert build_reply_intent("а" * 300, "123") is None
    assert keyboards.reply_variant("а" * 300, "123") is None  # текст копируется из сообщения


def test_variant_without_post_id_only_copies() -> None:
    buttons = _buttons(keyboards.reply_variant("коротко", None))
    assert [b.text for b in buttons] == ["📋 Скопировать"]


def test_reply_mode_actions() -> None:
    buttons = _buttons(keyboards.reply_mode_actions())
    assert [b.text for b in buttons] == ["🔁 Ещё варианты", "✖️ Выйти из режима"]
    assert [b.callback_data for b in buttons] == ["rpl:more", "rpl:exit"]


def test_persistent_menu_has_replies_button() -> None:
    labels = [b.text for row in keyboards.reply_menu().keyboard for b in row]
    assert keyboards.BTN_REPLIES == "💬 Ответы"
    assert keyboards.BTN_REPLIES in labels and keyboards.BTN_REPLIES in keyboards.REPLY_LABELS


def test_generate_payload_carries_view_only_when_given() -> None:
    task = TaskSpec(format="reply", niche="Крипта", topic="t", lang="ru")
    target = TargetPost(text="пост", url="https://x.com/a/status/1", author="a")
    assert "view" not in generate_payload(task, target=target)
    assert generate_payload(task, target=target, view=VIEW)["view"] == VIEW


def test_gate_payload_carries_view_only_when_given() -> None:
    ids = [uuid4()]
    assert gate_payload(ids) == {"draft_ids": [str(ids[0])]}
    assert gate_payload(ids, view=VIEW)["view"] == VIEW
