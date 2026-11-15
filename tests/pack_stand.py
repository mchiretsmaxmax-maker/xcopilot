"""Стенд пакета знаний (этап 9B): знания с областью, вектором и качеством, подделка эмбеддера."""

from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tests.brain_core_stand import NOW, knowledge
from tests.fakes import FakeClock
from tests.retrieval_stand import ScriptedEmbedder, axis
from xcopilot.brain import api
from xcopilot.brain.schemas import KnowledgePack, TaskSpec
from xcopilot.db.repos.chunks import EmbeddingModelsRepo
from xcopilot.db.repos.knowledge import KnowledgeEmbeddingsRepo, KnowledgeItemsRepo

_COUNTER = itertools.count(1)
TOPIC = "как писать про провалы"
LONG = " ".join(f"слово{c}" for c in "абвгдежзиклмнопрстуфхцчшэюя")


def task(fmt: str = "post", **kw: Any) -> TaskSpec:
    kw.setdefault("lang", "ru")
    kw.setdefault("topic", TOPIC)
    return TaskSpec(format=fmt, **kw)


def embedder() -> ScriptedEmbedder:
    return ScriptedEmbedder(vectors={TOPIC: axis(0)})


async def kitem(
    session: AsyncSession,
    acc: UUID,
    *,
    kind: str = "hook_pattern",
    element: str = "hook",
    layer: str = "craft",
    status: str = "active",
    guidance: str | None = None,
    quality: float = 0.6,
    scopes: Mapping[str, Sequence[str]] | None = None,
    vec: Sequence[float] | None = None,
    **extra: Any,
) -> UUID:
    """Знание с версией 1, областью и вектором (по умолчанию — вдоль темы задачи)."""
    guidance = guidance or f"Совет {kind} {element} №{next(_COUNTER)}"
    item_id = await knowledge(
        session,
        acc,
        kind=kind,
        element=element,
        layer=layer,
        status=status,
        guidance=guidance,
        statement=f"Наблюдение: {guidance}",
        skeleton="[раз] → [два]" if kind in ("structure", "exemplar") else None,
        quality=quality,
        **extra,
    )
    if scopes:
        await KnowledgeItemsRepo(session, acc).set_scopes(item_id, scopes)
    await set_vec(session, acc, item_id, vec if vec is not None else axis(0))
    return item_id


async def set_vec(session: AsyncSession, acc: UUID, item_id: UUID, vec: Sequence[float]) -> None:
    model = await EmbeddingModelsRepo(session).active()
    assert model is not None
    await KnowledgeEmbeddingsRepo(session, acc).upsert(item_id, model.id, version=1, vec=list(vec))


async def build(
    session: AsyncSession,
    acc: UUID,
    t: TaskSpec | None = None,
    *,
    now: datetime = NOW,
    **kw: Any,
) -> KnowledgePack:
    return await api.build_pack(
        session, acc, t or task(), embedder=embedder(), clock=FakeClock(now), **kw
    )


__all__ = ["NOW", "UTC", "build", "embedder", "kitem", "set_vec", "task"]
