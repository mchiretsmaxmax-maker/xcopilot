"""Закономерности кодом (этап 14, задача 6; v3.1, задача 15: кандидаты знаний).

Свои посты периода группируются по формату, длине, часу выхода (по поясу аккаунта) и теме;
медиана показов группы сравнивается с общим базовым уровнем периода. Группа считается
закономерностью, если в ней не меньше трёх постов и медиана отличается от базового уровня
на 20% и больше. Модель ничего не считает: числа берутся отсюда.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from statistics import median
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from xcopilot.brain.schemas import (
    FeatureCondition,
    FeatureDetector,
    KnowledgeCandidate,
    NoDetector,
    Scope,
)
from xcopilot.db.repos import AnalyticsRepo
from xcopilot.db.uow import DbSession

MIN_SAMPLE = 10  # меньше постов — предупреждение о малой выборке
MIN_GROUP = 3
LIFT_MIN = 0.2
MAX_CANDIDATES = 12
MAX_SUPPORTING = 25
LENGTH_BUCKETS = (("short", 100), ("medium", 200), ("long", 10**9))
LENGTH_TITLE = {
    "short": "короткие (до 100 знаков)",
    "medium": "средние (100–200)",
    "long": "длинные",
}
FORMATS = ("post", "thread", "reply", "quote")


@dataclass(frozen=True)
class Period:
    start: datetime
    end: datetime


@dataclass(frozen=True)
class Pattern:
    dimension: str  # format | length | hour | topic
    value: str
    n: int
    median: float
    baseline: float
    lift: float  # median / baseline - 1
    post_ids: tuple[str, ...]
    source_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True)
class Patterns:
    sample_size: int
    small_sample: bool
    baseline_median: float | None
    items: tuple[Pattern, ...] = field(default_factory=tuple)
    sources: tuple[UUID, ...] = ()  # все посты закономерностей: номер = метка M<n> кандидатов


def length_bucket(chars: int) -> str:
    return next(name for name, upper in LENGTH_BUCKETS if chars < upper)


async def _tags_and_lengths(
    repo: AnalyticsRepo, source_ids: list[UUID]
) -> tuple[dict[UUID, set[str]], dict[UUID, int]]:
    tags: dict[UUID, set[str]] = defaultdict(set)
    lengths: dict[UUID, int] = defaultdict(int)
    if not source_ids:
        return tags, lengths
    for r in await repo.document_lengths(source_ids):
        lengths[r["source_id"]] += int(r["n"] or 0)
    for r in await repo.topic_tags(source_ids):
        tags[r["source_id"]].add(str(r["tag"]).removeprefix("topic:"))
    return tags, lengths


async def compute_patterns(
    session: DbSession, account_id: UUID, period: Period, *, tz: str = "UTC"
) -> Patterns:
    repo = AnalyticsRepo(session, account_id)
    rows = await repo.own_posts_with_views(period.start, period.end)
    if not rows:
        return Patterns(0, True, None)
    tags, lengths = await _tags_and_lengths(repo, [r["source_id"] for r in rows])
    zone = ZoneInfo(tz)
    overall = float(median(float(r["impressions"]) for r in rows))
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[("format", r["kind"])].append(r)
        groups[("length", length_bucket(lengths[r["source_id"]]))].append(r)
        groups[("hour", str(r["posted_at"].astimezone(zone).hour))].append(r)
        for topic in sorted(tags[r["source_id"]]):
            groups[("topic", topic)].append(r)
    found: list[Pattern] = []
    for (dimension, value), members in groups.items():
        if len(members) < MIN_GROUP or overall <= 0:
            continue
        med = float(median(float(m["impressions"]) for m in members))
        lift = round(med / overall - 1, 4)
        if abs(lift) < LIFT_MIN:
            continue
        found.append(
            Pattern(
                dimension,
                value,
                len(members),
                med,
                overall,
                lift,
                tuple(m["external_id"] for m in members),
                tuple(m["source_id"] for m in members),
            )
        )
    found.sort(key=lambda p: (-abs(p.lift), p.dimension, p.value))
    sources: list[UUID] = []
    for p in found:
        for sid in p.source_ids:
            if sid not in sources:
                sources.append(sid)
    return Patterns(len(rows), len(rows) < MIN_SAMPLE, overall, tuple(found), tuple(sources))


def _statement(p: Pattern) -> str:
    pct = round(p.lift * 100)
    if p.dimension == "format":
        return f"В формате «{p.value}» твои посты набирают на {pct}% больше показов, чем обычно"
    if p.dimension == "length":
        return (
            f"Посты длины «{LENGTH_TITLE[p.value]}» набирают на {pct}% больше показов, чем обычно"
        )
    if p.dimension == "hour":
        return f"Посты около {p.value}:00 по твоему времени набирают на {pct}% больше показов"
    return f"Посты на тему «{p.value}» набирают на {pct}% больше показов, чем обычно"


def _detector(p: Pattern) -> FeatureDetector | NoDetector:
    if p.dimension == "format" and p.value in FORMATS:
        return FeatureDetector(
            type="feature", all=[FeatureCondition(path="format", op="eq", value=p.value)]
        )
    if p.dimension == "length":
        cond = {
            "short": [FeatureCondition(path="features.char_len", op="lte", value=99)],
            "medium": [
                FeatureCondition(path="features.char_len", op="gte", value=100),
                FeatureCondition(path="features.char_len", op="lte", value=199),
            ],
            "long": [FeatureCondition(path="features.char_len", op="gte", value=200)],
        }[p.value]
        return FeatureDetector(type="feature", all=cond)
    return NoDetector(type="none")


_ELEMENT = {"format": "packaging", "length": "structure", "hour": "timing", "topic": "general"}
_LAYER = {"format": "craft", "length": "craft", "hour": "platform", "topic": "niche"}


def to_candidates(patterns: Patterns) -> list[KnowledgeCandidate]:
    """Закономерности с ростом показов → кандидаты знаний (источник `own_metrics`).

    Метки M<n> — номера постов в `patterns.sources`; пачка для приёма строится в том же порядке.
    Падения не становятся знаниями: «что не сработало» остаётся в отчёте.
    """
    order = {sid: i + 1 for i, sid in enumerate(patterns.sources)}
    out: list[KnowledgeCandidate] = []
    for p in patterns.items:
        if p.lift <= 0 or len(out) >= MAX_CANDIDATES:
            continue
        try:
            out.append(
                KnowledgeCandidate(
                    kind="thesis",
                    layer=_LAYER[p.dimension],
                    element=_ELEMENT[p.dimension],
                    statement=_statement(p),
                    guidance="Учитывай это при выборе формата, длины, времени и темы поста",
                    target_metric="reach",
                    detector=_detector(p),
                    scope=Scope(
                        formats=[p.value] if p.dimension == "format" and p.value in FORMATS else [],
                        goals=["reach"],
                    ),
                    supporting=[f"M{order[s]}" for s in p.source_ids[:MAX_SUPPORTING]],
                    rationale=(
                        f"Медиана показов {p.median:.0f} против базового уровня"
                        f" {p.baseline:.0f} на {p.n} постах своего аккаунта"
                    ),
                )
            )
        except ValueError:
            continue  # тема с ссылкой, хэндлом или тикером знанием не становится
    return out
