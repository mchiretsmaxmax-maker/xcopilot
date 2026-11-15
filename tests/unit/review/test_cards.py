"""Этап 12: карточка, callback-данные, блок публикации (без БД)."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

from aiogram.types import InlineKeyboardMarkup

from tests.quality_stand import good
from xcopilot.quality.judge import JudgeVerdict
from xcopilot.quality.lint.engine import LintReport
from xcopilot.quality.policy import Decision
from xcopilot.review.callbacks import (
    APPROVE,
    MATCH_YES,
    REGEN,
    REJECT_REASON,
    REJECT_REASONS,
    CardCb,
    card_cb,
    from_short,
    short_id,
)
from xcopilot.review.cards import CardDraft, render_card
from xcopilot.review.publish import PublishDraft, publish_view

TEXT = "Комиссии в L2 падают быстрее, чем растёт активность. Проверил на своих транзакциях."


def decision() -> Decision:
    return Decision(
        outcome="pass",
        lint=LintReport(text=TEXT),
        verdict=JudgeVerdict.from_verdict(good()),
    )


def card(**over: object) -> CardDraft:
    base: dict[str, object] = {
        "draft_id": uuid4(),
        "variant_no": 2,
        "total": 3,
        "format": "post",
        "text": TEXT,
        "topic": "почему комиссии в L2 падают быстрее, чем растёт активность",
    }
    base.update(over)
    return CardDraft(**base)  # type: ignore[arg-type]


def labels(kb: InlineKeyboardMarkup | None) -> list[list[str]]:
    assert kb is not None
    return [[b.text for b in row] for row in kb.inline_keyboard]


def test_card_renders_text_meta_keyboard() -> None:
    view = render_card(card(), decision())
    head = view.text.splitlines()[0]
    assert head == f"📝 Вариант 2/3 · пост · {len(TEXT)}/280"
    assert "Тема: почему комиссии в L2 падают быстрее, чем растёт активность" in view.text
    assert TEXT in view.text
    assert "Проверка: линтер ✅ · судья ✅ голос 2.6/3, AI 2.8/3" in view.text
    assert view.text.count("─────────────") == 2
    assert labels(view.keyboard) == [
        ["◀", "✅ Одобрить", "✏️ Править", "▶"],
        ["🔁 Ещё вариант", "❌ Отклонить", "❓ Почему так"],
    ]
    assert view.before == []
    # без кнопок — устаревшая карточка
    assert render_card(card(), decision(), active=False).keyboard is None


def test_card_escapes_html_special_chars() -> None:
    view = render_card(card(text="a < b && <b>жирный</b>", topic="тег <i>"), None)
    assert "a &lt; b &amp;&amp; &lt;b&gt;жирный&lt;/b&gt;" in view.text
    assert "Тема: тег &lt;i&gt;" in view.text
    assert "<b>жирный</b>" not in view.text


def test_card_split_when_over_4096() -> None:
    long = ("Абзац длинного поста про комиссии и мосты. " * 40 + "\n\n") * 6
    view = render_card(card(text=long.strip()), decision())
    assert len(view.before) >= 2
    assert all(len(m) <= 4096 for m in view.messages)
    joined = "".join(view.before).replace("\n", "").replace(" ", "")
    assert long.replace("\n", "").replace(" ", "") in joined
    assert len(view.text) <= 4096 and view.keyboard is not None
    # спецсимволы не ломают разбиение: каждый кусок — целые сущности
    view2 = render_card(card(text="<&>" * 3000), None)
    assert all(len(m) <= 4096 for m in view2.messages)
    assert all(not m.endswith("&am") and not m.endswith("&l") for m in view2.before)


def test_callback_data_fits_64_bytes() -> None:
    draft = UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
    assert from_short(short_id(draft)) == draft and len(short_id(draft)) == 22
    longest = [
        card_cb(draft, APPROVE),
        card_cb(draft, MATCH_YES, "9" * 20),
        card_cb(draft, REGEN, "angle"),
        *(card_cb(draft, REJECT_REASON, r) for r in REJECT_REASONS),
    ]
    for data in longest:
        assert len(data.encode()) <= 64, data
        assert data.startswith("c1:")
        parsed = CardCb.unpack(data)
        assert from_short(parsed.draft) == draft
    # кнопки карточки — тоже в пределе
    view = render_card(card(draft_id=draft), decision())
    assert view.keyboard is not None
    for row in view.keyboard.inline_keyboard:
        for button in row:
            assert button.callback_data is not None and len(button.callback_data.encode()) <= 64


def test_card_shows_probation_note() -> None:
    view = render_card(card(probation="начни с личной цифры"), decision())
    lines = view.text.splitlines()
    assert lines[0].startswith("📝 Вариант 2/3")
    assert lines[1] == "🧪 Проверяю приём: начни с личной цифры"
    assert "🧪" not in render_card(card(), decision()).text


def test_approve_long_post_shows_copy_mode() -> None:
    short = publish_view(PublishDraft(draft_id=uuid4(), format="post", text=TEXT))
    assert short.mode == "intent" and short.intent_url is not None
    assert parse_qs(urlparse(short.intent_url).query)["text"] == [TEXT]
    long_text = "слово " * 80
    view = publish_view(PublishDraft(draft_id=uuid4(), format="post", text=long_text.strip()))
    assert view.mode == "copy" and view.intent_url is None
    assert view.copy_blocks == [long_text.strip()]


def test_thread_shows_copy_blocks_per_part() -> None:
    parts = ["Первая часть треда.", "Вторая часть — цифры.", "Третья: вывод."]
    view = publish_view(
        PublishDraft(draft_id=uuid4(), format="thread", text="\n\n".join(parts), parts=parts)
    )
    assert view.mode == "copy" and view.intent_url is None
    assert view.copy_blocks == parts


def test_reply_intent_contains_target_id() -> None:
    reply = publish_view(
        PublishDraft(
            draft_id=uuid4(),
            format="reply",
            text="Согласен, но мосты дороже.",
            reply_to_url="https://x.com/someone/status/1234567890123",
        )
    )
    assert reply.mode == "intent" and reply.intent_url is not None
    query = parse_qs(urlparse(reply.intent_url).query)
    assert query["in_reply_to"] == ["1234567890123"]
    quote = publish_view(
        PublishDraft(
            draft_id=uuid4(),
            format="quote",
            text="Вот почему я ушёл с мостов.",
            reply_to_url="https://x.com/someone/status/42",
        )
    )
    assert quote.intent_url is not None
    assert parse_qs(urlparse(quote.intent_url).query)["text"][0].endswith(
        "https://x.com/someone/status/42"
    )
