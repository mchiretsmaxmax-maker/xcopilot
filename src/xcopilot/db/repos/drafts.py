"""Черновики, их ревизии и статусы."""

from __future__ import annotations

from xcopilot.db.models import Draft
from xcopilot.db.repos.base import TenantRepo


class DraftsRepo(TenantRepo[Draft]):
    model = Draft
