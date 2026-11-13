"""Проверяемые факты."""

from __future__ import annotations

from xcopilot.db.models import Fact
from xcopilot.db.repos.base import TenantRepo


class FactsRepo(TenantRepo[Fact]):
    model = Fact
