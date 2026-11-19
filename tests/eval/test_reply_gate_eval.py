"""Eval строгих ворот для ответов (ветка feat/reply-gate-strict), детерминированный, идёт в CI.

Шаблон advice («совет автору в конце») включён только для reply/quote и не даёт ложных: ни на
чистых ответах набора ai_slop_replies, ни на постах владельца из gate_eval_set, проверенных как
ответы. Детектор на format reply ловит не меньше, чем без формата (≥ 80% slop, 0 ложных).
Полный отчёт по живой базе — `scripts/eval_reply_gate.py` (вне CI).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from xcopilot.quality.lint.slop import detect_slop

FIXTURES = Path(__file__).parents[1] / "fixtures" / "quality"


def load(name: str) -> Any:
    return yaml.safe_load((FIXTURES / name).read_text(encoding="utf-8"))


def test_reply_detector_on_real_replies() -> None:
    items = load("ai_slop_replies.yaml")["replies"]
    slop = [r["text"] for r in items if r["slop"]]
    clean = [r["text"] for r in items if not r["slop"]]
    caught = [t for t in slop if detect_slop(t, "en", fmt="reply")]
    false = {
        t: detect_slop(t, "en", fmt="reply") for t in clean if detect_slop(t, "en", fmt="reply")
    }
    assert len(caught) >= 0.8 * len(slop), f"поймано {len(caught)} из {len(slop)}"
    assert false == {}


def test_advice_silent_on_owner_posts_as_replies() -> None:
    owner = load("gate_eval_set.yaml")["owner"]
    hits = {
        t: h
        for t in owner
        if (h := [x for x in detect_slop(t, None, fmt="reply") if x.code == "advice"])
    }
    assert hits == {}
