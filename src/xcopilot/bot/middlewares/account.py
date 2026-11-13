"""Контекст аккаунта: текущий аккаунт пользователя (в MVP один)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from xcopilot.bot.context import AccountCtx, Recorder
from xcopilot.bot.deps import BotDeps
from xcopilot.db.repos import AccountsRepo


class AccountMiddleware(BaseMiddleware):
    """Кладёт в data: `acc` (AccountCtx | None), `event_account_id` и `rec` (запись событий)."""

    def __init__(self, deps: BotDeps) -> None:
        self._deps = deps

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with self._deps.uow_factory() as uow:
            repo = AccountsRepo(uow.session)
            accounts = await repo.list_for_user(data["user_id"])
            if accounts:
                a = accounts[0]
                acc: AccountCtx | None = AccountCtx(
                    a.id, a.handle, a.niche, a.lang, a.tz, a.x_read_mode
                )
                event_account_id = a.id
            else:
                acc = None
                event_account_id = (await repo.get_global()).id
        data["acc"] = acc
        data["event_account_id"] = event_account_id
        data["rec"] = Recorder(self._deps, data["user_id"], event_account_id)
        return await handler(event, data)
