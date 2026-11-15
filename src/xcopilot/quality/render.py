"""Строка «Проверка: …» для карточки (этап 11, Soft)."""

from __future__ import annotations

from xcopilot.quality.policy import Decision


def _scale(score: float) -> str:
    return f"{score:.1f}".rstrip("0").rstrip(".") if score == int(score) else f"{score:.1f}"


def gate_line(decision: Decision) -> str:
    """«Проверка: линтер ✅ · судья ✅ голос 2.6/3, AI 2.8/3» и пометки сомнений."""
    lint = "✅" if decision.lint is None or decision.lint.ok else "❌"
    v = decision.verdict
    if v is None or "lint_only" in decision.flags:
        return f"Проверка: линтер {lint} · Проверен только правилами"
    scores = f"голос {_scale(v.voice_match.score)}/3, AI {_scale(v.ai_smell.score)}/3"
    if "judge_unsure" in decision.flags:
        line = f"⚠️ Судья не уверен: {scores}"
    elif "risky" in decision.flags:
        line = f"⚠️ Судья: риск для аккаунта · {scores}"
    else:
        mark = "✅" if decision.outcome == "pass" else "⚠️"
        line = f"Проверка: линтер {lint} · судья {mark} {scores}"
    if "fallback_judge" in decision.flags:
        line += " · Резервный судья"
    if "max_rewrites" in decision.flags:
        line += " · после двух переписываний"
    return line
