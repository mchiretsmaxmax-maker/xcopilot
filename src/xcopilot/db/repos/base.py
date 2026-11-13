"""Базовый репозиторий тенанта."""

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession


class TenantRepo[M]:
    """Репозиторий с обязательным account_id."""

    def __init__(self, session: AsyncSession, account_id: UUID) -> None:
        raise NotImplementedError

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError
