"""Метка длины варианта в карточке и в простом показе (ветка feat/reply-lengths)."""

from __future__ import annotations

from uuid import uuid4

from xcopilot.bot import texts
from xcopilot.review.cards import CardDraft, render_card


def card(length: str | None) -> CardDraft:
    return CardDraft(
        draft_id=uuid4(), variant_no=1, total=3, format="reply", text="lol same", length=length
    )


def test_card_head_has_label_for_each_length() -> None:
    for slot, label in (("short", "🩳 коротко"), ("medium", "💬 средне"), ("long", "📜 длинно")):
        head = render_card(card(slot), None).text.splitlines()[0]
        assert label in head, head
        assert "Вариант 1/3" in head and "8/280" in head


def test_card_head_unchanged_without_length() -> None:
    head = render_card(card(None), None).text.splitlines()[0]
    assert head == "📝 Вариант 1/3 · ответ · 8/280"


def test_plain_draft_card_label() -> None:
    assert "🩳 коротко" in texts.draft_card(1, 3, "lol same", [], length="short")
    assert texts.draft_card(1, 3, "lol same", []) == "Вариант 1/3\n\nlol same"
