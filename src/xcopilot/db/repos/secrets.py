"""Зашифрованные секреты аккаунта."""

from __future__ import annotations

from xcopilot.db.models import Secret
from xcopilot.db.repos.base import TenantRepo


class SecretsRepo(TenantRepo[Secret]):
    model = Secret
