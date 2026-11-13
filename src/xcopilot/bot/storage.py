"""Хранилище FSM в PostgreSQL (`bot_sessions`).

Диалог привязан к пользователю и системному аккаунту global: до онбординга своего
аккаунта у человека нет, а состояние обязано пережить рестарт. Неизвестному
пользователю (не прошедшему AuthMiddleware) состояние не сохраняется.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any
from uuid import UUID

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey

from xcopilot.db.repos import AccountsRepo, BotSessionsRepo, UsersRepo
from xcopilot.db.uow import UnitOfWork


def _state_name(state: StateType) -> str | None:
    if state is None:
        return None
    return state.state if isinstance(state, State) else str(state)


class PgStorage(BaseStorage):
    def __init__(self, uow_factory: Callable[[], UnitOfWork]) -> None:
        self._uow = uow_factory
        self._global_id: UUID | None = None

    async def _repo(self, uow: UnitOfWork, key: StorageKey) -> tuple[BotSessionsRepo, UUID] | None:
        user = await UsersRepo(uow.session).get_by_tg_id(key.user_id)
        if user is None:
            return None
        if self._global_id is None:
            self._global_id = (await AccountsRepo(uow.session).get_global()).id
        return BotSessionsRepo(uow.session, self._global_id), user.id

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        async with self._uow() as uow:
            found = await self._repo(uow, key)
            if found is None:
                return
            repo, user_id = found
            row = await repo.get_for_user(user_id)
            await repo.save(user_id, _state_name(state), dict(row.data) if row else {})

    async def get_state(self, key: StorageKey) -> str | None:
        async with self._uow() as uow:
            found = await self._repo(uow, key)
            if found is None:
                return None
            repo, user_id = found
            row = await repo.get_for_user(user_id)
            return row.state if row else None

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        async with self._uow() as uow:
            found = await self._repo(uow, key)
            if found is None:
                return
            repo, user_id = found
            row = await repo.get_for_user(user_id)
            await repo.save(user_id, row.state if row else None, dict(data))

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        async with self._uow() as uow:
            found = await self._repo(uow, key)
            if found is None:
                return {}
            repo, user_id = found
            row = await repo.get_for_user(user_id)
            return dict(row.data) if row else {}

    async def close(self) -> None:
        return None
