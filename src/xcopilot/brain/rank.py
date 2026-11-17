"""Отбор и ранжирование знаний под задачу (ТЗ v3.1, 4.1–4.2). Чистые функции, без БД.

Порядок: жёсткий фильтр по области → оценка → слоты (MMR против однообразия) → бюджет.
Формула оценки: rel × quality × fresh × boost, где
rel = 0.45·cos(тема, знание) + 0.55·scope_match.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from xcopilot.brain.render import item_line
from xcopilot.brain.schemas import PackItem, TaskSpec
from xcopilot.retrieval.tokens import estimate_tokens

DIMENSIONS = ("format", "goal", "lang", "niche", "archetype")
UNSCOPED_MATCH = 0.6
MMR_LAMBDA = 0.7
MAX_ITEMS = 15
# Сначала режем приёмы, потом площадку, потом структуру (ТЗ 4.1, шаг 6); дальше — по важности.
TRIM_ORDER = ("device", "platform", "structure", "anti", "closer", "hook", "owner", "pinned")
OUTPUT_ORDER = ("pinned", "owner", "hook", "structure", "device", "closer", "platform", "anti")
_WORD = re.compile(r"\w+")


@dataclass(frozen=True)
class Candidate:
    """Знание, допущенное к выдаче, со всем, что нужно для оценки."""

    item_id: UUID
    version: int
    kind: str
    layer: str
    element: str
    statement: str
    guidance: str
    skeleton: str | None
    status: str
    owner_state: str
    quality: float
    scopes: Mapping[str, frozenset[str]]
    vec: Sequence[float] | None = None
    last_evidence_at: datetime | None = None
    own_support: int = 0
    own_contra: int = 0
    probation_started_at: datetime | None = None
    applied: int = 0


@dataclass(frozen=True)
class Scored:
    item: Candidate
    score: float


def slots_for(fmt: str) -> dict[str, int]:
    """Сколько знаний в каждом слоте; для ответа слоты другие (ТЗ 4.2)."""
    slots = {
        "pinned": 5,
        "owner": 5,
        "hook": 3,
        "structure": 2,
        "device": 3,
        "closer": 1,
        "platform": 2,
        "anti": 3,
    }
    if fmt == "reply":
        slots.update(hook=2, structure=1, platform=1)
    return slots


def classify(item: Candidate) -> str | None:
    """Слот знания; знание на испытании (не закреплённое) в обычные слоты не попадает."""
    if item.owner_state == "pinned":
        return "pinned"
    if item.status != "active":
        return None
    if item.kind == "anti_pattern":
        return "anti"
    if item.kind == "structure":
        return "structure"
    if item.element == "hook":
        return "hook"
    if item.element == "device":
        return "device"
    if item.element == "closer":
        return "closer"
    if item.layer == "platform" or item.element in ("packaging", "timing"):
        return "platform"
    if item.owner_state == "confirmed" and item.element == "general":
        return "owner"  # своё правило владельца без элемента (ручное добавление, онбординг)
    return None


def scope_match(item: Candidate, task: TaskSpec) -> float | None:
    """Среднее по измерениям задачи: 1.0 — точное совпадение, 0.6 — знание без ограничения.

    None — знание ограничено по измерению и значение задачи в его области не входит.
    """
    parts: list[float] = []
    for dim in DIMENSIONS:
        want = getattr(task, dim)
        if want is None:
            continue
        have = item.scopes.get(dim)
        if not have:
            parts.append(UNSCOPED_MATCH)
        elif want.lower() in {v.lower() for v in have}:
            parts.append(1.0)
        else:
            return None
    return sum(parts) / len(parts)


def _cos(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def score(
    item: Candidate, task: TaskSpec, topic_vec: Sequence[float] | None, now: datetime
) -> float:
    match = scope_match(item, task)
    if match is None:
        return 0.0
    cos = max(0.0, _cos(topic_vec, item.vec)) if topic_vec is not None and item.vec else 0.0
    rel = 0.45 * cos + 0.55 * match
    if item.last_evidence_at is None:
        fresh = 0.85
    else:
        days = max(0.0, (now - item.last_evidence_at).total_seconds() / 86400)
        fresh = 0.85 + 0.15 * 0.5 ** (days / 180)
    if item.owner_state == "confirmed":
        boost = 1.15
    elif item.own_support >= 2 and item.own_support > item.own_contra:
        boost = 1.10
    else:
        boost = 1.0
    return rel * item.quality * fresh * boost


def confidence_label(quality: float) -> str:
    return "high" if quality >= 0.60 else "medium" if quality >= 0.45 else "low"


def proven_on_own_posts(item: Candidate) -> bool:
    return item.own_support >= 2 and item.own_support > item.own_contra


def to_pack_item(s: Scored, slot: str, rank: int) -> PackItem:
    c = s.item
    return PackItem.model_validate(
        {
            "item_id": c.item_id,
            "version": c.version,
            "slot": "core"
            if slot in ("hook", "structure", "device", "closer", "platform")
            else "pinned"  # правила владельца показываем вместе с закреплёнными
            if slot == "owner"
            else slot,
            "rank": rank,
            "kind": c.kind,
            "layer": c.layer,
            "element": c.element,
            "guidance": c.guidance,
            "statement": c.statement,
            "skeleton": c.skeleton,
            "confidence": confidence_label(c.quality),
            "proven_on_own_posts": proven_on_own_posts(c),
            "score": round(s.score, 6),
        }
    )


def item_tokens(item: PackItem) -> int:
    """Токены знания в пакете (по строке, которую увидит модель)."""
    return estimate_tokens(item_line(item))


def _mmr(pool: list[Scored], limit: int) -> list[Scored]:
    """Выбрать до limit знаний: полезность минус сходство с уже выбранными."""
    left = sorted(pool, key=lambda s: (-s.score, str(s.item.item_id)))
    chosen: list[Scored] = []
    while left and len(chosen) < limit:
        best = max(
            left,
            key=lambda s: (
                MMR_LAMBDA * s.score
                - (1 - MMR_LAMBDA)
                * max(
                    (_cos(s.item.vec, c.item.vec) for c in chosen if s.item.vec and c.item.vec),
                    default=0.0,
                ),
                -s.score,
                str(s.item.item_id),
            ),
        )
        left.remove(best)
        chosen.append(best)
    return chosen


def fill_slots(
    scored: Sequence[Scored], slots: Mapping[str, int], budget_tokens: int
) -> list[PackItem]:
    """Слоты → MMR внутри слота → не больше 15 знаний → урезание под бюджет."""
    groups: dict[str, list[Scored]] = {name: [] for name in OUTPUT_ORDER}
    for s in scored:
        name = classify(s.item)
        if name is not None and s.score > 0:
            groups[name].append(s)
    picked = {name: _mmr(groups[name], slots.get(name, 0)) for name in OUTPUT_ORDER if groups[name]}
    # общий потолок: лишнее срезаем с конца порядка урезания
    while sum(len(v) for v in picked.values()) > MAX_ITEMS:
        _drop_one(picked)
    while (
        picked
        and sum(item_tokens(to_pack_item(s, n, 1)) for n, v in picked.items() for s in v)
        > budget_tokens
    ):
        if not _drop_one(picked):
            break
    out: list[PackItem] = []
    for name in OUTPUT_ORDER:
        for s in picked.get(name, []):
            out.append(to_pack_item(s, name, len(out) + 1))
    return out


def _drop_one(picked: dict[str, list[Scored]]) -> bool:
    for name in TRIM_ORDER:
        rows = picked.get(name)
        if rows:
            rows.remove(min(rows, key=lambda s: (s.score, str(s.item.item_id))))
            return True
    return False


def pick_probation(candidates: Sequence[Candidate]) -> Candidate | None:
    """Наименее проверенное знание на испытании: меньше применений, затем дольше ждёт."""
    trial = [c for c in candidates if c.status == "probation"]
    if not trial:
        return None
    return min(
        trial,
        key=lambda c: (
            c.applied,
            c.probation_started_at or datetime.max.replace(tzinfo=None),
            str(c.item_id),
        ),
    )


def _grams(text: str, n: int) -> set[tuple[str, ...]]:
    words = _WORD.findall(text.lower())
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


def shares_run(text: str, foreign: Sequence[str], n: int = 6) -> bool:
    """Есть ли в тексте n слов подряд из чужого материала (защита от дословного переноса)."""
    mine = _grams(text, n)
    return bool(mine) and any(mine & _grams(f, n) for f in foreign)
