"""Роли → провайдер и модель (config/models.toml)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from xcopilot.llm.roles import Role

DEFAULT_MODELS_PATH = Path("config/models.toml")


@dataclass(frozen=True)
class RoleConfig:
    provider: str
    model: str
    max_tokens: int = 2048


@dataclass(frozen=True)
class ModelsConfig:
    roles: dict[Role, RoleConfig]
    fallback: dict[Role, Role]

    @classmethod
    def load(cls, path: Path | None = None) -> ModelsConfig:
        raise NotImplementedError
