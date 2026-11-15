"""Задачи очереди этапа 12: `cards.expire`, `publications.match` (ADR 0025)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from uuid import UUID

import structlog
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from xcopilot.bot import texts
from xcopilot.jobs.queue import Job
from xcopilot.review.callbacks import MATCH_NO, MATCH_YES, card_cb
from xcopilot.review.matcher import Match, PublicationMatcher
from xcopilot.review.presenter import CardPresenter
from xcopilot.review.settings import CARDS_EXPIRE_KIND as CARDS_EXPIRE_KIND
from xcopilot.review.settings import PUBLICATIONS_MATCH_KIND as PUBLICATIONS_MATCH_KIND
from xcopilot.x.client import XClient
from xcopilot.x.errors import XError
from xcopilot.x.models import XPost

log = structlog.get_logger()

OWN_POSTS_LIMIT = 50  # свежих своих постов на одну сверку


class AskNotifier(Protocol):
    async def notify(
        self,
        account_id: UUID,
        text: str,
        keyboard: Any = None,
        roles: tuple[str, ...] = ("owner",),
    ) -> int: ...


def match_keyboard(draft_id: UUID, post_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, это он", callback_data=card_cb(draft_id, MATCH_YES, post_id)
                ),
                InlineKeyboardButton(
                    text="Нет", callback_data=card_cb(draft_id, MATCH_NO, post_id)
                ),
            ]
        ]
    )


async def handle_cards_expire(job: Job, *, presenter: CardPresenter) -> int:
    """Карточки старше 48 часов → expired, кнопки сняты."""
    return await presenter.expire_cards(job.account_id)


async def handle_publications_match(
    job: Job,
    *,
    matcher: PublicationMatcher,
    x_for: Callable[[UUID], Awaitable[XClient]],
    notifier: AskNotifier,
) -> list[Match]:
    """Свои посты из X (только чтение) → автосверка; неоднозначные — вопрос владельцу.

    X недоступен или выключен — сверяем с пустой лентой: просроченные черновики всё равно
    становятся `unconfirmed`, а владелец может прислать ссылку сам.
    """
    posts: list[XPost] = []
    try:
        client = await x_for(job.account_id)
        posts = await client.get_own_posts(limit=OWN_POSTS_LIMIT)
    except XError as exc:
        log.info(
            "publications_match_no_feed", account_id=str(job.account_id), error=type(exc).__name__
        )
    by_id = {p.id: p for p in posts}
    matches = await matcher.match_publications(job.account_id, posts)
    for m in matches:
        if m.outcome != "ask" or m.x_post_id is None:
            continue
        post = by_id[m.x_post_id]
        text = f"{texts.MATCH_ASK}\n\n{post.text}\n{m.url}"
        await notifier.notify(job.account_id, text, match_keyboard(m.draft_id, m.x_post_id))
    return matches
