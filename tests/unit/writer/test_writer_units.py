"""Юнит-тесты писателя: разбивка треда, сходство, дедуп тем, тексты карточек."""

from __future__ import annotations

from uuid import uuid4

from xcopilot.bot import texts
from xcopilot.writer.formats import FormatCheck, long_post_limit, split_thread, validate_format
from xcopilot.writer.schemas import TopicIdea, WhyItWorked
from xcopilot.writer.similarity import cosine, ensure_distinct, text_similarity
from xcopilot.writer.topics import dedupe_topics
from xcopilot.writer.trend_tail import is_near_copy, why_summary


def test_split_thread_by_paragraphs_sentences_and_words() -> None:
    text = "первый абзац.\n\n" + "длинное предложение " * 30 + "\n\nкороткий. ещё один."
    parts = split_thread(text, max_len=100)
    assert parts[0] == "первый абзац."
    assert all(len(p) <= 100 for p in parts)
    assert parts[-1] == "короткий. ещё один."
    assert split_thread("а" * 250, max_len=100) == ["а" * 100]  # слово длиннее части режется
    assert split_thread("\n\n") == []


def test_validate_format_edge_cases() -> None:
    assert validate_format(FormatCheck(format="story", text="x")) == ["unknown_format:story"]
    assert validate_format(FormatCheck(format="post", text="  ")) == ["empty"]
    assert long_post_limit(280) is None and long_post_limit(None) is None
    assert long_post_limit(4000) == 4000


def test_similarity_helpers() -> None:
    assert text_similarity("", "что-то") == 0.0
    assert text_similarity("Блобы Дешевле", "блобы дешевле") == 1.0
    assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0
    assert ensure_distinct(["один текст", "один текст!", "совсем другое про мосты"]) == [1]
    assert ensure_distinct(["аа бб", "вв гг"]) == []


def test_dedupe_topics_against_recent_and_batch() -> None:
    ideas = [TopicIdea(title=f"тема {i}", angle="угол", rationale="почему") for i in range(3)]
    vecs = [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]]
    assert dedupe_topics(ideas, vecs, []) == [0, 2]
    assert dedupe_topics(ideas, vecs, [[0.0, 1.0]]) == [0]


def test_why_summary_and_texts() -> None:
    why = WhyItWorked(available=True, hook_type="number_first", devices=["list"])
    assert why_summary(why) == "number_first, list"
    assert why_summary(WhyItWorked(available=True, hypotheses=["а", "б"])) == "а; б"
    assert is_near_copy("один и тот же текст", "один и тот же текст.")
    assert texts.trend_why("короткий зачин.") == "Почему зашёл (гипотеза): короткий зачин."
    card = texts.draft_card(2, 3, "", ["раз", "два"], probation="приём")
    assert (
        card.startswith("Вариант 2/3") and "1/2 раз" in card and "🧪 Проверяю приём: приём" in card
    )
    assert texts.writer_warning("unknown") is None
    assert "Формат: Тред" in texts.topic_card("т", None, None, "thread")
    assert str(uuid4())  # keyboard ids are plain strings
