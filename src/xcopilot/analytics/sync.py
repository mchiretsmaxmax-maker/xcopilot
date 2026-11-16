"""Синк своей ленты и снимки метрик поста (ТЗ, этап 14, задачи 1–4, 12–14). Заглушка."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from xcopilot.clock import Clock
from xcopilot.db.uow import UnitOfWork
from xcopilot.jobs.queue import JobQueue
from xcopilot.x.client import XClient

if TYPE_CHECKING:
    from xcopilot.brain.jobs import BrainDeps
    from xcopilot.review.matcher import PublicationMatcher

SYNC_KIND = "analytics.sync"
SNAPSHOT_KIND = "analytics.snapshot"
CHECKPOINT_MINUTES = (60, 24 * 60, 72 * 60, 7 * 24 * 60)
OUTCOME_CHECKPOINT = 24 * 60
OWN_POSTS_LIMIT = 100


class SyncNotifier(Protocol):
    async def notify_owner(self, account_id: UUID, text: str) -> None: ...


@dataclass(frozen=True)
class SyncResult:
    new_posts: int = 0
    matched: int = 0
    skipped: str | None = None  # mode_off | paused | auth


@dataclass(frozen=True)
class SnapshotResult:
    taken: bool
    outcome_basis: str | None = None
    evidence: int = 0


class OwnSync:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        x_for: Callable[[UUID], Awaitable[XClient]],
        queue: JobQueue,
        matcher: PublicationMatcher | None,
        brain: BrainDeps | None,
        notifier: SyncNotifier,
    ) -> None:
        raise NotImplementedError

    async def sync_own_posts(self, account_id: UUID) -> SyncResult:
        raise NotImplementedError

    async def schedule_checkpoints(
        self, account_id: UUID, x_post_id: str, published_at: datetime
    ) -> list[UUID]:
        raise NotImplementedError

    async def take_snapshot(
        self, account_id: UUID, x_post_id: str, checkpoint_min: int | None = None
    ) -> SnapshotResult:
        raise NotImplementedError
