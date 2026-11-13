"""Уведомления пользователям аккаунта."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from aiogram import Bot

from xcopilot.db.uow import UnitOfWork


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
        """Отправить text участникам аккаунта с указанными ролями; вернуть число получателей."""
        raise NotImplementedError
