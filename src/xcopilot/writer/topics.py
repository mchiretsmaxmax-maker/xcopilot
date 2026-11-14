"""Темы: предложение, дедуп, ручная тема (ТЗ, этап 10, задачи 1–2)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from uuid import UUID

from xcopilot.clock import Clock
from xcopilot.db.uow import UnitOfWork
from xcopilot.embeddings.embedder import Embedder
from xcopilot.llm.gateway import LLMGateway
from xcopilot.retrieval.context_pack import KnowledgeProvider
from xcopilot.writer.schemas import Topic, TopicIdea
from xcopilot.writer.similarity import TOPIC_MAX

PROMPT_KEY = "writer.topics.v1"


def dedupe_topics(
    ideas: Sequence[TopicIdea],
    vectors: Sequence[Sequence[float]],
    recent: Sequence[Sequence[float]],
    threshold: float = TOPIC_MAX,
) -> list[int]:
    raise NotImplementedError


class TopicService:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        gateway: LLMGateway,
        embedder: Embedder,
        clock: Clock,
        knowledge: KnowledgeProvider | None = None,
    ) -> None:
        self._uow = uow_factory
        self._gateway = gateway
        self._embedder = embedder
        self._clock = clock
        self._knowledge = knowledge

    async def suggest_topics(self, account_id: UUID, n: int = 5) -> list[Topic]:
        raise NotImplementedError

    async def add_manual_topic(
        self, account_id: UUID, title: str, *, angle: str | None = None, fmt: str = "post"
    ) -> Topic:
        raise NotImplementedError
