"""Сигналы «зашло / не зашло» (ТЗ v3.1, раздел 1.3; этап 7A).

Наблюдения только добавляются (`material_signals`); исход считает `brain.outcomes` по
лучшему из них (представление v_material_best_signal).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from xcopilot.db.repos import SourcesRepo
from xcopilot.db.repos.materials import MaterialSignalsRepo
from xcopilot.db.uow import DbSession
from xcopilot.metrics.events import EventType, emit

OWNER_LABELS = ("hit", "normal", "flop")
# Сигнал отбора по умолчанию получает только «нравится» (раздел 1.3): конкурент и
# антипример — не «мне понравилось», свои посты меряются метриками.
PICK_STANCES = frozenset({"inspiration"})


@dataclass(frozen=True)
class Signal:
    origin: str
    views: int | None = None
    likes: int | None = None
    replies: int | None = None
    reposts: int | None = None
    quotes: int | None = None
    bookmarks: int | None = None
    author_followers: int | None = None
    owner_label: str | None = None
    read_confidence: float = 1.0
    post_age_hours: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


_NUM = re.compile(
    r"(?P<num>\d+(?:[.,]\d+)?)\s*"
    r"(?P<suf>млрд|млн|тыс\.?|kk|[kmbкмт](?![a-zа-я]))?\s*"
    r"(?P<label>[a-zа-яё]+)?",
    re.IGNORECASE,
)
_SUFFIX = {
    "k": 1_000,
    "к": 1_000,
    "т": 1_000,
    "тыс": 1_000,
    "тыс.": 1_000,
    "m": 1_000_000,
    "м": 1_000_000,
    "млн": 1_000_000,
    "kk": 1_000_000,
    "b": 1_000_000_000,
    "млрд": 1_000_000_000,
}
_LABELS = (
    ("views", ("view", "impression", "просмотр", "показ")),
    ("likes", ("like", "лайк", "сердеч")),
    ("replies", ("repl", "comment", "ответ", "коммент")),
    ("reposts", ("repost", "retweet", "rt", "репост", "ретвит")),
    ("quotes", ("quote", "цитат")),
    ("bookmarks", ("bookmark", "save", "закладк", "сохран")),
)
# Числа без подписи идут в порядке «просмотры, лайки, ответы, репосты, закладки» (раздел 1.3).
_ORDER = ("views", "likes", "replies", "reposts", "bookmarks")


def _number(raw: str, suffix: str | None) -> int | None:
    digits = raw
    if "," in raw:
        head, tail = raw.split(",", 1)
        # «1,200» без множителя — разделитель тысяч; «1,2 млн» — десятичная запятая
        digits = head + tail if (len(tail) == 3 and not suffix) else f"{head}.{tail}"
    try:
        value = Decimal(digits)
    except InvalidOperation:
        return None
    mult = _SUFFIX.get((suffix or "").lower(), 1)
    return int(value * mult)


def _metric(label: str | None) -> str | None:
    if not label:
        return None
    low = label.lower()
    for metric, stems in _LABELS:
        if any(low.startswith(stem) for stem in stems):
            return metric
    return None


def parse_numbers(text: str) -> Signal | None:
    """Цифры текстом: «12k 340 56», «12.4K views, 340 likes», «1,2 млн» → сигнал manual_numbers.

    Подписанные числа ложатся в свои поля; неподписанные — по порядку просмотры, лайки,
    ответы, репосты, закладки. Нет ни одного числа — None.
    """
    values: dict[str, int] = {}
    unlabelled: list[int] = []
    for m in _NUM.finditer(text or ""):
        value = _number(m.group("num"), m.group("suf"))
        if value is None:
            continue
        metric = _metric(m.group("label"))
        if metric is None:
            unlabelled.append(value)
        elif metric not in values:
            values[metric] = value
    free = [k for k in _ORDER if k not in values]
    for metric, value in zip(free, unlabelled, strict=False):
        values[metric] = value
    if not values:
        return None
    return Signal(
        origin="manual_numbers",
        views=values.get("views"),
        likes=values.get("likes"),
        replies=values.get("replies"),
        reposts=values.get("reposts"),
        quotes=values.get("quotes"),
        bookmarks=values.get("bookmarks"),
        raw={"text": text[:200]},
    )


async def add_signal(
    session: DbSession,
    account_id: UUID,
    source_id: UUID,
    signal: Signal,
    *,
    user_id: UUID | None = None,
    observed_at: datetime | None = None,
) -> UUID:
    """Записать наблюдение материала (только добавление) и событие material.signal_added."""
    src = await SourcesRepo(session, account_id).require(source_id)
    fields: dict[str, Any] = {
        "origin": signal.origin,
        "views": signal.views,
        "likes": signal.likes,
        "replies": signal.replies,
        "reposts": signal.reposts,
        "quotes": signal.quotes,
        "bookmarks": signal.bookmarks,
        "author_followers": signal.author_followers,
        "owner_label": signal.owner_label,
        "read_confidence": Decimal(str(signal.read_confidence)),
        "confirmed_by": user_id,
        "raw": signal.raw,
    }
    if observed_at is not None:
        fields["observed_at"] = observed_at
    age = signal.post_age_hours
    if age is None and src.posted_at is not None and observed_at is not None:
        age = max(0.0, (observed_at - src.posted_at).total_seconds() / 3600)
    if age is not None:
        fields["post_age_hours"] = Decimal(str(round(age, 2)))
    row = await MaterialSignalsRepo(session, account_id).add(source_id=source_id, **fields)
    await emit(
        session,
        account_id=account_id,
        type=EventType.MATERIAL_SIGNAL_ADDED,
        actor="user" if user_id is not None else "system",
        user_id=user_id,
        entity=("source", source_id),
        props={"source_id": str(source_id), "signal_id": str(row.id), "origin": signal.origin},
    )
    return row.id


async def label(
    session: DbSession,
    account_id: UUID,
    source_id: UUID,
    owner_label: str,
    user_id: UUID,
    *,
    observed_at: datetime | None = None,
) -> UUID:
    """Кнопка владельца «залетел / норм / не зашёл» → сигнал owner_label."""
    if owner_label not in OWNER_LABELS:
        raise ValueError(f"неизвестная оценка: {owner_label!r}")
    return await add_signal(
        session,
        account_id,
        source_id,
        Signal(origin="owner_label", owner_label=owner_label),
        user_id=user_id,
        observed_at=observed_at,
    )


async def default_pick(
    session: DbSession,
    account_id: UUID,
    source_id: UUID,
    *,
    observed_at: datetime | None = None,
) -> UUID | None:
    """Слабый сигнал «мне понравилось» для материала «нравится» без других сигналов.

    Идемпотентно: материал с любым сигналом (в том числе прежним отбором) не получает нового.
    """
    src = await SourcesRepo(session, account_id).require(source_id)
    if src.stance not in PICK_STANCES:
        return None
    if await MaterialSignalsRepo(session, account_id).count_for(source_id):
        return None
    return await add_signal(
        session,
        account_id,
        source_id,
        Signal(origin="owner_pick", read_confidence=1.0),
        observed_at=observed_at,
    )
