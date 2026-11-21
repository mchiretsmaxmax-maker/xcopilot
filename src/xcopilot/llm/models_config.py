"""Роли → провайдер и модель (config/models.toml). Код моделей не знает."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

from xcopilot.errors import ConfigError
from xcopilot.llm.roles import Role
from xcopilot.llm.settings_paths import config_dir

DEFAULT_MODELS_PATH = config_dir() / "models.toml"
PROVIDERS = frozenset({"codex_cli", "deepseek", "jev"})
# Разрешённые замены: судья → дешёвая модель с той же схемой. Писатель замены не имеет.
ALLOWED_FALLBACKS = {Role.JUDGE: frozenset({Role.CHEAP})}


@dataclass(frozen=True)
class RoleConfig:
    provider: str
    model: str
    max_tokens: int = 2048
    thinking: bool | None = None  # режим рассуждения (DeepSeek); None — не передаётся
    # назначение вызова → своя модель того же провайдера (например судья ответов — сильнее)
    purpose_models: tuple[tuple[str, PurposeModel], ...] = field(default=())

    def for_purpose(self, purpose: str) -> RoleConfig:
        """Конфиг роли под назначение вызова: своя модель из `purpose_models` или как есть."""
        own = dict(self.purpose_models).get(purpose)
        if own is None:
            return self
        return replace(
            self,
            model=own.model,
            thinking=own.thinking if own.thinking is not None else self.thinking,
            max_tokens=own.max_tokens or self.max_tokens,
            purpose_models=(),
        )


@dataclass(frozen=True)
class PurposeModel:
    model: str
    thinking: bool | None = None
    max_tokens: int | None = None


def _purpose_model(role: str, purpose: str, raw: object) -> PurposeModel:
    if isinstance(raw, str):
        return PurposeModel(model=raw)
    if isinstance(raw, dict) and "model" in raw:
        thinking = raw.get("thinking")
        max_tokens = raw.get("max_tokens")
        return PurposeModel(
            model=str(raw["model"]),
            thinking=bool(thinking) if thinking is not None else None,
            max_tokens=int(max_tokens) if max_tokens is not None else None,
        )
    raise ConfigError(f"models.toml: роль {role}: purpose_models.{purpose} — строка или таблица")


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
            by_purpose = raw.get("purpose_models", {})
            if not isinstance(by_purpose, dict):
                raise ConfigError(f"models.toml: роль {name}: purpose_models — таблица")
            roles[role] = RoleConfig(
                provider=raw["provider"],
                model=str(raw["model"]),
                max_tokens=int(raw.get("max_tokens", 2048)),
                thinking=bool(raw["thinking"]) if "thinking" in raw else None,
                purpose_models=tuple(
                    (str(k), _purpose_model(name, str(k), v)) for k, v in by_purpose.items()
                ),
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
