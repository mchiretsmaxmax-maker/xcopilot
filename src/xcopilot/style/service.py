"""Версии паспорта: черновик → активный → архив, откат и правки владельца."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from xcopilot.db.uow import UnitOfWork
from xcopilot.style.extractor import StyleExtractor
from xcopilot.style.passport import StylePassport


class PassportVersion(BaseModel):
    id: UUID
    version: int
    status: str
    created_by: str
    created_at: datetime
    passport: StylePassport


class StyleService:
    def __init__(
        self, uow_factory: Callable[[], UnitOfWork], extractor: StyleExtractor | None = None
    ) -> None:
        self._uow = uow_factory
        self._extractor = extractor

    async def create_draft_version(
        self,
        account_id: UUID,
        passport: StylePassport,
        *,
        created_by: str = "llm",
        user_id: UUID | None = None,
        source_refs: list[str] | None = None,
    ) -> PassportVersion:
        raise NotImplementedError

    async def get(self, account_id: UUID, version: int) -> PassportVersion | None:
        raise NotImplementedError

    async def get_active(self, account_id: UUID) -> PassportVersion | None:
        raise NotImplementedError

    async def list_versions(self, account_id: UUID) -> list[PassportVersion]:
        raise NotImplementedError

    async def activate(
        self, account_id: UUID, version: int, *, user_id: UUID | None = None
    ) -> PassportVersion:
        raise NotImplementedError

    async def rollback(self, account_id: UUID, *, user_id: UUID | None = None) -> PassportVersion:
        raise NotImplementedError

    async def patch_hard_rule(
        self, account_id: UUID, name: str, value: Any, *, user_id: UUID
    ) -> PassportVersion:
        raise NotImplementedError

    async def add_banned_phrase(
        self, account_id: UUID, phrase: str, *, user_id: UUID
    ) -> PassportVersion | None:
        raise NotImplementedError

    async def remove_rule(self, account_id: UUID, number: int, *, user_id: UUID) -> PassportVersion:
        raise NotImplementedError

    async def rebuild(self, account_id: UUID, *, user_id: UUID | None = None) -> PassportVersion:
        raise NotImplementedError


def list_rules(passport: StylePassport) -> list[tuple[str, str]]:
    """Пронумерованные правила для показа и «убери правило N»: (имя, текст), по порядку."""
    raise NotImplementedError


def describe_change(old: StylePassport | None, new: StylePassport) -> str:
    """«+2 запрета, −1 правило» — чем новая версия отличается от старой."""
    raise NotImplementedError
