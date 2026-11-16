"""Сборка сервисов этапа 14 одним объектом (для BotDeps, JobDeps и тестовых стендов)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from xcopilot.analytics.analyst import Analyst
from xcopilot.analytics.reports import Reports
from xcopilot.analytics.sync import OwnSync, SyncNotifier
from xcopilot.clock import Clock
from xcopilot.db.uow import UnitOfWork
from xcopilot.jobs.queue import JobQueue
from xcopilot.llm.gateway import LLMGateway
from xcopilot.x.client import XClient

if TYPE_CHECKING:
    from xcopilot.brain.jobs import BrainDeps
    from xcopilot.review.matcher import PublicationMatcher


@dataclass
class AnalyticsKit:
    sync: OwnSync
    reports: Reports
    analyst: Analyst


def build_analytics(
    *,
    uow_factory: Callable[[], UnitOfWork],
    clock: Clock,
    x_for: Callable[[UUID], Awaitable[XClient]],
    queue: JobQueue,
    matcher: PublicationMatcher | None,
    brain: BrainDeps | None,
    notifier: SyncNotifier,
    gateway: LLMGateway,
) -> AnalyticsKit:
    return AnalyticsKit(
        sync=OwnSync(
            uow_factory=uow_factory,
            clock=clock,
            x_for=x_for,
            queue=queue,
            matcher=matcher,
            brain=brain,
            notifier=notifier,
        ),
        reports=Reports(uow_factory=uow_factory, gateway=gateway, clock=clock),
        analyst=Analyst(uow_factory=uow_factory, gateway=gateway, clock=clock, brain=brain),
    )
