"""/help."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from xcopilot.bot import texts
from xcopilot.bot.context import Recorder


def build_router() -> Router:
    """Новый роутер (экземпляр нельзя подключить к двум диспетчерам)."""
    router = Router(name="help")

    @router.message(Command("help"))
    async def cmd_help(message: Message, rec: Recorder) -> None:
        await rec("help")
        await message.answer(texts.HELP)

    return router
