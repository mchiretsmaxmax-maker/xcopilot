"""Публикации черновиков в X."""

from __future__ import annotations

from xcopilot.db.models import Publication
from xcopilot.db.repos.base import TenantRepo


class PublicationsRepo(TenantRepo[Publication]):
    model = Publication
