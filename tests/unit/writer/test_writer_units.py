"""Юнит-тесты писателя: разбивка треда, сходство, дедуп тем, тексты карточек."""

from __future__ import annotations

from uuid import uuid4

from xcopilot.bot import texts
from xcopilot.writer.formats import FormatCheck, validate_format
from xcopilot.writer.schemas import TopicIdea, WhyItWorked
from xcopilot.writer.similarity import cosine, ensure_distinct, text_similarity
from xcopilot.writer.topics import dedupe_topics
from xcopilot.writer.trend_tail import is_near_copy, why_summary


def test_validate_format_edge_cases() -> None:
    assert validate_format(FormatCheck(format="story", text="x")) == ["unknown_format:story"]
    assert validate_format(FormatCheck(format="post", text="  ")) == ["empty"]


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
