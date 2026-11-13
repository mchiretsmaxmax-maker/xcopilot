"""Зависимости бота: всё, что нужно обработчикам, собрано в одном объекте."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from aiogram import Bot

from xcopilot.clock import Clock
from xcopilot.config import Settings
from xcopilot.db.uow import UnitOfWork
from xcopilot.jobs.queue import JobQueue


@dataclass
class BotDeps:
    """Бот, настройки, доступ к БД, время и очередь задач."""

    bot: Bot
    settings: Settings
    uow_factory: Callable[[], UnitOfWork]
    clock: Clock
    queue: JobQueue
    flood_limit: int = 5
    flood_window_seconds: float = 10.0
