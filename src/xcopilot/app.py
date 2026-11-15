"""Сборка живого приложения (заглушка red-фазы)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from xcopilot.bot.deps import BotDeps
from xcopilot.config import Settings
from xcopilot.db.engine import SessionFactory
from xcopilot.db.uow import UnitOfWork
from xcopilot.jobs.queue import JobQueue
from xcopilot.jobs.worker import Registry
from xcopilot.x.client import XClient


@dataclass
class App:
    settings: Settings
    session_factory: SessionFactory
    uow_factory: Callable[[], UnitOfWork]
    queue: JobQueue
    bot_deps: BotDeps
    registry: Registry
    x_for: Callable[[UUID], Awaitable[XClient]]

    async def startup(self) -> None:
        raise NotImplementedError

    async def aclose(self) -> None:
        raise NotImplementedError


def install_hooks() -> None:
    raise NotImplementedError


def build_app(settings: Settings, **_: Any) -> App:
    raise NotImplementedError


Poller = Callable[[BotDeps, asyncio.Event], Awaitable[None]]


async def serve_app(app: App, stop: asyncio.Event, **_: Any) -> None:
    raise NotImplementedError
