"""Темы постов."""

from __future__ import annotations

from xcopilot.db.models import Topic
from xcopilot.db.repos.base import TenantRepo


class TopicsRepo(TenantRepo[Topic]):
    model = Topic
