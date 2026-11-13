"""Настоящий fastembed: модель качается из сети, поэтому `slow` (вне CI, как live)."""

import math

import pytest

from xcopilot.embeddings.fastembed_impl import FastEmbedder

pytestmark = pytest.mark.slow


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def test_real_embedder_cross_lingual_sanity() -> None:
    emb = FastEmbedder()
    btc_ru, btc_en, borsch = emb.embed(["купил биткоин", "bought btc", "рецепт борща"])
    assert len(btc_ru) == emb.dim == 384
    assert _cos(btc_ru, btc_en) > _cos(btc_ru, borsch)
