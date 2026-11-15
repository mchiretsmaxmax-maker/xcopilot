"""Окружение операций «Мозга»: сессия, аккаунт, часы, настройки, модели, эмбеддер."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel

from xcopilot.brain.config import BrainSettings, load_brain_config
from xcopilot.clock import Clock
from xcopilot.db.uow import DbSession
from xcopilot.embeddings.embedder import Embedder
from xcopilot.llm.roles import Role
from xcopilot.llm.schemas import Question, Verdict


class BrainLLM(Protocol):
    """Часть LLMGateway, нужная «Мозгу» (generate — writer, decide — judge)."""

    async def generate[T: BaseModel](
        self,
        *,
        role: Role,
        prompt_key: str,
        variables: Mapping[str, Any],
        schema: type[T],
        account_id: UUID,
        purpose: str,
        entity: tuple[str, UUID] | None = None,
    ) -> T: ...

    async def decide(
        self,
        *,
        state: str,
        questions: Mapping[str, Question],
        account_id: UUID,
        purpose: str,
        entity: tuple[str, UUID] | None = None,
    ) -> Verdict: ...


class NoLLM:
    """Модели не подключены: любая попытка вызова — ошибка (код без моделей это не задевает)."""

    async def generate[T: BaseModel](self, **_: Any) -> T:
        raise RuntimeError("brain: модель не подключена")

    async def decide(self, **_: Any) -> Verdict:
        raise RuntimeError("brain: судья не подключён")


class NoEmbedder:
    model_name = "none"
    dim = 384

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise RuntimeError("brain: эмбеддер не подключён")


@dataclass
class BrainContext:
    session: DbSession
    account_id: UUID
    clock: Clock
    llm: BrainLLM = field(default_factory=NoLLM)
    embedder: Embedder = field(default_factory=NoEmbedder)
    config: BrainSettings = field(default_factory=load_brain_config)
