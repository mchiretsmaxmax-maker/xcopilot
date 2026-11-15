"""Настройки «Мозга» из `config/brain.toml` (ТЗ v3.1, раздел 2.4)."""

from __future__ import annotations

import tomllib
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


class BrainConfig(_Strict):
    priors: Priors
    decay_half_life_days: HalfLife
    weights: Weights
    thresholds: Thresholds
    detectors: Detectors
    extract: Extract
    judge: Judge
    seed: Seed


def read_brain_config(path: Path = BRAIN_TOML) -> BrainConfig:
    """Прочитать и проверить файл настроек; битый файл — ConfigError."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        return BrainConfig.model_validate(data)
    except (OSError, tomllib.TOMLDecodeError, ValueError) as exc:
        raise ConfigError(f"{path.name}: {exc}") from exc


@lru_cache(maxsize=1)
def load_brain_config() -> BrainConfig:
    """Настройки по умолчанию (файл из репозитория), читаются один раз."""
    return read_brain_config()
