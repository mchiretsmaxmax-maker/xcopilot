"""Реестр промптов: файлы с шапкой → таблица prompts, версия по хэшу содержимого."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from xcopilot.db.uow import DbSession
from xcopilot.llm.providers.base import PromptBlock
from xcopilot.llm.roles import Role

PACKAGED_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class PromptVersion:
    key: str
    role: Role
    template: str
    content_hash: str
    cacheable: tuple[str, ...] = ()
    version: int | None = None
    id: UUID | None = None


@dataclass(frozen=True)
class RenderedPrompt:
    key: str
    role: Role
    content_hash: str
    system: tuple[PromptBlock, ...]
    user: str


class PromptRegistry:
    def __init__(self, *directories: Path) -> None:
        raise NotImplementedError

    def keys(self) -> list[str]:
        raise NotImplementedError

    def get(self, key: str) -> PromptVersion:
        raise NotImplementedError

    def render(self, key: str, variables: Mapping[str, Any]) -> RenderedPrompt:
        raise NotImplementedError

    async def sync(self, session: DbSession) -> dict[str, PromptVersion]:
        raise NotImplementedError
