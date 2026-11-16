"""Прогон эталонного eval-набора (этап 15): заглушка до кода."""

from __future__ import annotations

from pathlib import Path
from typing import Any

BASELINE_PATH = Path(__file__).resolve().parent / "baseline.json"


def validate_baseline(data: dict[str, Any]) -> list[str]:
    raise NotImplementedError
