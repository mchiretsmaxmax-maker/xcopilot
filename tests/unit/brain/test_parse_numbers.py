"""Цифры текстом (этап 7A, задача 5)."""

from __future__ import annotations

from xcopilot.brain.signals import parse_numbers


def test_parse_numbers_variants() -> None:
    plain = parse_numbers("12k 340 56")
    assert plain is not None
    assert (plain.origin, plain.views, plain.likes, plain.replies) == (
        "manual_numbers",
        12_000,
        340,
        56,
    )

    labelled = parse_numbers("12.4K views, 340 likes")
    assert labelled is not None
    assert (labelled.views, labelled.likes, labelled.replies) == (12_400, 340, None)

    russian = parse_numbers("1,2 млн")
    assert russian is not None
    assert russian.views == 1_200_000

    assert parse_numbers("отличный пост") is None
    assert parse_numbers("") is None
