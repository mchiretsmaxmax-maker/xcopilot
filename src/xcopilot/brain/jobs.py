"""Фоновые задачи «Мозга»: brain.detect, brain.extract, brain.nightly."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from xcopilot.brain.config import BrainConfig, load_brain_config
from xcopilot.brain.context import BrainLLM, NoEmbedder, NoLLM
from xcopilot.clock import Clock
from xcopilot.db.uow import UnitOfWork
from xcopilot.embeddings.embedder import Embedder
from xcopilot.jobs.queue import Job

DETECT_KIND = "brain.detect"
EXTRACT_KIND = "brain.extract"
NIGHTLY_KIND = "brain.nightly"


@dataclass
class BrainDeps:
    uow_factory: Callable[[], UnitOfWork]
    clock: Clock
    llm: BrainLLM = NoLLM()  # noqa: RUF009
    embedder: Embedder = NoEmbedder()  # noqa: RUF009
    config: BrainConfig = load_brain_config()  # noqa: RUF009


async def handle_detect(job: Job, *, deps: BrainDeps) -> int:
    """payload {"source_id"}: детекторы и доказательства (свой пост — own_outcome)."""
    raise NotImplementedError


async def handle_extract(job: Job, *, deps: BrainDeps) -> int:
    """Извлечение по всем новым пачкам аккаунта; вернуть число затронутых знаний."""
    raise NotImplementedError


async def handle_nightly(job: Job, *, deps: BrainDeps) -> int:
    """Пересчёт со спадом, переходы, разбор конфликтов, снимок дня; вернуть число переходов."""
    raise NotImplementedError
