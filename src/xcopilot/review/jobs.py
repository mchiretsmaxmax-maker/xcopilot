"""Задачи очереди этапа 12: `cards.expire`, `publications.match` (ADR 0025)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from uuid import UUID

from xcopilot.jobs.queue import Job
from xcopilot.review.matcher import Match, PublicationMatcher
from xcopilot.review.presenter import CardPresenter
from xcopilot.x.client import XClient

CARDS_EXPIRE_KIND = "cards.expire"
PUBLICATIONS_MATCH_KIND = "publications.match"


class AskNotifier(Protocol):
    async def notify(
        self,
        account_id: UUID,
        text: str,
        keyboard: Any = None,
        roles: tuple[str, ...] = ("owner",),
    ) -> int: ...


async def handle_cards_expire(job: Job, *, presenter: CardPresenter) -> int:
    raise NotImplementedError


async def handle_publications_match(
    job: Job,
    *,
    matcher: PublicationMatcher,
    x_for: Callable[[UUID], Awaitable[XClient]],
    notifier: AskNotifier,
) -> list[Match]:
    """Свои посты из X (только чтение) → автосверка; неоднозначные — вопрос владельцу."""
    raise NotImplementedError
