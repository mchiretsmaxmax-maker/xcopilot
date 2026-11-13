"""Аккаунты (тенанты), участники и пользователи — глобальные репозитории без account_id."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import cast, select, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession

from xcopilot.db.models import Account, AccountMember, User
from xcopilot.errors import NotFound


class AccountsRepo:
    """X-аккаунты и доступ к ним."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, *, handle: str, kind: str = "x", **fields: Any) -> Account:
        acc = Account(handle=handle, kind=kind, **fields)
        self.session.add(acc)
        await self.session.flush()
        await self.session.refresh(acc)
        return acc

    async def get(self, account_id: UUID) -> Account | None:
        return await self.session.get(Account, account_id, populate_existing=True)

    async def update_settings(self, account_id: UUID, **patch: Any) -> Account:
        """Слить patch в accounts.settings (jsonb ||); NotFound, если аккаунта нет."""
        stmt = (
            update(Account)
            .where(Account.id == account_id)
            .values(settings=Account.settings.op("||")(cast(patch, JSONB)))
            .returning(Account.id)
        )
        if (await self.session.execute(stmt)).scalar_one_or_none() is None:
            raise NotFound(f"accounts: {account_id} не найден")
        acc = await self.get(account_id)
        assert acc is not None
        return acc

    async def get_by_handle(self, handle: str) -> Account | None:
        stmt = select(Account).where(Account.kind == "x", Account.handle == handle)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_global(self) -> Account:
        """Системный аккаунт kind=global (создаётся сидом)."""
        stmt = select(Account).where(Account.kind == "global")
        acc = (await self.session.execute(stmt)).scalar_one_or_none()
        if acc is None:
            raise NotFound("системный аккаунт global не найден: миграции не накатаны?")
        return acc

    async def add_member(self, account_id: UUID, user_id: UUID, *, role: str) -> AccountMember:
        member = AccountMember(account_id=account_id, user_id=user_id, role=role)
        self.session.add(member)
        await self.session.flush()
        return member

    async def list_for_user(self, user_id: UUID) -> list[Account]:
        stmt = (
            select(Account)
            .join(AccountMember, AccountMember.account_id == Account.id)
            .where(AccountMember.user_id == user_id)
            .order_by(Account.id)
        )
        return list((await self.session.execute(stmt)).scalars().all())


class UsersRepo:
    """Пользователи Telegram."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, user_id: UUID) -> User | None:
        return await self.session.get(User, user_id)

    async def get_by_tg_id(self, tg_user_id: int) -> User | None:
        stmt = select(User).where(User.tg_user_id == tg_user_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_or_create(
        self, tg_user_id: int, *, name: str | None = None, is_admin: bool = False
    ) -> tuple[User, bool]:
        """Пользователь по tg_user_id; создаётся, если его нет. (пользователь, создан_ли)."""
        stmt = (
            insert(User)
            .values(tg_user_id=tg_user_id, name=name, is_admin=is_admin)
            .on_conflict_do_nothing(index_elements=["tg_user_id"])
            .returning(User.id)
        )
        created = (await self.session.execute(stmt)).scalar_one_or_none() is not None
        user = await self.get_by_tg_id(tg_user_id)
        assert user is not None
        return user, created
