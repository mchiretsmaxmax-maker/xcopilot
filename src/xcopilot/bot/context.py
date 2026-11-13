"""Контекст запроса и запись событий действий пользователя."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from xcopilot.bot.deps import BotDeps
from xcopilot.metrics.events import EventType, emit


@dataclass(frozen=True)
class AccountCtx:
    """Текущий аккаунт пользователя (в MVP один)."""

    id: UUID
    handle: str
    niche: str | None
    lang: str
    tz: str
    x_read_mode: str


class Recorder:
    """Пишет user.action для пользователя; до онбординга — в системный аккаунт global."""

    def __init__(self, deps: BotDeps, user_id: UUID, account_id: UUID) -> None:
        self._deps = deps
        self._user_id = user_id
        self._account_id = account_id

    def for_account(self, account_id: UUID) -> Recorder:
        return Recorder(self._deps, self._user_id, account_id)

    async def __call__(self, action: str) -> None:
        async with self._deps.uow_factory() as uow:
            await emit(
                uow.session,
                account_id=self._account_id,
                type=EventType.USER_ACTION,
                actor="user",
                user_id=self._user_id,
                props={"action": action},
            )
