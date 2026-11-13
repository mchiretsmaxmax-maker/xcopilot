"""Куски документов и их векторы."""

from __future__ import annotations

from xcopilot.db.models import Chunk
from xcopilot.db.repos.base import TenantRepo


class ChunksRepo(TenantRepo[Chunk]):
    model = Chunk
