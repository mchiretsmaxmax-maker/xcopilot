"""Жизненный цикл знания (ТЗ v3.1, раздел 2.4, «Переходы»): правила, применение, откат.

`evaluate` — чистая функция над снимком знания `ItemState`: модель не решает, растёт вес или
падает. `apply` пишет переход в журнал с числами, `revert` откатывает его.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from xcopilot.brain.config import BrainConfig
from xcopilot.brain.context import BrainContext


@dataclass(frozen=True)
class ItemState:
    """Всё, что нужно правилам переходов, об одном знании."""

    item_id: UUID
    status: str
    risk_class: str
    owner_state: str = "none"
    target_metric: str = "engagement"
    eff_conf: float = 0.2113
    fit_conf: float = 0.4310
    quality: float = 0.2992
    evidence_n: int = 0
    independent_authors: int = 0
    own_support: int = 0
    own_contra: int = 0
    voice_compat: float | None = None
    open_conflict: bool = False
    approvals_clean: int = 0
    voice_rejections: int = 0
    status_since: datetime | None = None
    last_applied_at: datetime | None = None
    last_support_at: datetime | None = None
    auto_probations_week: int = 0


@dataclass(frozen=True)
class Transition:
    item_id: UUID
    from_status: str
    to_status: str
    rule: str
    reason: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Rule:
    name: str
    from_statuses: frozenset[str]
    to_status: str
    test: Callable[[ItemState, BrainConfig, datetime], str | None]


RULES: tuple[Rule, ...] = ()


def evaluate(state: ItemState, config: BrainConfig, now: datetime) -> Transition | None:
    """Первое сработавшее правило для знания или None."""
    raise NotImplementedError


async def load_state(ctx: BrainContext, item_id: UUID) -> ItemState:
    """Снимок знания для правил: веса, счётчики, совместимость, конфликты, лимит недели."""
    raise NotImplementedError


async def apply(ctx: BrainContext, transition: Transition) -> UUID:
    """Записать переход (статус, журнал с числами, событие); вернуть id перехода."""
    raise NotImplementedError


async def revert(ctx: BrainContext, transition_id: UUID, user_id: UUID | None) -> None:
    """Откатить переход: знание возвращается в прежний статус, событие knowledge.reverted."""
    raise NotImplementedError


async def run(ctx: BrainContext, now: datetime | None = None) -> list[Transition]:
    """Проверить все знания аккаунта и применить сработавшие переходы."""
    raise NotImplementedError
