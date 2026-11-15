"""Настройки «Мозга» (`config/brain.toml`, ТЗ v3.1 раздел 2.4): этап 7A читает раздел analysis."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "brain.toml"


@dataclass(frozen=True)
class BrainConfig:
    reanalyze_daily_limit_micro: int
    source_weights: dict[str, float]

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> BrainConfig:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        usd = float(raw["analysis"]["reanalyze_daily_limit_usd"])
        return cls(
            reanalyze_daily_limit_micro=round(usd * 1_000_000),
            source_weights={k: float(v) for k, v in raw["weights"]["source"].items()},
        )
