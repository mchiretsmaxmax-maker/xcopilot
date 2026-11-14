"""Генерация черновиков: три варианта в голосе аккаунта с пакетом и знаниями (этап 10)."""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from xcopilot.clock import Clock
from xcopilot.db.uow import UnitOfWork
from xcopilot.embeddings.embedder import Embedder
from xcopilot.llm.gateway import LLMGateway
from xcopilot.llm.prompts.registry import PromptRegistry
from xcopilot.retrieval.context_pack import KnowledgeProvider, TaskSpec
from xcopilot.writer.probation import ProbationPicker
from xcopilot.writer.schemas import GenerationRun, TargetPost
from xcopilot.writer.similarity import ensure_distinct
from xcopilot.writer.trend_tail import PostRef

DRAFT_PROMPT = "writer.draft.v1"
TAIL_PROMPT = "writer.trend_tail.write.v1"

__all__ = ["DRAFT_PROMPT", "TAIL_PROMPT", "WriterInputError", "WriterService", "ensure_distinct"]


class WriterInputError(ValueError):
    """Задача писателю составлена неверно (например, ответ без целевого поста)."""


class WriterService:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        gateway: LLMGateway,
        embedder: Embedder,
        clock: Clock,
        knowledge: KnowledgeProvider | None = None,
        probation: ProbationPicker | None = None,
        prompts: PromptRegistry | None = None,
    ) -> None:
        self._uow = uow_factory
        self._gateway = gateway
        self._embedder = embedder
        self._clock = clock
        self._knowledge = knowledge
        self._probation = probation
        self._prompts = prompts

    async def generate(
        self,
        account_id: UUID,
        task: TaskSpec,
        n: int = 3,
        hint: str | None = None,
        *,
        topic_id: UUID | None = None,
        target: TargetPost | None = None,
        job_id: UUID | None = None,
    ) -> GenerationRun:
        raise NotImplementedError

    async def generate_tail(
        self,
        account_id: UUID,
        post_ref: PostRef,
        own_topic: str | None = None,
        *,
        n: int = 3,
        job_id: UUID | None = None,
    ) -> GenerationRun:
        raise NotImplementedError
