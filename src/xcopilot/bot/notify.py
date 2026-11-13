"""Уведомления пользователям аккаунта."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

import structlog
from aiogram import Bot

from xcopilot.db.repos import AccountsRepo
from xcopilot.db.uow import UnitOfWork

log = structlog.get_logger()


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
