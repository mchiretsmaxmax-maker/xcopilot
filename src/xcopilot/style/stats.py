"""Статистика стиля кодом и жёсткие правила из неё."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel

from xcopilot.style.passport import HardRules


class StyleStats(BaseModel):
    n: int = 0
    lowercase_share: float = 0.0
    emoji_share: float = 0.0
    hashtag_share: float = 0.0
    link_share: float = 0.0
    median_len: int = 0
    p90_len: int = 0


def compute_style_stats(texts: Sequence[str]) -> StyleStats:
    raise NotImplementedError


def derive_hard_rules(stats: StyleStats) -> HardRules:
    raise NotImplementedError
