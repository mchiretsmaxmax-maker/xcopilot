"""Базовый репозиторий тенанта: каждый запрос ограничен своим account_id."""

from __future__ import annotations

import builtins
from typing import Any, ClassVar
from uuid import UUID

from sqlalchemy import ColumnElement, Select, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from xcopilot.db.models import Base
from xcopilot.errors import NotFound

_PROTECTED = frozenset({"id", "account_id"})


class TenantRepo[M: Base]:
    """Репозиторий с обязательным account_id.

    Подкласс задаёт `model`; все чтения и изменения идут с условием `account_id = :свой`,
    поэтому строка чужого аккаунта для репозитория «не существует» (get → None,
    update → NotFound). `id` и `account_id` менять нельзя.
    """

    model: ClassVar[type[Any]]

    def __init__(self, session: AsyncSession, account_id: UUID) -> None:
        if not isinstance(account_id, UUID):
            raise ValueError(f"{type(self).__name__}: account_id обязателен (UUID)")
        self.session = session
        self.account_id = account_id

    def col(self, name: str) -> ColumnElement[Any]:
        """Колонка таблицы модели по имени."""
        return self.model.__table__.c[name]  # type: ignore[no-any-return]

    def scoped(self) -> Select[M]:
        """SELECT модели, ограниченный своим аккаунтом."""
        return select(self.model).where(self.col("account_id") == self.account_id)

    async def get(self, id_: UUID) -> M | None:
        """Строка по id или None, если её нет или она чужая."""
        stmt = self.scoped().where(self.col("id") == id_).execution_options(populate_existing=True)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def require(self, id_: UUID) -> M:
        """Как get, но NotFound вместо None."""
        obj = await self.get(id_)
        if obj is None:
            raise NotFound(f"{self.model.__tablename__}: {id_} не найден")
        return obj

    async def list(self, *, limit: int = 100, offset: int = 0) -> builtins.list[M]:
        """Строки аккаунта в порядке создания (UUIDv7)."""
        stmt = self.scoped().order_by(self.col("id")).limit(limit).offset(offset)
        return builtins.list((await self.session.execute(stmt)).scalars().all())

    async def add(self, **values: Any) -> M:
        """Вставить строку своего аккаунта; вернуть её со значениями по умолчанию из БД."""
        if "account_id" in values:
            raise ValueError("account_id задаёт репозиторий")
        obj: M = self.model(account_id=self.account_id, **values)
        self.session.add(obj)
        await self.session.flush()
        await self.session.refresh(obj)
        return obj

    async def update(self, id_: UUID, **values: Any) -> M:
        """Изменить поля строки своего аккаунта; чужая или несуществующая — NotFound."""
        if _PROTECTED & values.keys():
            raise ValueError("id и account_id менять нельзя")
        stmt = (
            update(self.model)
            .where(self.col("id") == id_, self.col("account_id") == self.account_id)
            .values(**values)
            .returning(self.col("id"))
            .execution_options(synchronize_session=False)
        )
        if (await self.session.execute(stmt)).scalar_one_or_none() is None:
            raise NotFound(f"{self.model.__tablename__}: {id_} не найден")
        return await self.require(id_)
