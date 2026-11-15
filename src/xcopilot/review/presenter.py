"""Показ карточек владельцу (ТЗ, этап 12, задачи 1, 3, 8, 11).

Карточка запуска уходит после решения ворот качества (этап 11) по финальным ревизиям
вариантов. Статус `shown` и событие `card.shown` пишутся только после успешной отправки.
Сверх дневного предела карточка ждёт в /queue. Навигация ◀ ▶ редактирует одно сообщение.
Карточка старше 48 часов — `expired`: кнопки сняты, решения по ней не принимаются.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from aiogram import Bot

from xcopilot.clock import Clock
from xcopilot.db.uow import UnitOfWork
from xcopilot.review.settings import ReviewSettings


@dataclass(frozen=True)
class QueuedCard:
    run_id: UUID
    draft_id: UUID  # первый вариант — для кнопки «Показать»
    title: str
    created_at: datetime


class CardPresenter:
    def __init__(
        self,
        bot: Bot,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        settings: ReviewSettings | None = None,
    ) -> None:
        self._bot = bot
        self._uow = uow_factory
        self._clock = clock
        self._settings = settings or ReviewSettings()

    async def show_for_drafts(self, account_id: UUID, draft_ids: Sequence[UUID]) -> int:
        """После ворот: показать карточки запусков этих черновиков; вернуть число отправленных."""
        raise NotImplementedError

    async def show_run(
        self, account_id: UUID, run_id: UUID, *, chat_id: int | None = None, force: bool = False
    ) -> int:
        """Карточка запуска владельцам (или одному чату); force — мимо дневного предела."""
        raise NotImplementedError

    async def navigate(
        self, account_id: UUID, draft_id: UUID, variant_no: int, chat_id: int, message_id: int
    ) -> bool:
        """Показать вариант variant_no в том же сообщении; False — карточка устарела."""
        raise NotImplementedError

    async def is_expired(self, account_id: UUID, draft_id: UUID) -> bool:
        """Карточка черновика старше срока (и пометить её expired)."""
        raise NotImplementedError

    async def disable(self, chat_id: int, message_id: int) -> None:
        """Снять кнопки с сообщения карточки (ошибки Telegram не мешают)."""
        raise NotImplementedError

    async def expire_cards(self, account_id: UUID) -> int:
        """Задача cards.expire: карточки старше срока → expired, кнопки сняты; вернуть число."""
        raise NotImplementedError

    async def queued(self, account_id: UUID) -> list[QueuedCard]:
        """Карточки, ждущие показа (дневной предел)."""
        raise NotImplementedError

    async def why(self, account_id: UUID, draft_id: UUID) -> str:
        """«Почему так» по варианту: знания пакета и отметки применения."""
        raise NotImplementedError
