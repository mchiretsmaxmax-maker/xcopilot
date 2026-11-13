"""Сборка зависимостей ингеста из настроек."""

from __future__ import annotations

from xcopilot.config import Settings
from xcopilot.ingest.blobs import FsBlobStore


def build_blob_store(settings: Settings) -> FsBlobStore:
    """Временное хранилище файлов в `settings.ingest_dir` (по умолчанию data/ingest)."""
    return FsBlobStore(settings.ingest_dir)
