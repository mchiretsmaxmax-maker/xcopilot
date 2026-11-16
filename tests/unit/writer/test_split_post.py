"""Нарезка длинного поста на части ≤ 280 (требование владельца 16.11.2026): код, не модель."""

from __future__ import annotations

import pytest

from xcopilot.writer.formats import split_post
from xcopilot.x.intents import weighted_len


def test_short_text_is_one_part() -> None:
    assert split_post("Короткий пост.") == ["Короткий пост."]
    assert split_post("  \n ") == []


def test_exactly_280_is_one_part_and_281_is_two() -> None:
    assert split_post("a" * 280) == ["a" * 280]
    parts = split_post("слово " * 46 + "abcde")  # 281 знак
    assert len(parts) == 2 and all(weighted_len(p) <= 280 for p in parts)


def test_paragraph_boundaries_preferred() -> None:
    a, b, c = "а" * 150, "б" * 150, "в" * 100
    assert split_post(f"{a}\n\n{b}\n\n{c}") == [a, f"{b}\n\n{c}"]


def test_greedy_fill_close_to_limit() -> None:
    sentences = [f"Предложение номер {i} про комиссии в сетях." for i in range(30)]
    parts = split_post(" ".join(sentences))
    assert all(weighted_len(p) <= 280 for p in parts)
    assert all(weighted_len(p) > 280 - 45 for p in parts[:-1])  # заполнено почти до края
    assert " ".join(parts) == " ".join(sentences)


def test_long_sentence_split_by_words() -> None:
    text = " ".join(f"слово{i}" for i in range(120))
    parts = split_post(text)
    assert len(parts) > 1
    assert all(weighted_len(p) <= 280 for p in parts)
    assert " ".join(parts).split() == text.split()  # ни одно слово не порвано


def test_url_never_broken() -> None:
    url = "https://example.com/" + "x" * 150
    text = "начало " * 35 + url + " конец " * 10
    parts = split_post(text)
    assert sum(url in p for p in parts) == 1
    assert all(weighted_len(p) <= 280 for p in parts)


def test_emoji_and_cyrillic_latin_weights() -> None:
    text = " ".join(["gas fees 🔥 упали"] * 40)  # эмодзи весит 2
    parts = split_post(text)
    assert all(weighted_len(p) <= 280 for p in parts)
    assert " ".join(parts) == text
    cjk = "字" * 300  # вес 2: ровно 140 в части
    assert [weighted_len(p) for p in split_post(cjk)] == [280, 280, 40]


def test_giant_word_hard_cut() -> None:
    parts = split_post("ы" * 700)
    assert [len(p) for p in parts] == [280, 280, 140]


def test_deterministic() -> None:
    text = "Первый абзац. Второй.\n\n" + "длинное предложение " * 40
    assert split_post(text) == split_post(text)


@pytest.mark.parametrize("limit", [50, 100])
def test_custom_limit(limit: int) -> None:
    parts = split_post("слово " * 60, limit=limit)
    assert all(weighted_len(p) <= limit for p in parts)
