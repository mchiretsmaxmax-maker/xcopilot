"""Доступ: только пользователи из `users`; владельцы из OWNER_TG_IDS заводятся сами."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User

from xcopilot.bot import texts
from xcopilot.bot.deps import BotDeps
from xcopilot.db.repos import UsersRepo

log = structlog.get_logger()


class AuthMiddleware(BaseMiddleware):
    def __init__(self, deps: BotDeps) -> None:
        self._deps = deps

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user: User | None = data.get("event_from_user")
        if tg_user is None or tg_user.is_bot:
            return None
        async with self._deps.uow_factory() as uow:
            users = UsersRepo(uow.session)
            row = await users.get_by_tg_id(tg_user.id)
            if row is None and tg_user.id in self._deps.settings.owner_tg_ids:
                row, _ = await users.get_or_create(
                    tg_user.id, name=tg_user.full_name, is_admin=True
                )
            user_id = row.id if row is not None else None
        if user_id is None:
            log.info("bot_access_denied", tg_user_id=tg_user.id)
            if isinstance(event, Message):
                await event.answer(texts.DENIED)
            elif isinstance(event, CallbackQuery):
                await event.answer(texts.DENIED, show_alert=True)
            return None
        data["user_id"] = user_id
        return await handler(event, data)
