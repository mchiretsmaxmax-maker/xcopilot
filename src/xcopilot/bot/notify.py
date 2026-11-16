"""Уведомления пользователям аккаунта."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

import structlog
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

from xcopilot.db.repos import AccountsRepo
from xcopilot.db.uow import UnitOfWork
from xcopilot.errors import ExternalServiceError

log = structlog.get_logger()


class DeliveryFailed(ExternalServiceError):
    """Сообщение не доставлено в Telegram (повторы исчерпаны или ошибка не повторяемая)."""


async def send_with_retry(bot: Bot, chat_id: int, text: str, **kwargs: Any) -> Any:
    raise NotImplementedError


class Notifier:
    def __init__(self, bot: Bot, uow_factory: Callable[[], UnitOfWork]) -> None:
        self._bot = bot
        self._uow = uow_factory

    async def notify(
        self,
        account_id: UUID,
        text: str,
        keyboard: Any = None,
        roles: tuple[str, ...] = ("owner",),
    ) -> int:
        """Отправить text участникам аккаунта с указанными ролями; вернуть число получателей.

        Недоступный получатель (бот заблокирован) не мешает остальным: ошибка в лог.
        """
        async with self._uow() as uow:
            tg_ids = await AccountsRepo(uow.session).member_tg_ids(account_id, roles)
        sent = 0
        for tg_id in tg_ids:
            try:
                await self._bot.send_message(tg_id, text, reply_markup=keyboard)
                sent += 1
            except Exception:
                log.warning("notify_failed", tg_user_id=tg_id, exc_info=True)
        return sent

    async def notify_owner(self, account_id: UUID, text: str) -> None:
        """OwnerNotifier для X (x/auth.py): сообщение владельцам аккаунта."""
        await self.notify(account_id, text)

    async def send_status(
        self, account_id: UUID, text: str, keyboard: Any = None
    ) -> list[tuple[int, int]]:
        """Прислать владельцам статус-сообщение; вернуть (чат, id сообщения) для правок."""
        async with self._uow() as uow:
            tg_ids = await AccountsRepo(uow.session).member_tg_ids(account_id, ("owner",))
        out: list[tuple[int, int]] = []
        for tg_id in tg_ids:
            try:
                message = await self._bot.send_message(tg_id, text, reply_markup=keyboard)
                out.append((tg_id, message.message_id))
            except Exception:
                log.warning("notify_failed", tg_user_id=tg_id, exc_info=True)
        return out

    async def edit_text(
        self, chat_id: int, message_id: int, text: str, keyboard: Any = None
    ) -> None:
        """Поправить сообщение; «не изменено», лимит и сбои Telegram не роняют задачу."""
        try:
            await self._bot.edit_message_text(
                text, chat_id=chat_id, message_id=message_id, reply_markup=keyboard
            )
        except TelegramBadRequest as exc:
            if "message is not modified" not in str(exc):
                log.warning("edit_failed", chat_id=chat_id, error=str(exc))
        except TelegramRetryAfter as exc:
            log.warning("edit_rate_limited", chat_id=chat_id, retry_after=exc.retry_after)
        except Exception:
            log.warning("edit_failed", chat_id=chat_id, exc_info=True)
