"""/health — состояние сервиса (этап 15), только для администратора (users.is_admin)."""

from __future__ import annotations

from uuid import UUID

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from xcopilot.bot import texts
from xcopilot.bot.context import AccountCtx, Recorder
from xcopilot.bot.deps import BotDeps
from xcopilot.db.repos import UsersRepo
from xcopilot.ops.health import collect, render


def build_router() -> Router:
    """Новый роутер (экземпляр нельзя подключить к двум диспетчерам)."""
    router = Router(name="health")

    @router.message(Command("health"))
    async def cmd_health(
        message: Message, deps: BotDeps, acc: AccountCtx | None, rec: Recorder, user_id: UUID
    ) -> None:
        await rec("health")
        async with deps.uow_factory() as uow:
            user = await UsersRepo(uow.session).get(user_id)
        if user is None or not user.is_admin:
            await message.answer(texts.ADMIN_ONLY)
            return
        report = await collect(
            deps.uow_factory, deps.clock, acc.id if acc else None, deps.open_since
        )
        await message.answer(render(report))

    return router
