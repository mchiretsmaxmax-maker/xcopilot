"""Недельная сводка «чему научилась база» (ТЗ v3.1, 5.2, 9C) и пакетное решение."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING
from uuid import UUID

from xcopilot.brain.context import BrainContext

if TYPE_CHECKING:
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
        return not (self.materials or self.new_items or self.auto_trials or self.class_a)


async def weekly_digest(ctx: BrainContext, week: date) -> Digest:
    raise NotImplementedError


async def accept_all(ctx: BrainContext, user_id: UUID, digest: Digest) -> int:
    raise NotImplementedError


async def revert_auto(ctx: BrainContext, transition_id: UUID, user_id: UUID) -> None:
    raise NotImplementedError


async def handle_weekly_digest(job: Job, *, deps: object, notifier: object) -> int:
    raise NotImplementedError
