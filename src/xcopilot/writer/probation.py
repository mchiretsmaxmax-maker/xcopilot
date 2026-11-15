"""Испытуемое знание для варианта 🧪 (ТЗ v3.1, этап 10, задача 12).

Настоящий выбор (наименее проверенное знание на испытании за 14 дней) даст этап 9A/9B;
до него — заглушка «нет испытуемого».
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from xcopilot.db.uow import DbSession
from xcopilot.retrieval.context_pack import KnowledgeItem, TaskSpec


class ProbationPicker(Protocol):
    async def pick(
        self, session: DbSession, account_id: UUID, task: TaskSpec
    ) -> KnowledgeItem | None: ...


class NoProbation:
    """Заглушка до этапа 9A/9B: испытуемого знания нет."""

    async def pick(
        self, session: DbSession, account_id: UUID, task: TaskSpec
    ) -> KnowledgeItem | None:
        return None
