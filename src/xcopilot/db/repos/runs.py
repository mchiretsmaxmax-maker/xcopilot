"""Запуски генерации."""

from __future__ import annotations

from xcopilot.db.models import GenerationRun
from xcopilot.db.repos.base import TenantRepo


class RunsRepo(TenantRepo[GenerationRun]):
    model = GenerationRun
