"""Каталог метрик: разбор definitions/*.sql и синхронизация в metric_definitions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from xcopilot.db.uow import DbSession

DEFINITIONS_DIR = Path(__file__).parent / "definitions"


class MetricDefinition(BaseModel):
    key: str
    title: str
    description: str | None = None
    unit: str
    grain: str
    version: int
    dimensions: list[str] = []
    formula_sql: str
    formula_hash: str


@dataclass(frozen=True, slots=True)
class SyncResult:
    created: list[str]
    updated: list[str]
    unchanged: list[str]
    deactivated: list[str]


def load_definitions(path: Path | None = None) -> list[MetricDefinition]:
    """Разобрать все `*.sql` каталога (по умолчанию — metrics/definitions)."""
    raise NotImplementedError


async def sync_catalog(session: DbSession, path: Path | None = None) -> SyncResult:
    """Привести metric_definitions в соответствие файлам; формула без новой версии — ошибка."""
    raise NotImplementedError
