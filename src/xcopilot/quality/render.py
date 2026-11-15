"""Строка «Проверка: …» для карточки (этап 11, Soft)."""

from __future__ import annotations

from xcopilot.quality.policy import Decision


def gate_line(decision: Decision) -> str:
    raise NotImplementedError
