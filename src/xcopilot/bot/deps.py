"""Зависимости бота: всё, что нужно обработчикам, собрано в одном объекте."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aiogram import Bot

from xcopilot.clock import Clock
from xcopilot.config import Settings
from xcopilot.db.uow import UnitOfWork
from xcopilot.jobs.queue import JobQueue

if TYPE_CHECKING:
    from uuid import UUID

    from xcopilot.brain.jobs import BrainDeps
    from xcopilot.ingest.batch import Debouncer
    from xcopilot.ingest.service import IngestPayload, IngestService
    from xcopilot.retrieval.context_pack import KnowledgeProvider
    from xcopilot.review.kit import ReviewKit
    from xcopilot.style.service import StyleService


IngestBatchKey = tuple[int, "UUID", "UUID"]  # (чат, аккаунт, пользователь)


@dataclass
class BotDeps:
    """Бот, настройки, доступ к БД, время и очередь задач."""

    bot: Bot
    settings: Settings
    uow_factory: Callable[[], UnitOfWork]
    clock: Clock
    queue: JobQueue
    flood_limit: int = 30
    flood_window_seconds: float = 10.0
    ingest: IngestService | None = None
    # ТЗ v3.1: пачки (альбом, серия за 8 с) — routers.learn.build_ingest_debouncer(deps)
    ingest_batch: Debouncer[IngestBatchKey, IngestPayload] | None = None
    knowledge: KnowledgeProvider | None = None
    style: StyleService | None = None
    review: ReviewKit | None = None  # этап 12: карточки, решения, публикация
    brain: BrainDeps | None = None  # 9C: решения владельца о знаниях
