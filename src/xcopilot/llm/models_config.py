"""Роли → провайдер и модель (config/models.toml). Код моделей не знает."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from xcopilot.errors import ConfigError
from xcopilot.llm.roles import Role
from xcopilot.llm.settings_paths import config_dir

DEFAULT_MODELS_PATH = config_dir() / "models.toml"
PROVIDERS = frozenset({"anthropic", "deepseek", "jev"})
# Разрешённые замены: судья → дешёвая модель с той же схемой. Писатель замены не имеет.
ALLOWED_FALLBACKS = {Role.JUDGE: frozenset({Role.CHEAP})}


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
        path = path or config_dir() / "models.toml"
        data = tomllib.loads(path.read_text())
        roles: dict[Role, RoleConfig] = {}
        for name, raw in data.get("roles", {}).items():
            role = Role(name)
            if raw.get("provider") not in PROVIDERS:
                raise ConfigError(f"models.toml: роль {name}: неизвестный провайдер")
            roles[role] = RoleConfig(
                provider=raw["provider"],
                model=str(raw["model"]),
                max_tokens=int(raw.get("max_tokens", 2048)),
            )
        if missing := set(Role) - roles.keys():
            raise ConfigError(f"models.toml: нет ролей {sorted(missing)}")
        fallback: dict[Role, Role] = {}
        for name, target in data.get("fallback", {}).items():
            role, to = Role(name), Role(target)
            if to not in ALLOWED_FALLBACKS.get(role, frozenset()):
                raise ConfigError(f"models.toml: замена {name} → {target} запрещена")
            fallback[role] = to
        return cls(roles=roles, fallback=fallback)
