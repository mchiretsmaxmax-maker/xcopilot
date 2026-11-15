"""Жизненный цикл знания (ТЗ v3.1, раздел 2.4, «Переходы»): правила, применение, откат.

`evaluate` — чистая функция над снимком знания `ItemState`: модель не решает, растёт вес или
падает. `apply` пишет переход в журнал с числами, `revert` откатывает его. Закреплённое
знание (`owner_state = pinned`) автоматически не ослабляется и не архивируется; класс A
автоматически не повышается никогда.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from xcopilot.brain.config import BrainSettings
from xcopilot.brain.context import BrainContext
from xcopilot.db.repos.brain import WORKING, BrainRepo
from xcopilot.db.repos.knowledge import KnowledgeItemsRepo, KnowledgeTransitionsRepo
from xcopilot.metrics.events import EventType, emit


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


Test = Callable[[ItemState, BrainSettings, datetime], str | None]


@dataclass(frozen=True)
class Rule:
    """Правило: из каких статусов, куда; test возвращает пояснение или None."""

    name: str
    from_statuses: frozenset[str]
    to_status: str
    test: Test


def _pinned(s: ItemState) -> bool:
    return s.owner_state == "pinned"


def _older(ts: datetime | None, now: datetime, days: int) -> bool:
    return ts is None or now - ts > timedelta(days=days)


def _auto_probation(s: ItemState, c: BrainSettings, _: datetime) -> str | None:
    t = c.thresholds
    ok = (
        s.risk_class == "B"
        and s.eff_conf >= t.probation_eff_conf
        and s.evidence_n >= t.probation_min_evidence
        and (
            s.independent_authors >= t.probation_min_authors
            or s.own_support >= t.probation_min_own_support
        )
        and s.voice_compat is not None
        and s.voice_compat >= t.voice_compat_min
        and not s.open_conflict
        and s.auto_probations_week < t.max_auto_probation_per_week
    )
    return "автоиспытание: доказательства от разных авторов, совместимо с голосом" if ok else None


def _low_fit(s: ItemState, c: BrainSettings, _: datetime) -> str | None:
    if _pinned(s) or s.fit_conf >= c.thresholds.low_fit:
        return None
    return "не моё: тексты с этим знанием не принимаются"


def _promote_reviews(s: ItemState, c: BrainSettings, _: datetime) -> str | None:
    if s.approvals_clean >= c.thresholds.promote_approvals and s.voice_rejections == 0:
        return "два одобренных черновика без отказа по голосу"
    return None


def _promote_own(s: ItemState, c: BrainSettings, _: datetime) -> str | None:
    if s.own_support >= c.thresholds.promote_own_support and s.own_contra == 0:
        return "подтверждено на своих постах без опровержений"
    return None


def _probation_idle(s: ItemState, c: BrainSettings, now: datetime) -> str | None:
    days = c.thresholds.probation_idle_days
    if _pinned(s) or not _older(s.status_since, now, days):
        return None
    if not _older(s.last_applied_at, now, days):
        return None
    return f"{days} дней без единого применения"


def _weaken(s: ItemState, c: BrainSettings, _: datetime) -> str | None:
    if _pinned(s):
        return None
    if s.quality < c.thresholds.weaken_quality:
        return "качество ниже порога"
    if s.own_contra >= 2 and s.own_contra > s.own_support:
        return "опровержения на своих постах перевешивают"
    return None


def _restore(s: ItemState, c: BrainSettings, _: datetime) -> str | None:
    return "качество восстановилось" if s.quality >= c.thresholds.restore_quality else None


def _archive_low_quality(s: ItemState, c: BrainSettings, _: datetime) -> str | None:
    if _pinned(s) or s.quality >= c.thresholds.archive_quality:
        return None
    return "качество ниже порога архива"


def _weakened_idle(s: ItemState, c: BrainSettings, now: datetime) -> str | None:
    days = c.thresholds.weakened_idle_days
    if _pinned(s) or not _older(s.status_since, now, days):
        return None
    if not _older(s.last_support_at, now, days):
        return None
    return f"{days} дней без новых подтверждений"


def _from(*statuses: str) -> frozenset[str]:
    return frozenset(statuses)


RULES: tuple[Rule, ...] = (
    Rule("auto_probation", _from("candidate"), "probation", _auto_probation),
    Rule("low_fit", _from("probation", "active", "weakened"), "archived", _low_fit),
    Rule("promote_by_reviews", _from("probation"), "active", _promote_reviews),
    Rule("promote_by_own_evidence", _from("probation"), "active", _promote_own),
    Rule("archive_stale", _from("probation"), "archived", _probation_idle),
    Rule("weaken_low_quality", _from("active"), "weakened", _weaken),
    Rule("restore_quality", _from("weakened"), "active", _restore),
    Rule("archive_low_quality", _from("weakened"), "archived", _archive_low_quality),
    Rule("archive_stale", _from("weakened"), "archived", _weakened_idle),
)


def _numbers(s: ItemState, c: BrainSettings) -> dict[str, Any]:
    keys = (
        "eff_conf",
        "fit_conf",
        "quality",
        "evidence_n",
        "independent_authors",
        "own_support",
        "own_contra",
        "voice_compat",
        "approvals_clean",
        "voice_rejections",
        "auto_probations_week",
    )
    state = asdict(s)
    return {k: state[k] for k in keys} | {"thresholds": c.thresholds.model_dump()}


def evaluate(state: ItemState, config: BrainSettings, now: datetime) -> Transition | None:
    """Первое сработавшее правило для знания или None."""
    for rule in RULES:
        if state.status not in rule.from_statuses:
            continue
        reason = rule.test(state, config, now)
        if reason is not None:
            return Transition(
                item_id=state.item_id,
                from_status=state.status,
                to_status=rule.to_status,
                rule=rule.name,
                reason=reason,
                payload=_numbers(state, config),
            )
    return None


async def load_state(ctx: BrainContext, item_id: UUID) -> ItemState:
    """Снимок знания для правил: веса, счётчики, совместимость, конфликты, лимит недели."""
    now = ctx.clock.now()
    row = await BrainRepo(ctx.session, ctx.account_id).lifecycle_state(
        item_id, week_from=now - timedelta(days=7)
    )
    if row is None:
        raise LookupError(f"знание {item_id} не найдено")
    return ItemState(
        item_id=row["id"],
        status=row["status"],
        risk_class=row["risk_class"],
        owner_state=row["owner_state"],
        target_metric=row["target_metric"],
        eff_conf=row["eff_conf"],
        fit_conf=row["fit_conf"],
        quality=row["quality"],
        evidence_n=row["evidence_n"],
        independent_authors=row["independent_authors"],
        own_support=row["own_support"],
        own_contra=row["own_contra"],
        voice_compat=row["voice_compat"],
        open_conflict=row["open_conflict"],
        approvals_clean=row["approvals_clean"],
        voice_rejections=row["voice_rejections"],
        status_since=row["status_since"],
        last_applied_at=row["last_applied_at"],
        last_support_at=row["last_support_at"],
        auto_probations_week=row["auto_probations_week"],
    )


async def apply(
    ctx: BrainContext,
    transition: Transition,
    *,
    user_id: UUID | None = None,
    merged_into: UUID | None = None,
) -> UUID:
    """Записать переход (статус, журнал с числами, событие); вернуть id перехода."""
    actor = "owner" if user_id is not None else "system"
    row = await KnowledgeItemsRepo(ctx.session, ctx.account_id).transition(
        transition.item_id,
        transition.to_status,
        actor=actor,
        rule=transition.rule,
        at=ctx.clock.now(),
        user_id=user_id,
        reason=transition.reason,
        payload=transition.payload,
        merged_into=merged_into,
    )
    await emit(
        ctx.session,
        account_id=ctx.account_id,
        type=EventType.KNOWLEDGE_TRANSITION,
        actor="user" if user_id is not None else "system",
        user_id=user_id,
        entity=("knowledge_item", transition.item_id),
        props={
            "item_id": str(transition.item_id),
            "from_status": row.from_status,
            "to_status": transition.to_status,
            "actor": actor,
            "rule": transition.rule,
        },
    )
    return row.id


async def revert(ctx: BrainContext, transition_id: UUID, user_id: UUID | None) -> None:
    """Откатить переход: знание возвращается в прежний статус, событие knowledge.reverted."""
    t = await KnowledgeTransitionsRepo(ctx.session, ctx.account_id).require(transition_id)
    if t.reverted_at is not None:
        return
    await KnowledgeItemsRepo(ctx.session, ctx.account_id).revert_transition(
        transition_id, user_id=user_id, at=ctx.clock.now()
    )
    await emit(
        ctx.session,
        account_id=ctx.account_id,
        type=EventType.KNOWLEDGE_REVERTED,
        actor="user" if user_id is not None else "system",
        user_id=user_id,
        entity=("knowledge_item", t.item_id),
        props={
            "item_id": str(t.item_id),
            "transition_id": str(transition_id),
            "to_status": t.from_status or t.to_status,
        },
    )


async def run(ctx: BrainContext, now: datetime | None = None) -> list[Transition]:
    """Проверить все знания аккаунта и применить сработавшие переходы."""
    when = now or ctx.clock.now()
    done: list[Transition] = []
    for item_id in await BrainRepo(ctx.session, ctx.account_id).item_ids(WORKING):
        state = await load_state(ctx, item_id)
        transition = evaluate(state, ctx.config, when)
        if transition is not None:
            await apply(ctx, transition)
            done.append(transition)
    return done
