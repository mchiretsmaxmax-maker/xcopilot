"""Запись пакета знаний писателя (`knowledge_packs`) — одна функция за интерфейсом.

Пока `KnowledgeProvider` (этап 9) отдаёт только список знаний, строку пакета пишет писатель:
без неё не на что сослаться в `knowledge_applications.pack_id` и
`generation_runs.knowledge_pack_id`.
Этап 9B, собирая пакет сам, подменит `PackRecorder` (ADR 0020).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from xcopilot.db.repos.knowledge import KnowledgePacksRepo
from xcopilot.db.repos.writer import WriterRepo
from xcopilot.db.uow import DbSession
from xcopilot.metrics.events import EventType, emit
from xcopilot.retrieval.context_pack import KnowledgeItem, TaskSpec
from xcopilot.retrieval.tokens import estimate_tokens

PACK_SCHEMA_VERSION = 1


class PackRecorder(Protocol):
    async def record(
        self,
        session: DbSession,
        account_id: UUID,
        task: TaskSpec,
        items: Sequence[KnowledgeItem],
        probation: KnowledgeItem | None,
    ) -> UUID: ...


class DbPackRecorder:
    """Пишет строку `knowledge_packs` (consumer=writer) и событие `knowledge.pack_built`."""

    async def record(
        self,
        session: DbSession,
        account_id: UUID,
        task: TaskSpec,
        items: Sequence[KnowledgeItem],
        probation: KnowledgeItem | None,
    ) -> UUID:
        listed = [*items, *([probation] if probation is not None else [])]
        rows = [
            {
                "item_id": str(k.item_id),
                "version": None,
                "slot": "probation" if k is probation else k.slot,
                "rank": rank,
                "score": None,
            }
            for rank, k in enumerate(listed, start=1)
        ]
        taxonomy = await WriterRepo(session, account_id).taxonomy_version()
        tokens = estimate_tokens("\n".join(f"{k.guidance} {k.statement}" for k in listed))
        pack = await KnowledgePacksRepo(session, account_id).add(
            consumer="writer",
            task=task.model_dump(mode="json"),
            schema_version=PACK_SCHEMA_VERSION,
            taxonomy_version=int(taxonomy),
            items=rows,
            token_count=tokens,
        )
        await emit(
            session,
            account_id=account_id,
            type=EventType.KNOWLEDGE_PACK_BUILT,
            entity=("knowledge_pack", pack.id),
            props={
                "pack_id": str(pack.id),
                "consumer": "writer",
                "items": len(rows),
                "token_count": tokens,
                "format": task.format,
            },
        )
        return pack.id
