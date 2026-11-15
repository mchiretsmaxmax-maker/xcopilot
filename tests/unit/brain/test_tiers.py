"""Ступени исхода по долям (этап 7A, задача 6) — дополнительный тест к исходным."""

from __future__ import annotations

from xcopilot.brain.outcomes import OutcomeConfig, Rates, tier_from_rates


def test_tier_from_rates_uses_start_thresholds() -> None:
    th = OutcomeConfig.load().thresholds
    tiers = tier_from_rates(Rates(engagement=0.05, replies=0.0001, saves=0.003, reach=None), th)
    assert tiers.engagement == "hit"  # > 4%
    assert tiers.replies == "flop"  # < 0.02%
    assert tiers.saves == "above"  # 0.2–0.5%
    assert tiers.reach == "unknown"  # подписчики неизвестны
    edge = tier_from_rates(Rates(engagement=0.008, replies=0.0015, saves=0.0005, reach=0.5), th)
    assert (edge.engagement, edge.replies, edge.saves, edge.reach) == (
        "normal",
        "above",
        "normal",
        "above",
    )
