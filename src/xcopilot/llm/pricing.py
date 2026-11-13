"""Цены моделей (config/pricing.toml) и расчёт стоимости вызова в микродолларах."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(frozen=True)
class ModelPrice:
    input: int
    output: int
    cache_read: int = 0
    cache_write: int = 0
    peak_input: int | None = None
    peak_output: int | None = None


@dataclass(frozen=True)
class PricingTable:
    verified: date
    prices: dict[str, dict[str, ModelPrice]]

    @classmethod
    def load(cls, path: Path | None = None) -> PricingTable:
        raise NotImplementedError

    def lookup(self, provider: str, model: str) -> ModelPrice:
        raise NotImplementedError


def is_deepseek_peak(ts: datetime) -> bool:
    raise NotImplementedError


def price(
    provider: str, model: str, usage: Usage, ts: datetime, *, table: PricingTable | None = None
) -> int:
    raise NotImplementedError
