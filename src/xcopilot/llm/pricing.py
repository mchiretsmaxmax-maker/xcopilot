"""Цены моделей (config/pricing.toml) и расчёт стоимости вызова в микродолларах.

Цены хранятся в микродолларах за 1M токенов; стоимость вызова — сумма по видам токенов,
делённая на 1M с округлением вверх (бюджет не занижается). Anthropic: `input_tokens` — без
кэша, чтение и запись кэша считаются отдельно. DeepSeek: в пиковые часы — `peak_*`. Jev: выход 0.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any

from xcopilot.errors import ConfigError
from xcopilot.llm.settings_paths import config_dir

PER = 1_000_000
DEEPSEEK_PEAK_HOURS = frozenset({1, 2, 3, 6, 7, 8, 9})  # UTC, по будням


@dataclass(frozen=True)
class Usage:
    """Токены вызова; `input_tokens` — без чтения и записи кэша."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(frozen=True)
class ModelPrice:
    """Цена модели: микродоллары за 1M токенов."""

    input: int
    output: int
    cache_read: int = 0
    cache_write: int = 0
    peak_input: int | None = None
    peak_output: int | None = None


def _micro(value: Any) -> int:
    return int(Decimal(str(value)) * PER)


def _price(raw: dict[str, Any]) -> ModelPrice:
    known = {"input", "output", "cache_read", "cache_write", "peak_input", "peak_output"}
    if unknown := set(raw) - known:
        raise ConfigError(f"pricing.toml: неизвестные поля {sorted(unknown)}")
    return ModelPrice(
        input=_micro(raw["input"]),
        output=_micro(raw["output"]),
        cache_read=_micro(raw.get("cache_read", 0)),
        cache_write=_micro(raw.get("cache_write", 0)),
        peak_input=_micro(raw["peak_input"]) if "peak_input" in raw else None,
        peak_output=_micro(raw["peak_output"]) if "peak_output" in raw else None,
    )


@dataclass(frozen=True)
class PricingTable:
    verified: date
    prices: dict[str, dict[str, ModelPrice]]

    @classmethod
    def load(cls, path: Path | None = None) -> PricingTable:
        path = path or config_dir() / "pricing.toml"
        data = tomllib.loads(path.read_text())
        verified = data.pop("verified", None)
        if not isinstance(verified, date):
            raise ConfigError("pricing.toml: нужна дата сверки `verified = ГГГГ-ММ-ДД`")
        prices = {
            provider: {model: _price(raw) for model, raw in models.items()}
            for provider, models in data.items()
        }
        return cls(verified=verified, prices=prices)

    def lookup(self, provider: str, model: str) -> ModelPrice:
        """Цена модели или цена провайдера по умолчанию; неизвестный провайдер — KeyError."""
        models = self.prices[provider]
        return models.get(model) or models["default"]


@cache
def _default_table() -> PricingTable:
    return PricingTable.load()


def is_deepseek_peak(ts: datetime) -> bool:
    """Пиковые часы DeepSeek: 01–04 и 06–10 UTC с понедельника по пятницу."""
    utc = ts.astimezone(UTC)
    return utc.weekday() < 5 and utc.hour in DEEPSEEK_PEAK_HOURS


def price(
    provider: str, model: str, usage: Usage, ts: datetime, *, table: PricingTable | None = None
) -> int:
    """Стоимость вызова в микродолларах (округление вверх)."""
    p = (table or _default_table()).lookup(provider, model)
    rate_in, rate_out = p.input, p.output
    if provider == "deepseek" and is_deepseek_peak(ts):
        rate_in = p.peak_input if p.peak_input is not None else rate_in
        rate_out = p.peak_output if p.peak_output is not None else rate_out
    total = (
        usage.input_tokens * rate_in
        + usage.output_tokens * rate_out
        + usage.cache_read_tokens * p.cache_read
        + usage.cache_write_tokens * p.cache_write
    )
    return -(-total // PER)
