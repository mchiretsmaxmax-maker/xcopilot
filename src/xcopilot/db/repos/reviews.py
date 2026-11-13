"""Решения владельца по черновикам."""

from __future__ import annotations

from xcopilot.db.models import Review
from xcopilot.db.repos.base import TenantRepo


class ReviewsRepo(TenantRepo[Review]):
    model = Review
