"""En dash в диапазонах — типографика, а не AI-шаблон (поправка главного чата 19.11.2026).

Между цифрами («11–12%», «30–40%», «$5–10») и между месяцами/днями недели («November–December»,
«mon–fri») en dash разрешён. Em dash (—) и en dash как тире в тексте ловятся по-прежнему.
"""

from __future__ import annotations

import pytest

from xcopilot.quality.lint.slop import detect_slop


def dash(text: str) -> bool:
    return "em_dash" in {h.code for h in detect_slop(text, "en")}


@pytest.mark.parametrize(
    "text",
    [
        "fees went up 11–12% this week",
        "i'd expect a 30–40% drawdown",
        "a 30 – 40% drawdown is normal",
        "spent $5–10 on gas",
        "slow November–December as usual",
        "markets are open mon–fri",
        "same thing in jan–feb last year",
        "cycles of 3–4 years, 2024–2025 included",
    ],
)
def test_en_dash_ranges_allowed(text: str) -> None:
    assert not dash(text), detect_slop(text, "en")


@pytest.mark.parametrize(
    "text",
    [
        "gas is cheap — nobody cares",
        "gas is cheap – nobody cares",
        "gas–cheap",
        "11% — that's the whole fee",
        "fees went up 11–12% – nobody noticed",
        "November — the slow month",
    ],
)
def test_dashes_in_text_still_caught(text: str) -> None:
    assert dash(text)
