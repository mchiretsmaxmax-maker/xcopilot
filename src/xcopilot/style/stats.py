"""Статистика стиля кодом и жёсткие правила из неё."""

from __future__ import annotations

import math
import re
import statistics
from collections.abc import Sequence

from pydantic import BaseModel

from xcopilot.style.passport import HardRules

_EMOJI_RE = re.compile(
    "[\U0001f300-\U0001faff\U0001f1e6-\U0001f1ff"
    "\U00002600-\U000027bf\U00002b00-\U00002bff"
    "\U00002300-\U000023ff\U00002764\U0000fe0f]"
)
_HASHTAG_RE = re.compile(r"(?<!\w)#\w+")
_LINK_RE = re.compile(r"https?://|\bt\.co/")


class StyleStats(BaseModel):
    n: int = 0
    lowercase_share: float = 0.0
    emoji_share: float = 0.0
    hashtag_share: float = 0.0
    link_share: float = 0.0
    median_len: int = 0
    p90_len: int = 0


def compute_style_stats(texts: Sequence[str]) -> StyleStats:
    n = len(texts)
    if n == 0:
        return StyleStats()

    lowercase_count = 0
    emoji_count = 0
    hashtag_count = 0
    link_count = 0
    lengths = []

    for text in texts:
        lengths.append(len(text))

        has_letter = any(ch.isalpha() for ch in text)
        has_upper = any(ch.isupper() for ch in text)
        if has_letter and not has_upper:
            lowercase_count += 1

        if _EMOJI_RE.search(text):
            emoji_count += 1
        if _HASHTAG_RE.search(text):
            hashtag_count += 1
        if _LINK_RE.search(text):
            link_count += 1

    sorted_lengths = sorted(lengths)
    p90_index = min(n - 1, math.ceil(0.9 * n) - 1)

    return StyleStats(
        n=n,
        lowercase_share=lowercase_count / n,
        emoji_share=emoji_count / n,
        hashtag_share=hashtag_count / n,
        link_share=link_count / n,
        median_len=int(statistics.median(lengths)),
        p90_len=sorted_lengths[p90_index],
    )


def _level(share: float) -> str:
    if share <= 0.1 + 1e-9:
        return "none"
    if share <= 0.4 + 1e-9:
        return "rare"
    return "free"


def derive_hard_rules(stats: StyleStats) -> HardRules:
    if stats.n == 0:
        return HardRules()

    casing = "lower" if stats.lowercase_share >= 0.9 else "any"
    max_len = 280 if stats.p90_len <= 280 else min(25000, math.ceil(stats.p90_len * 1.1 / 10) * 10)

    return HardRules(
        casing=casing,
        emoji=_level(stats.emoji_share),
        hashtags=_level(stats.hashtag_share),
        links=_level(stats.link_share),
        max_len=max_len,
        banned_phrases=[],
    )
