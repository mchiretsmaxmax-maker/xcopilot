"""Исходы материалов, метод 1 (ТЗ v3.1, раздел 1.3; этап 7A).

Свои посты — перцентиль среди своих того же формата за 90 дней (от 20 постов); чужие —
доли от просмотров и стартовые пороги `config/outcomes.toml`. Пост моложе 24 часов исхода
не получает: ставится задача `brain.outcome` на момент, когда ему исполнится сутки.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from xcopilot.db.repos import SourcesRepo
from xcopilot.db.repos.jobs import JobsRepo
from xcopilot.db.repos.materials import BaselineRow, MaterialOutcomesRepo, MaterialSignalsRepo
from xcopilot.db.uow import DbSession
from xcopilot.metrics.events import EventType, emit

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
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        th = raw["thresholds"]
        return cls(
            method_version=int(raw["method_version"]),
            min_own_posts=int(raw["own"]["min_posts"]),
            baseline_days=int(raw["own"]["baseline_days"]),
            defer_hours=int(raw["defer"]["hours"]),
            percentile_cuts=_four(raw["own"]["percentile_cuts"]),
            thresholds=Thresholds(
                engagement=_four(th["engagement"]),
                replies=_four(th["replies"]),
                saves=_four(th["saves"]),
                reach=_four(th["reach"]),
            ),
            reliability={k: float(v) for k, v in raw["reliability"].items()},
        )


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


def _four(values: Any) -> tuple[float, float, float, float]:
    a, b, c, d = (float(v) for v in values)
    return (a, b, c, d)


def _tier(value: float | None, cuts: tuple[float, float, float, float]) -> str:
    """Ступень по четырём границам; нижняя граница ступени включительно."""
    if value is None:
        return "unknown"
    return TIERS[sum(value >= cut for cut in cuts)]


def tier_from_rates(rates: Rates, thresholds: Thresholds) -> Tiers:
    """Ступени по долям от просмотров и стартовым порогам; нет доли — unknown."""
    return Tiers(
        reach=_tier(rates.reach, thresholds.reach),
        engagement=_tier(rates.engagement, thresholds.engagement),
        replies=_tier(rates.replies, thresholds.replies),
        saves=_tier(rates.saves, thresholds.saves),
    )


def _rates(
    views: int | None,
    likes: int | None,
    reposts: int | None,
    quotes: int | None,
    replies: int | None,
    bookmarks: int | None,
    followers: int | None = None,
) -> Rates:
    if not views:
        return Rates()
    eng_parts = [v for v in (likes, reposts, quotes) if v is not None]
    return Rates(
        engagement=sum(eng_parts) / views if eng_parts else None,
        replies=replies / views if replies is not None else None,
        saves=bookmarks / views if bookmarks is not None else None,
        reach=views / followers if followers else None,
    )


def _metric_values(row: BaselineRow) -> dict[str, float | None]:
    r = _rates(row.views, row.likes, row.reposts, row.quotes, row.replies, row.bookmarks)
    return {
        "reach": float(row.views) if row.views is not None else None,
        "engagement": r.engagement,
        "replies": r.replies,
        "saves": r.saves,
    }


async def own_percentiles(
    session: DbSession, account_id: UUID, fmt: str, *, now: datetime, days: int = 90
) -> dict[str, list[float]]:
    """База сравнения своих постов формата за `days` дней: значения по каждой цели.

    Охват — просмотры; вовлечённость, ответы, сохранения — доли от просмотров.
    """
    rows = await MaterialOutcomesRepo(session, account_id).own_baseline(
        fmt, now - timedelta(days=days)
    )
    out: dict[str, list[float]] = {g: [] for g in GOALS}
    for row in rows:
        for goal, value in _metric_values(row).items():
            if value is not None:
                out[goal].append(value)
    return out


def _percentile(value: float, values: list[float]) -> float:
    below = sum(v < value for v in values)
    equal = sum(v == value for v in values)
    return round(min(1.0, (below + 0.5 * equal) / len(values)), 4)


def _same(current: Any, out: Outcome) -> bool:
    return bool(
        current.basis == out.basis
        and current.signal_origin == out.signal_origin
        and (current.tier_reach, current.tier_engagement, current.tier_replies, current.tier_saves)
        == (out.tiers.reach, out.tiers.engagement, out.tiers.replies, out.tiers.saves)
        and _dec(out.rates.engagement) == current.engagement_rate
        and _dec(out.rates.replies) == current.reply_rate
        and _dec(out.rates.saves) == current.save_rate
        and _dec(out.rates.reach) == current.reach_ratio
    )


def _dec(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(round(value, 8)))


async def _defer(session: DbSession, account_id: UUID, source_id: UUID, retry_at: datetime) -> None:
    await JobsRepo(session).enqueue(
        account_id=account_id,
        kind=OUTCOME_KIND,
        payload={"source_id": str(source_id)},
        run_at=retry_at,
        max_attempts=5,
        idempotency_key=f"{OUTCOME_KIND}:{source_id}:{retry_at:%Y%m%d%H%M}",
    )


async def compute_outcome(
    session: DbSession,
    account_id: UUID,
    source_id: UUID,
    *,
    now: datetime,
    config: OutcomeConfig | None = None,
) -> Outcome:
    """Исход материала по лучшему сигналу; новая строка — только если исход изменился.

    Пост моложе `defer.hours` с цифрами исхода не получает: ставится задача brain.outcome.
    """
    cfg = config or OutcomeConfig.load()
    src = await SourcesRepo(session, account_id).require(source_id)
    best = await MaterialSignalsRepo(session, account_id).best(source_id)
    out: Outcome
    if best is None:
        out = Outcome(basis="none", signal_origin="none", reliability=0.0)
    elif best.origin == "owner_label" and best.owner_label:
        tier = best.owner_label
        out = Outcome(
            basis="owner_label",
            signal_origin=best.origin,
            reliability=cfg.reliability[best.origin],
            tiers=Tiers(reach=tier, engagement=tier, replies=tier, saves=tier),
        )
    elif best.origin == "owner_pick":
        out = Outcome(
            basis="owner_pick", signal_origin=best.origin, reliability=cfg.reliability[best.origin]
        )
    elif not best.views:
        out = Outcome(basis="none", signal_origin=best.origin, reliability=0.0)
    else:
        if best.post_age_hours is not None:
            age = float(best.post_age_hours)
        elif src.posted_at is not None:
            age = (now - src.posted_at).total_seconds() / 3600
        else:
            age = float(cfg.defer_hours)
        if age < cfg.defer_hours:
            due = (
                src.posted_at + timedelta(hours=cfg.defer_hours)
                if src.posted_at is not None
                else now + timedelta(hours=cfg.defer_hours - age)
            )
            await _defer(session, account_id, source_id, due)
            return Outcome(
                basis="none",
                signal_origin=best.origin,
                reliability=0.0,
                deferred=True,
                retry_at=due,
            )
        rates = _rates(
            best.views,
            best.likes,
            best.reposts,
            best.quotes,
            best.replies,
            best.bookmarks,
            best.author_followers,
        )
        reliability = cfg.reliability.get(best.origin, 0.0)
        out = Outcome(
            basis="rates",
            signal_origin=best.origin,
            reliability=reliability,
            tiers=tier_from_rates(rates, cfg.thresholds),
            rates=rates,
        )
        if src.stance == "own" and best.origin in ("own_sync", "x_api"):
            base = await own_percentiles(
                session, account_id, src.kind, now=now, days=cfg.baseline_days
            )
            if len(base["reach"]) >= cfg.min_own_posts:
                mine = {
                    "reach": float(best.views),
                    "engagement": rates.engagement,
                    "replies": rates.replies,
                    "saves": rates.saves,
                }
                pct = {
                    g: _percentile(v, base[g]) for g, v in mine.items() if v is not None and base[g]
                }
                out = Outcome(
                    basis="own_baseline",
                    signal_origin=best.origin,
                    reliability=reliability,
                    tiers=Tiers(**{g: _tier(pct.get(g), cfg.percentile_cuts) for g in GOALS}),
                    rates=rates,
                    percentiles=pct,
                )
    return await _store(session, account_id, source_id, out, now=now, cfg=cfg)


async def _store(
    session: DbSession,
    account_id: UUID,
    source_id: UUID,
    out: Outcome,
    *,
    now: datetime,
    cfg: OutcomeConfig,
) -> Outcome:
    repo = MaterialOutcomesRepo(session, account_id)
    current = await repo.current(source_id)
    if current is not None and _same(current, out):
        return Outcome(**{**out.__dict__, "outcome_id": current.id})
    row = await repo.add_current(
        source_id,
        method_version=cfg.method_version,
        basis=out.basis,
        signal_origin=out.signal_origin,
        reliability=Decimal(str(out.reliability)),
        engagement_rate=_dec(out.rates.engagement),
        reply_rate=_dec(out.rates.replies),
        save_rate=_dec(out.rates.saves),
        reach_ratio=_dec(out.rates.reach),
        pct_reach=_dec(out.percentiles.get("reach")),
        pct_engagement=_dec(out.percentiles.get("engagement")),
        pct_replies=_dec(out.percentiles.get("replies")),
        pct_saves=_dec(out.percentiles.get("saves")),
        tier_reach=out.tiers.reach,
        tier_engagement=out.tiers.engagement,
        tier_replies=out.tiers.replies,
        tier_saves=out.tiers.saves,
        computed_at=now,
    )
    await emit(
        session,
        account_id=account_id,
        type=EventType.MATERIAL_OUTCOME_COMPUTED,
        entity=("source", source_id),
        props={
            "source_id": str(source_id),
            "basis": out.basis,
            "signal_origin": out.signal_origin,
            "tier_engagement": out.tiers.engagement,
        },
    )
    return Outcome(**{**out.__dict__, "outcome_id": row.id})
