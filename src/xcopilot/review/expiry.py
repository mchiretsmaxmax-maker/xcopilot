"""Срок карточки (ТЗ, этап 12, задача 8): старше 48 часов — `expired`, решения не принимаются."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from xcopilot.db.repos import DraftsRepo
from xcopilot.db.repos.drafts import DraftStatus
from xcopilot.db.repos.review import ReviewCardsRepo
from xcopilot.db.uow import DbSession

# статусы, из которых вариант карточки устаревает (решённые не трогаем)
OPEN = (DraftStatus.JUDGED, DraftStatus.NEEDS_OWNER, DraftStatus.SHOWN)


async def run_expired(
    session: DbSession, account_id: UUID, run_id: UUID, now: datetime, ttl: timedelta
) -> bool:
    """Карточка запуска показана раньше now − ttl (или уже помечена expired)."""
    cards = await ReviewCardsRepo(session, account_id).by_run(run_id)
    if any(c.state == "expired" for c in cards):
        return True
    shown = [c.shown_at for c in cards if c.shown_at is not None]
    return bool(shown) and min(shown) + ttl < now


async def expire_run(session: DbSession, account_id: UUID, run_id: UUID) -> list[tuple[int, int]]:
    """Пометить карточки запуска и его нерешённые варианты expired; вернуть (чат, сообщение)."""
    cards = ReviewCardsRepo(session, account_id)
    messages: list[tuple[int, int]] = []
    for card in await cards.by_run(run_id):
        if card.state != "expired":
            await cards.update(card.id, state="expired")
        if card.message_id is not None:
            messages.append((card.chat_id, card.message_id))
    drafts = DraftsRepo(session, account_id)
    for draft in await drafts.list_by_run(run_id):
        if DraftStatus(draft.status) in OPEN:
            await drafts.set_status(draft.id, DraftStatus.EXPIRED)
    return messages
