"""Статистика кодом и жёсткие правила из неё (ТЗ этапа 8, задачи 2–3)."""

from __future__ import annotations

import pytest

from xcopilot.style.stats import compute_style_stats, derive_hard_rules


def test_stats_detect_lowercase_style() -> None:
    texts = ["блобы дешевле"] * 9 + ["Блобы дешевле"]
    stats = compute_style_stats(texts)
    assert stats.n == 10
    assert stats.lowercase_share == pytest.approx(0.9)
    assert derive_hard_rules(stats).casing == "lower"
    mixed = compute_style_stats(["Один", "Два", "три", "Четыре"])
    assert derive_hard_rules(mixed).casing == "any"


def test_stats_detect_no_emoji_and_no_hashtags() -> None:
    texts = ["обычный пост про сети"] * 9 + ["ракета 🚀 #l2"]
    stats = compute_style_stats(texts)
    assert stats.emoji_share == pytest.approx(0.1)
    assert stats.hashtag_share == pytest.approx(0.1)
    rules = derive_hard_rules(stats)
    assert rules.emoji == "none"
    assert rules.hashtags == "none"
    heavy = derive_hard_rules(compute_style_stats(["ура 🎉", "класс 🔥", "мощно 🚀", "тихо"]))
    assert heavy.emoji == "free"
    assert compute_style_stats(["флаг 🇷🇺 и сердце ❤️"]).emoji_share == 1.0


def test_stats_median_length_and_link_share() -> None:
    texts = ["a" * 10, "b" * 20, "c" * 30, "d https://t.co/x " + "e" * 3]
    stats = compute_style_stats(texts)
    assert stats.median_len == 20
    assert stats.link_share == pytest.approx(0.25)
    assert stats.p90_len >= 30
    assert derive_hard_rules(stats).max_len == 280
    long = compute_style_stats(["x" * 900] * 5)
    assert derive_hard_rules(long).max_len >= 900
    empty = compute_style_stats([])
    assert empty.n == 0
    assert derive_hard_rules(empty).casing == "any"
