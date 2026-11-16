"""Сборка сервисов этапа 12 одним объектом (для BotDeps, JobDeps и тестовых стендов)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

from aiogram import Bot

from xcopilot.clock import Clock
from xcopilot.db.uow import UnitOfWork
from xcopilot.jobs.queue import JobQueue
from xcopilot.review.decisions import Decisions, Relinter
from xcopilot.review.matcher import PublicationMatcher
from xcopilot.review.presenter import CardPresenter
from xcopilot.review.publish import Publisher
from xcopilot.review.settings import PUBLICATIONS_MATCH_KIND, ReviewSettings
from xcopilot.review.translate import Translator
from xcopilot.x.client import XClient


@dataclass
class ReviewKit:
    presenter: CardPresenter
    decisions: Decisions
    publisher: Publisher
    matcher: PublicationMatcher
    settings: ReviewSettings


def build_review(
    *,
    bot: Bot,
    uow_factory: Callable[[], UnitOfWork],
    clock: Clock,
    queue: JobQueue,
    x_for: Callable[[UUID], Awaitable[XClient]] | None = None,
    relinter: Relinter | None = None,
    settings: ReviewSettings | None = None,
    translator: Translator | None = None,
) -> ReviewKit:
    settings = settings or ReviewSettings()

    async def find(account_id: UUID, _draft_id: UUID) -> None:
        """«Найди сам»: сверка ленты сейчас (только чтение X)."""
        await queue.enqueue(PUBLICATIONS_MATCH_KIND, {}, account_id=account_id)

    return ReviewKit(
        presenter=CardPresenter(bot, uow_factory, clock, settings, translator),
        decisions=Decisions(
            uow_factory, clock, queue, relinter=relinter, settings=settings, translator=translator
        ),
        publisher=Publisher(uow_factory, clock, x_for=x_for, on_find=find),
        matcher=PublicationMatcher(uow_factory, clock, settings),
        settings=settings,
    )
