"""Список команд бота (меню «/» в Telegram)."""

from __future__ import annotations

from aiogram import Bot
from aiogram.types import BotCommand

COMMANDS = [
    ("menu", "Постоянное меню"),
    ("status", "Что сейчас делает бот"),
    ("brain", "База знаний"),
    ("stats", "Аналитика"),
    ("queue", "Очередь карточек"),
    ("cost", "Расход"),
    ("restart", "Перезапустить бота (админ)"),
    ("settings", "Настройки"),
    ("help", "Помощь"),
    ("cancel", "Отменить шаг"),
    ("why", "Что попало в последний черновик"),
    ("health", "Состояние сервиса (админ)"),
]


async def set_commands(bot: Bot) -> None:
    await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in COMMANDS])
