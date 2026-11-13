"""Хранилище FSM в PostgreSQL (`bot_sessions`)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey

from xcopilot.db.uow import UnitOfWork


class PgStorage(BaseStorage):
    def __init__(self, uow_factory: Callable[[], UnitOfWork]) -> None:
        self._uow = uow_factory

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        raise NotImplementedError

    async def get_state(self, key: StorageKey) -> str | None:
        raise NotImplementedError

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        raise NotImplementedError

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        raise NotImplementedError

    async def close(self) -> None:
        return None


__all__ = ["PgStorage", "State"]
