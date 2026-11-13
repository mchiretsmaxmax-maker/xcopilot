"""Паспорта стиля (версии, один активный)."""

from __future__ import annotations

from xcopilot.db.models import StylePassport
from xcopilot.db.repos.base import TenantRepo


class PassportsRepo(TenantRepo[StylePassport]):
    model = StylePassport
