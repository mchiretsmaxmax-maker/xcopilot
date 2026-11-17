"""Хранилище FSM в PostgreSQL (`bot_sessions`).

Диалог привязан к пользователю и системному аккаунту global: до онбординга своего
аккаунта у человека нет, а состояние обязано пережить рестарт. Неизвестному
пользователю (не прошедшему AuthMiddleware) состояние не сохраняется.

Служебный ключ `_ui` в данных (fix/one-action: сообщения с кнопками текущего действия) скрыт
от FSM: `get_data` его не отдаёт, `set_data` и `clear` его не трогают; читать и писать —
`get_ui` / `set_ui`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any
from uuid import UUID

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey

from xcopilot.db.repos import AccountsRepo, BotSessionsRepo, UsersRepo
from xcopilot.db.uow import UnitOfWork

UI_KEY = "_ui"


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
            new = {k: v for k, v in data.items() if k != UI_KEY}
            if row is not None and UI_KEY in row.data:
                new[UI_KEY] = row.data[UI_KEY]
            await repo.save(user_id, row.state if row else None, new)

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        async with self._uow() as uow:
            found = await self._repo(uow, key)
            if found is None:
                return {}
            repo, user_id = found
            row = await repo.get_for_user(user_id)
            return {k: v for k, v in row.data.items() if k != UI_KEY} if row else {}

    async def get_ui(self, key: StorageKey) -> dict[str, Any]:
        """Служебные данные интерфейса (кнопки текущего действия, закрытые сообщения)."""
        async with self._uow() as uow:
            found = await self._repo(uow, key)
            if found is None:
                return {}
            repo, user_id = found
            row = await repo.get_for_user(user_id)
            return dict(row.data.get(UI_KEY) or {}) if row else {}

    async def set_ui(self, key: StorageKey, ui: Mapping[str, Any]) -> None:
        async with self._uow() as uow:
            found = await self._repo(uow, key)
            if found is None:
                return
            repo, user_id = found
            row = await repo.get_for_user(user_id)
            data = dict(row.data) if row else {}
            data[UI_KEY] = dict(ui)
            await repo.save(user_id, row.state if row else None, data)

    async def close(self) -> None:
        return None
