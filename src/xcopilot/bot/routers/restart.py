"""/restart и «⚙️ Ещё» → «🔄 Перезапустить бота»: только администратор, с подтверждением.

Подтверждение: метка в `ops_marks` (кому сказать «✅ Бот перезапущен» после старта) и остановка
процесса с кодом выхода 3 (`BotDeps.request_restart`; собирает `main.serve`). Задачи в работе
возвращаются в очередь корректной остановкой воркера (этап 15a).
"""

from __future__ import annotations

from uuid import UUID

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from xcopilot.bot import keyboards, texts
from xcopilot.bot.access import is_admin
from xcopilot.bot.context import Recorder
from xcopilot.bot.deps import BotDeps
from xcopilot.ops.restart import mark_restart


def build_router() -> Router:
    """Новый роутер (экземпляр нельзя подключить к двум диспетчерам)."""
    router = Router(name="restart")

    async def ask(message: Message, deps: BotDeps, user_id: UUID) -> None:
        if not await is_admin(deps, user_id):
            await message.answer(texts.ADMIN_ONLY)
            return
        await message.answer(
            texts.RESTART_ASK,
            reply_markup=keyboards.yes_no("restart", "Да, перезапустить", "Отмена"),
        )

    @router.message(Command("restart"))
    async def cmd_restart(message: Message, deps: BotDeps, user_id: UUID, rec: Recorder) -> None:
        await rec("restart.ask")
        await ask(message, deps, user_id)

    @router.callback_query(F.data == "more:restart")
    async def on_more(query: CallbackQuery, deps: BotDeps, user_id: UUID) -> None:
        await query.answer()
        if isinstance(query.message, Message):
            await ask(query.message, deps, user_id)

    @router.callback_query(F.data == "restart:no")
    async def on_no(query: CallbackQuery) -> None:
        await query.answer()
        if isinstance(query.message, Message):
            await query.message.answer(texts.RESTART_DECLINED)

    @router.callback_query(F.data == "restart:yes")
    async def on_yes(query: CallbackQuery, deps: BotDeps, user_id: UUID, rec: Recorder) -> None:
        await query.answer()
        message = query.message
        if not isinstance(message, Message):
            return
        if not await is_admin(deps, user_id):
            await message.answer(texts.ADMIN_ONLY)
            return
        if deps.request_restart is None:
            await message.answer(texts.RESTART_UNAVAILABLE)
            return
        await rec("restart.confirm")
        await mark_restart(deps.uow_factory, deps.clock, query.from_user.id)
        await message.answer(texts.RESTARTING)
        deps.request_restart()

    return router
