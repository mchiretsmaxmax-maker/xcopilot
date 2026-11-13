"""Реестр промптов: файлы с шапкой → таблица prompts, версия по хэшу содержимого.

Формат файла `*.md`:

    ---
    key: writer.post
    role: writer
    cacheable: passport, lessons     # необязательно
    ---
    Системная часть с {{ переменными }}.
    @@user
    Пользовательская часть с {{ переменными }}.

Без строки `@@user` весь текст — пользовательская часть. Переменные из `cacheable` в текст не
подставляются: каждая становится отдельным системным блоком `<имя>…</имя>`, помеченным для
кэша провайдера (паспорт и уроки меняются редко, а платятся каждый вызов). Значения
подставляются за один проход и повторно не рендерятся. Хэш — sha256 всего файла; новая
версия в БД появляется только при новом хэше, старые строки не меняются.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from uuid import UUID

from xcopilot.db.repos import PromptsRepo
from xcopilot.db.uow import DbSession
from xcopilot.errors import ConfigError
from xcopilot.llm.errors import PromptNotFound, PromptVariableMissing
from xcopilot.llm.providers.base import PromptBlock
from xcopilot.llm.roles import Role

PACKAGED_DIR = Path(__file__).resolve().parent
USER_MARKER = re.compile(r"^@@user[ \t]*$", re.M)
PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


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


def parse_prompt(text: str, source: str = "<prompt>") -> PromptVersion:
    """Разобрать файл промпта: шапка между строками `---`, затем шаблон."""
    if not text.startswith("---\n"):
        raise ConfigError(f"{source}: промпт должен начинаться с шапки ---")
    header, sep, body = text[4:].partition("\n---\n")
    if not sep:
        raise ConfigError(f"{source}: шапка не закрыта строкой ---")
    fields: dict[str, str] = {}
    for line in header.splitlines():
        if line.strip():
            name, _, value = line.partition(":")
            fields[name.strip()] = value.strip()
    if not fields.get("key") or not fields.get("role"):
        raise ConfigError(f"{source}: в шапке нужны key и role")
    cacheable = tuple(v.strip() for v in fields.get("cacheable", "").split(",") if v.strip())
    return PromptVersion(
        key=fields["key"],
        role=Role(fields["role"]),
        template=body,
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
        cacheable=cacheable,
    )


def _fill(part: str, variables: Mapping[str, Any]) -> str:
    return PLACEHOLDER.sub(lambda m: str(variables[m.group(1)]), part).strip()


class PromptRegistry:
    """Промпты из каталогов (по умолчанию — встроенные); позже указанный каталог главнее."""

    def __init__(self, *directories: Path) -> None:
        self._prompts: dict[str, PromptVersion] = {}
        for directory in directories or (PACKAGED_DIR,):
            for path in sorted(directory.glob("*.md")):
                version = parse_prompt(path.read_text(), str(path))
                self._prompts[version.key] = version

    def keys(self) -> list[str]:
        return sorted(self._prompts)

    def get(self, key: str) -> PromptVersion:
        try:
            return self._prompts[key]
        except KeyError:
            raise PromptNotFound(f"промпт {key!r} не найден") from None

    def render(self, key: str, variables: Mapping[str, Any]) -> RenderedPrompt:
        """Подставить переменные; нехватка любой — PromptVariableMissing со списком имён."""
        version = self.get(key)
        needed = set(PLACEHOLDER.findall(version.template)) | set(version.cacheable)
        if missing := sorted(needed - variables.keys()):
            raise PromptVariableMissing(f"{key}: не хватает переменных {', '.join(missing)}")
        system_part, marker, user_part = version.template.partition("\n@@user\n")
        if not marker:
            match = USER_MARKER.search(version.template)
            if match is not None:
                system_part = version.template[: match.start()]
                user_part = version.template[match.end() :]
            else:
                system_part, user_part = "", version.template
        cached = bool(version.cacheable)
        blocks: list[PromptBlock] = []
        if system_text := _fill(system_part, variables):
            blocks.append(PromptBlock(system_text, cacheable=cached))
        for name in version.cacheable:
            blocks.append(PromptBlock(f"<{name}>\n{variables[name]}\n</{name}>", cacheable=True))
        return RenderedPrompt(
            key=key,
            role=version.role,
            content_hash=version.content_hash,
            system=tuple(blocks),
            user=_fill(user_part, variables),
        )

    async def sync(self, session: DbSession) -> dict[str, PromptVersion]:
        """Записать в prompts новые версии; вернуть все промпты с номером версии и id."""
        repo = PromptsRepo(session)
        synced: dict[str, PromptVersion] = {}
        for key, version in self._prompts.items():
            row = await repo.find(key, version.content_hash)
            if row is None:
                row = await repo.add(
                    key=key,
                    version=await repo.last_version(key) + 1,
                    role=version.role.value,
                    template=version.template,
                    content_hash=version.content_hash,
                )
            synced[key] = replace(version, version=row.version, id=row.id)
        return synced
