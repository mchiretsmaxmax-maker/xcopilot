"""Права в боте: администратор сервиса и управляющий аккаунтом."""

from __future__ import annotations

from uuid import UUID

from xcopilot.bot.deps import BotDeps
from xcopilot.db.repos import AccountsRepo, UsersRepo


async def is_admin(deps: BotDeps, user_id: UUID) -> bool:
    """`users.is_admin` или Telegram-ID из OWNER_TG_IDS."""
    async with deps.uow_factory() as uow:
        user = await UsersRepo(uow.session).get(user_id)
    if user is None:
        return False
    return bool(user.is_admin) or user.tg_user_id in deps.settings.owner_tg_ids


async def can_manage(deps: BotDeps, user_id: UUID, account_id: UUID) -> bool:
    """Отменять задачи аккаунта может его владелец (role owner) или администратор."""
    if await is_admin(deps, user_id):
        return True
    async with deps.uow_factory() as uow:
        return await AccountsRepo(uow.session).member_role(account_id, user_id) == "owner"
