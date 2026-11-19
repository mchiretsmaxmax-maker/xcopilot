"""Длины вариантов ответа: короткий / средний / длинный (ветка feat/reply-lengths, 19.11.2026)."""

from __future__ import annotations

import pytest

from xcopilot.writer.lengths import (
    LABELS,
    LengthBands,
    assign_slots,
    bounds,
    label,
    length_issue,
    render_lengths,
)

B = LengthBands()


def test_defaults_and_config() -> None:
    assert (B.short_max, B.medium_max, B.long_max, B.tolerance) == (70, 140, 220, 10)
    assert LengthBands.load() == B  # config/thresholds.toml [reply_lengths]


def test_assign_slots() -> None:
    assert assign_slots(3) == ["short", "medium", "long"]
    assert assign_slots(1) == []  # один вариант — длина свободна (в пределах 280)
    assert assign_slots(2) == ["short", "long"]
    assert assign_slots(4) == ["short", "medium", "long", "short"]


def test_bounds() -> None:
    assert bounds("short", B) == (0, 70)
    assert bounds("medium", B) == (70, 140)
    assert bounds("long", B) == (140, 220)


@pytest.mark.parametrize(
    ("slot", "size", "expected"),
    [
        ("short", 5, None),
        ("short", 70, None),
        ("short", 80, None),  # допуск 10
        ("short", 81, ("too_long", 70)),
        ("medium", 60, None),
        ("medium", 59, ("too_short", 70)),
        ("medium", 150, None),
        ("medium", 151, ("too_long", 140)),
        ("long", 130, None),
        ("long", 129, ("too_short", 140)),
        ("long", 230, None),
        ("long", 231, ("too_long", 220)),
    ],
)
def test_length_issue(slot: str, size: int, expected: tuple[str, int] | None) -> None:
    got = length_issue(slot, size, B)
    assert ((got.kind, got.target) if got else None) == expected


def test_labels() -> None:
    assert set(LABELS) == {"short", "medium", "long"}
    assert label("short") == "🩳 коротко"
    assert label(None) == ""
    assert label("bogus") == ""


def test_render_lengths_names_each_variant() -> None:
    text = render_lengths(["short", "medium", "long"], B)
    assert "70" in text and "140" in text and "220" in text
    assert text.index("1") < text.index("2") < text.index("3")
