"""Уроки из правок и отказов."""

from __future__ import annotations

from xcopilot.db.models import Lesson
from xcopilot.db.repos.base import TenantRepo


class LessonsRepo(TenantRepo[Lesson]):
    model = Lesson
