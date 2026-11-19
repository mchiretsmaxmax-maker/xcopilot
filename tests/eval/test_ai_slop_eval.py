"""Eval детектора AI-шаблонов на 20 настоящих ответах бота (детерминированный, идёт в CI).

Набор — `tests/fixtures/quality/ai_slop_replies.yaml` (живая база, 18.11.2026, до правил
no-ai-slop). Детектор ловит ≥ 80% размеченных slop и не трогает ни одного clean.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from xcopilot.quality.lint.slop import detect_slop

SET = Path(__file__).parents[1] / "fixtures" / "quality" / "ai_slop_replies.yaml"


def replies() -> list[dict[str, Any]]:
    data: dict[str, Any] = yaml.safe_load(SET.read_text(encoding="utf-8"))
    return list(data["replies"])


def test_ai_slop_detector_on_real_replies() -> None:
    items = replies()
    assert len(items) == 20
    slop = [r["text"] for r in items if r["slop"]]
    clean = [r["text"] for r in items if not r["slop"]]
    caught = [t for t in slop if detect_slop(t, "en")]
    false = {t: detect_slop(t, "en") for t in clean if detect_slop(t, "en")}
    assert len(caught) >= 0.8 * len(slop), f"поймано {len(caught)} из {len(slop)}"
    assert false == {}
