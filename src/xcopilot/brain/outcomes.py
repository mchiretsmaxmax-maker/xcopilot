"""Исходы материалов, метод 1 (ТЗ v3.1, раздел 1.3; этап 7A).

Свои посты — перцентиль среди своих того же формата за 90 дней (от 20 постов); чужие —
доли от просмотров и стартовые пороги `config/outcomes.toml`. Пост моложе 24 часов исхода
не получает: ставится задача `brain.outcome` на момент, когда ему исполнится сутки.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import UUID

from xcopilot.db.uow import DbSession

CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "outcomes.toml"
OUTCOME_KIND = "brain.outcome"
GOALS = ("reach", "engagement", "replies", "saves")
TIERS = ("flop", "below", "normal", "above", "hit")


@dataclass(frozen=True)
class Rates:
    engagement: float | None = None
    replies: float | None = None
    saves: float | None = None
    reach: float | None = None


@dataclass(frozen=True)
class Tiers:
    reach: str = "unknown"
    engagement: str = "unknown"
    replies: str = "unknown"
    saves: str = "unknown"


@dataclass(frozen=True)
class Thresholds:
    """Четыре границы на показатель: flop | below | normal | above | hit."""

    engagement: tuple[float, float, float, float]
    replies: tuple[float, float, float, float]
    saves: tuple[float, float, float, float]
    reach: tuple[float, float, float, float]


@dataclass(frozen=True)
class OutcomeConfig:
    method_version: int
    min_own_posts: int
    baseline_days: int
    defer_hours: int
    percentile_cuts: tuple[float, float, float, float]
    thresholds: Thresholds
    reliability: dict[str, float]

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> OutcomeConfig:
        raise NotImplementedError


@dataclass(frozen=True)
class Outcome:
    basis: str
    signal_origin: str
    reliability: float
    tiers: Tiers = field(default_factory=Tiers)
    rates: Rates = field(default_factory=Rates)
    percentiles: dict[str, float] = field(default_factory=dict)
    deferred: bool = False
    retry_at: datetime | None = None
    outcome_id: UUID | None = None


def tier_from_rates(rates: Rates, thresholds: Thresholds) -> Tiers:
    raise NotImplementedError


async def own_percentiles(
    session: DbSession, account_id: UUID, fmt: str, *, now: datetime, days: int = 90
) -> dict[str, list[float]]:
    raise NotImplementedError


async def compute_outcome(
    session: DbSession,
    account_id: UUID,
    source_id: UUID,
    *,
    now: datetime,
    config: OutcomeConfig | None = None,
) -> Outcome:
    raise NotImplementedError
