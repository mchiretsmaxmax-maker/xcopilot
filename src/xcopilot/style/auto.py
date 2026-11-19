"""Автопересборка паспорта по новым чистым своим постам (заглушка, feat/clean-voice)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from xcopilot.db.uow import DbSession, UnitOfWork

AUTO_REBUILD_KIND = "style.auto_rebuild"


@dataclass(frozen=True)
class AutoConfig:
    min_new: int = 5
    min_first: int = 10
    min_interval: timedelta = timedelta(hours=12)
    delay: timedelta = timedelta(minutes=2)


@dataclass(frozen=True)
class AutoResult:
    reason: str
    new: int = 0
    version: int | None = None


def load_auto_config() -> AutoConfig:
    raise NotImplementedError


async def request_auto_rebuild(
    session: DbSession, account_id: UUID, *, now: datetime, config: AutoConfig | None = None
) -> UUID | None:
    raise NotImplementedError


async def run_auto_rebuild(
    *,
    account_id: UUID,
    service: Any,
    uow_factory: Callable[[], UnitOfWork],
    clock: Any,
    notifier: Any,
    config: AutoConfig | None = None,
) -> AutoResult:
    raise NotImplementedError
