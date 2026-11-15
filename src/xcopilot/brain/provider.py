"""Подключение «Мозга» к этапам 9 и 10: `KnowledgeProvider` и `PackRecorder` (этап 9B).

Выдача пишется один раз: `select()` собирает пакет и сохраняет строку `knowledge_packs`,
`record()` писателя дописывает в неё слот испытания и возвращает тот же `pack_id`.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from xcopilot.brain import api
from xcopilot.brain.probation import BrainProbationPicker
from xcopilot.brain.schemas import PackItem
from xcopilot.clock import Clock
from xcopilot.db.repos.knowledge import KnowledgePacksRepo
from xcopilot.db.repos.pack import PackRepo
from xcopilot.db.uow import DbSession
from xcopilot.embeddings.embedder import Embedder
from xcopilot.retrieval.context_pack import KnowledgeItem, TaskSpec
from xcopilot.writer.knowledge import DbPackRecorder
from xcopilot.writer.probation import ProbationPicker

_PENDING_MAX = 256


def _view(i: PackItem) -> KnowledgeItem:
    return KnowledgeItem(
        item_id=i.item_id,
        slot=i.slot,
        guidance=i.guidance,
        statement=i.statement,
        skeleton=i.skeleton,
    )


class BrainKnowledge:
    """Один объект: источник знаний для контекстного пакета и запись выдачи писателя."""

    def __init__(
        self, *, embedder: Embedder, clock: Clock, probation: ProbationPicker | None = None
    ) -> None:
        self._embedder = embedder
        self._clock = clock
        self.probation: ProbationPicker = probation or BrainProbationPicker(clock)
        self._pending: dict[tuple[UUID, tuple[UUID, ...]], UUID] = {}

    async def select(
        self, session: DbSession, account_id: UUID, task: TaskSpec
    ) -> list[KnowledgeItem]:
        pack = await api.build_pack(
            session,
            account_id,
            task,
            embedder=self._embedder,
            clock=self._clock,
            with_probation=False,
        )
        key = (account_id, tuple(i.item_id for i in pack.items))
        if len(self._pending) >= _PENDING_MAX:
            self._pending.pop(next(iter(self._pending)))
        self._pending[key] = pack.pack_id
        return [_view(i) for i in pack.items]

    async def by_ids(
        self, session: DbSession, account_id: UUID, ids: Sequence[UUID]
    ) -> list[KnowledgeItem]:
        if not ids:
            return []
        rows = {r["id"]: r for r in await PackRepo(session, account_id).cards(ids)}
        return [
            KnowledgeItem(
                item_id=i,
                slot="anti" if rows[i]["kind"] == "anti_pattern" else "core",
                guidance=rows[i]["guidance"],
                statement=rows[i]["statement"],
                skeleton=rows[i]["skeleton"],
            )
            for i in ids
            if i in rows
        ]

    async def record(
        self,
        session: DbSession,
        account_id: UUID,
        task: TaskSpec,
        items: Sequence[KnowledgeItem],
        probation: KnowledgeItem | None,
    ) -> UUID:
        key = (account_id, tuple(i.item_id for i in items))
        pack_id = self._pending.pop(key, None)
        if pack_id is None:
            return await DbPackRecorder().record(session, account_id, task, items, probation)
        if probation is not None:
            repo = PackRepo(session, account_id)
            row = await repo.pack(pack_id)
            cards = await repo.cards([probation.item_id])
            if row is not None and cards:
                stored = list(row["items"])
                stored.append(
                    {
                        "item_id": str(probation.item_id),
                        "version": cards[0]["version"],
                        "slot": "probation",
                        "rank": len(stored) + 1,
                        "score": None,
                        "confidence": cards[0]["confidence_label"],
                        "proven": False,
                    }
                )
                await KnowledgePacksRepo(session, account_id).update(pack_id, items=stored)
        return pack_id


def make_knowledge(*, embedder: Embedder, clock: Clock) -> BrainKnowledge:
    """Фабрика сборки: `knowledge=k`, `pack_recorder=k`, `probation=k.probation`."""
    return BrainKnowledge(embedder=embedder, clock=clock)
