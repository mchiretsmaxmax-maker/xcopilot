"""Настройки «Мозга» из `config/brain.toml` (ТЗ v3.1, раздел 2.4).

`BrainSettings` — весь файл (этап 9A: веса, пороги, детекторы, извлечение, сид);
`BrainConfig` — срез этапа 7A (лимит фонового переразбора и веса источников).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from xcopilot.errors import ConfigError

BRAIN_TOML = Path(__file__).resolve().parents[3] / "config" / "brain.toml"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Priors(_Strict):
    eff: tuple[float, float]
    fit: tuple[float, float]


class HalfLife(_Strict):
    default: int
    platform: int
    voice: int

    def for_layer(self, layer: str) -> int:
        return {"platform": self.platform, "voice": self.voice}.get(layer, self.default)


class Weights(_Strict):
    source: dict[str, float]
    scope: dict[str, float]
    magnitude: dict[str, float]
    review: dict[str, float]


class Thresholds(_Strict):
    foreign_cap: float
    probation_eff_conf: float
    probation_min_evidence: int
    probation_min_authors: int
    probation_min_contexts: int
    probation_min_own_support: int
    voice_compat_min: float
    weaken_quality: float
    restore_quality: float
    archive_quality: float
    low_fit: float
    max_auto_probation_per_week: int
    probation_idle_days: int
    weakened_idle_days: int
    promote_approvals: int
    promote_own_support: int
    conflict_own_gap: int
    heavy_rewrite_distance: float


class Detectors(_Strict):
    rubric_batch: int
    rubric_max_items: int
    rubric_present: float
    rubric_absent: float


class Extract(_Strict):
    batch_max: int
    min_supporting: int
    min_supporting_contexts: int
    per_context_in_batch: int
    harvest_min_contexts: int
    contrast_share: float
    existing_max: int
    min_new_per_format: int


class Judge(_Strict):
    craft_min: float
    portable_min: float
    layer_override: float
    relation_same: float
    relation_contradicts: float
    nearest_k: int


class Seed(_Strict):
    prior_factor: float


class Analysis(_Strict):
    reanalyze_daily_limit_usd: float


class BrainSettings(_Strict):
    priors: Priors
    decay_half_life_days: HalfLife
    weights: Weights
    thresholds: Thresholds
    detectors: Detectors
    extract: Extract
    judge: Judge
    seed: Seed
    analysis: Analysis


def read_brain_config(path: Path = BRAIN_TOML) -> BrainSettings:
    """Прочитать и проверить файл настроек; битый файл — ConfigError."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        return BrainSettings.model_validate(data)
    except (OSError, tomllib.TOMLDecodeError, ValueError) as exc:
        raise ConfigError(f"{path.name}: {exc}") from exc


@lru_cache(maxsize=1)
def load_brain_config() -> BrainSettings:
    """Настройки по умолчанию (файл из репозитория), читаются один раз."""
    return read_brain_config()


CONFIG_PATH = BRAIN_TOML


@dataclass(frozen=True)
class BrainConfig:
    """Срез настроек для разбора материалов (этап 7A)."""

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
