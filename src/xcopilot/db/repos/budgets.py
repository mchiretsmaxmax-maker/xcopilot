"""Лимиты расходов аккаунта."""

from __future__ import annotations

from xcopilot.db.models import Budget
from xcopilot.db.repos.base import TenantRepo


class BudgetsRepo(TenantRepo[Budget]):
    model = Budget
