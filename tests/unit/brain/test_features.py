"""Признаки кодом (этап 7A, задача 3)."""

from __future__ import annotations

import pytest

from xcopilot.brain.features import compute_features


def test_features_number_in_first_line() -> None:
    with_number = compute_features("i lost $40k in 3 weeks\nhere is why", "post")
    assert with_number["has_number_first_line"] is True
    assert with_number["line_count"] == 2
    assert with_number["first_line_len"] == len("i lost $40k in 3 weeks")
    assert with_number["char_len"] == len("i lost $40k in 3 weeks\nhere is why")

    without = compute_features("nobody talks about this\n3 reasons below", "post")
    assert without["has_number_first_line"] is False


def test_features_question_in_last_line() -> None:
    feats = compute_features("Shipped it.\nTook 3 months.\nWould you do the same?", "post")
    assert feats["has_question"] is True
    assert feats["has_question_last_line"] is True

    middle = compute_features("Why does nobody ship?\nBecause it is hard.", "post")
    assert middle["has_question"] is True
    assert middle["has_question_last_line"] is False

    none = compute_features("I ship every week.", "post")
    assert none["has_question"] is False
    assert none["has_question_last_line"] is False
    assert none["first_person"] is True


def test_features_lowercase_ratio_and_emoji_count() -> None:
    feats = compute_features("gm frens 🔥🔥\nwe are SO back 🚀 #crypto https://x.com/a", "post")
    assert feats["emoji_count"] == 3
    assert feats["hashtag_count"] == 1
    assert feats["has_link"] is True
    letters = [c for c in "gm frens\nwe are SO back crypto" if c.isalpha()]
    expected = sum(c.islower() for c in letters) / len(letters)
    assert feats["lowercase_ratio"] == pytest.approx(expected, abs=0.02)

    upper = compute_features("BIG NEWS TODAY", "post")
    assert upper["lowercase_ratio"] == 0.0
    assert upper["emoji_count"] == 0
    assert compute_features("", "post")["lowercase_ratio"] == 0.0


def test_features_thread_parts() -> None:
    numbered = compute_features("1/ how i grew\n\n2/ first step\n\n3/ second step", "thread")
    assert numbered["thread_parts"] == 3

    explicit = compute_features("a\n\nb", "thread", parts=["a", "b", "c", "d"])
    assert explicit["thread_parts"] == 4

    single = compute_features("just one post", "post", has_media=True)
    assert single["thread_parts"] == 1
    assert single["has_media"] is True
