"""Интерфейсы провайдеров: текст (writer, cheap) и решения (judge)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

from xcopilot.llm.pricing import Usage
from xcopilot.llm.schemas import Answer, Question


@dataclass(frozen=True)
class PromptBlock:
    text: str
    cacheable: bool = False


@dataclass(frozen=True)
class Image:
    """Картинка в сообщении (vision): байты и MIME-тип."""

    data: bytes
    mime: str = "image/png"


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant"]
    content: str
    images: tuple[Image, ...] = ()


@dataclass(frozen=True)
class TextRequest:
    system: tuple[PromptBlock, ...]
    messages: tuple[Message, ...]
    max_tokens: int = 2048
    json_mode: bool = False


@dataclass(frozen=True)
class ProviderResult:
    text: str
    usage: Usage
    model: str


@dataclass(frozen=True)
class DecisionResult:
    answers: dict[str, Answer]
    usage: Usage
    model: str


class TextProvider(Protocol):
    name: str

    async def complete(self, *, model: str, request: TextRequest) -> ProviderResult: ...


class DecisionProvider(Protocol):
    name: str

    async def decide(
        self, *, model: str, state: str, questions: Mapping[str, Question]
    ) -> DecisionResult: ...
