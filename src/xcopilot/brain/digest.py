"""Недельная сводка «чему научилась база» (ТЗ v3.1, 5.2, 9C) и пакетное решение.

Сводка — чистое чтение журнала за семь дней (`[week, week + 7 дней)` по поясу аккаунта):
новые материалы и знания, усилившиеся и ослабшие, взятые на испытание автоматикой (с id
перехода — каждое можно отменить), знания класса A, ждущие «да», открытые противоречия и
число постов с применёнными знаниями (меньше 10 — «мало данных»).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from xcopilot.brain import lifecycle, owner
from xcopilot.brain.context import BrainContext
from xcopilot.db.repos import AccountsRepo
from xcopilot.db.repos.brain_owner import OwnerRepo
from xcopilot.errors import NotFound

if TYPE_CHECKING:
    from xcopilot.brain.jobs import BrainDeps
    from xcopilot.jobs.queue import Job

WEEKLY_DIGEST_KIND = "brain.weekly_digest"
LOW_SAMPLE_POSTS = 10


@dataclass(frozen=True)
class DigestItem:
    item_id: UUID
    statement: str
    transition_id: UUID | None = None


@dataclass(frozen=True)
class DigestConflict:
    link_id: UUID
    first: str
    second: str


@dataclass(frozen=True)
class Digest:
    account_id: UUID
    week_start: date
    materials: int = 0
    new_items: int = 0
    strengthened: int = 0
    weakened: int = 0
    auto_trials: list[DigestItem] = field(default_factory=list)
    class_a: list[DigestItem] = field(default_factory=list)
    conflicts: list[DigestConflict] = field(default_factory=list)
    applied_posts: int = 0
    low_sample: bool = True

    @property
    def is_empty(self) -> bool:
        return not (
            self.materials or self.new_items or self.auto_trials or self.class_a or self.conflicts
        )


class Notifier(Protocol):
    async def notify(
        self, account_id: UUID, text: str, keyboard: Any = None, roles: tuple[str, ...] = ...
    ) -> int: ...


async def _zone(ctx: BrainContext) -> ZoneInfo:
    account = await AccountsRepo(ctx.session).get(ctx.account_id)
    try:
        return ZoneInfo(account.tz if account is not None and account.tz else "UTC")
    except Exception:
        return ZoneInfo("UTC")


def _bounds(week: date, zone: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(week, time.min, tzinfo=zone)
    return start, start + timedelta(days=7)


async def weekly_digest(ctx: BrainContext, week: date) -> Digest:
    """Сводка за неделю, начавшуюся в день `week`."""
    repo = OwnerRepo(ctx.session, ctx.account_id)
    start, end = _bounds(week, await _zone(ctx))
    trials = await repo.transitions_between(start, end, rule="auto_probation")
    applied = await repo.applied_posts_between(start, end)
    return Digest(
        account_id=ctx.account_id,
        week_start=week,
        materials=await repo.count_materials_between(start, end),
        new_items=await repo.count_new_items_between(start, end),
        strengthened=len(await repo.transitions_between(start, end, to_status="active")),
        weakened=len(await repo.transitions_between(start, end, to_status="weakened")),
        auto_trials=[DigestItem(r["item_id"], r["statement"], r["id"]) for r in trials],
        class_a=[DigestItem(r["id"], r["statement"]) for r in await repo.class_a_pending()],
        conflicts=[
            DigestConflict(r["id"], r["first"], r["second"])
            for r in await repo.open_conflict_statements()
        ],
        applied_posts=applied,
        low_sample=applied < LOW_SAMPLE_POSTS,
    )


async def accept_all(ctx: BrainContext, user_id: UUID, digest: Digest) -> int:
    """«Принять всё»: подтверждаются только знания из списка автоиспытаний сводки."""
    return await owner.confirm_batch(ctx, [i.item_id for i in digest.auto_trials], user_id)


async def revert_auto(ctx: BrainContext, transition_id: UUID, user_id: UUID) -> None:
    """Отменить автоматическое повышение («Убрать» в сводке): знание возвращается назад."""
    await owner.require_owner(ctx, user_id)
    row = await OwnerRepo(ctx.session, ctx.account_id).transition(transition_id)
    if row is None:
        raise NotFound(f"переход {transition_id} не найден")
    if row["actor"] != "system":
        raise owner.InvalidItem("откатывается только автоматический переход")
    await lifecycle.revert(ctx, transition_id, user_id)


async def handle_weekly_digest(job: Job, *, deps: BrainDeps, notifier: Notifier) -> int:
    """Задача brain.weekly_digest: прислать владельцу сводку; пустую неделю не шлём.

    payload {"week": "YYYY-MM-DD"} — начало недели; без него — семь дней до сегодняшнего дня
    по поясу аккаунта (понедельник утром — прошедшая неделя). Вернуть 1, если сообщение ушло.
    """
    from xcopilot.bot.brain_view import render_digest

    async with deps.uow_factory() as uow:
        ctx = deps.ctx(uow.session, job.account_id)
        account = await AccountsRepo(uow.session).get(job.account_id)
        if account is None:
            raise NotFound(f"аккаунт {job.account_id} не найден")
        if "week" in job.payload:
            week = date.fromisoformat(str(job.payload["week"]))
        else:
            week = deps.clock.now().astimezone(await _zone(ctx)).date() - timedelta(days=7)
        digest = await weekly_digest(ctx, week)
    if digest.is_empty:
        return 0
    text, keyboard = render_digest(digest)
    await notifier.notify(job.account_id, text, keyboard)
    return 1
