"""Подключение «Мозга» к этапам 9 и 10: `KnowledgeProvider` и `PackRecorder` (этап 9B)."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from xcopilot.clock import Clock
from xcopilot.db.uow import DbSession
from xcopilot.embeddings.embedder import Embedder
from xcopilot.retrieval.context_pack import KnowledgeItem, TaskSpec
from xcopilot.writer.probation import ProbationPicker


class BrainKnowledge:
    """Один объект: источник знаний для контекстного пакета и запись выдачи писателя."""

    def __init__(
        self, *, embedder: Embedder, clock: Clock, probation: ProbationPicker | None = None
    ) -> None:
        raise NotImplementedError

    async def select(
        self, session: DbSession, account_id: UUID, task: TaskSpec
    ) -> list[KnowledgeItem]:
        raise NotImplementedError

    async def by_ids(
        self, session: DbSession, account_id: UUID, ids: Sequence[UUID]
    ) -> list[KnowledgeItem]:
        raise NotImplementedError

    async def record(
        self,
        session: DbSession,
        account_id: UUID,
        task: TaskSpec,
        items: Sequence[KnowledgeItem],
        probation: KnowledgeItem | None,
    ) -> UUID:
        raise NotImplementedError


def make_knowledge(*, embedder: Embedder, clock: Clock) -> BrainKnowledge:
    """Фабрика сборки: `knowledge=k`, `pack_recorder=k`, `probation=k.probation`."""
    raise NotImplementedError
