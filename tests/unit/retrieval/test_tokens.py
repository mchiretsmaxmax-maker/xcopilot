"""Оценка токенов без сети: консервативная, монотонная."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from xcopilot.retrieval.tokens import estimate_tokens


def test_empty_text_is_zero() -> None:
    assert estimate_tokens("") == 0


def test_nonempty_text_is_at_least_one() -> None:
    assert estimate_tokens("a") >= 1


def test_estimate_is_conservative_for_words_and_cyrillic() -> None:
    # слов не меньше, чем реальных токенов: оценка не ниже числа слов
    assert estimate_tokens("one two three four five") >= 5
    # кириллица токенизируется хуже латиницы: не меньше трети знаков
    text = "купил биткоин на просадке и забыл про него"
    assert estimate_tokens(text) >= len(text) // 3


@given(st.text(max_size=200), st.text(max_size=200))
def test_estimate_is_monotonic_under_concatenation(a: str, b: str) -> None:
    assert estimate_tokens(a + b) >= estimate_tokens(a)
