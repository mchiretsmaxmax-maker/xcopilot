"""Движок линтера (ТЗ, этап 11, задача 1): правило = класс с id, уровнем и методом проверки.

`Linter.run(draft, ctx)` прогоняет правила по порядку. Правило с автоисправлением (`fix`)
сначала правит текст (регистр, хэштеги) — находка остаётся в отчёте с пометкой `autofixed`
и ошибкой уже не считается. Упавшее правило не роняет линтер: в отчёт идёт предупреждение
`internal`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, Field

from xcopilot.style.passport import HardRules

Level = Literal["error", "warn"]


class Finding(BaseModel):
    """Одна находка: правило, уровень, что не так и цитата найденного места."""

    rule: str
    level: Level
    message: str
    fragment: str = ""
    autofixed: bool = False


@dataclass(frozen=True)
class LintDraft:
    """Что проверяется: текст, формат и части треда."""

    text: str
    format: str = "post"
    parts: tuple[str, ...] = ()


@dataclass(frozen=True)
class ForeignText:
    """Чужой материал базы (stance ≠ own) — для `foreign_similarity`."""

    text: str
    source_id: UUID | None = None


@dataclass(frozen=True)
class SoftRule:
    """Знание класса C как мягкий запрет: фразы, при которых линтер предупреждает."""

    item_id: UUID
    statement: str
    phrases: tuple[str, ...]


@dataclass(frozen=True)
class LintContext:
    """Всё, с чем сверяется черновик; собирается петлёй ворот из БД."""

    hard_rules: HardRules = field(default_factory=HardRules)
    global_banned: tuple[str, ...] = ()
    lang: str | None = None
    facts: tuple[str, ...] = ()
    user_input: str = ""
    own_recent: tuple[str, ...] = ()
    foreign: tuple[ForeignText, ...] = ()
    class_c: tuple[SoftRule, ...] = ()


@runtime_checkable
class Rule(Protocol):
    id: str
    level: Level

    def check(self, draft: LintDraft, ctx: LintContext) -> list[Finding]: ...


@runtime_checkable
class FixableRule(Rule, Protocol):
    def fix(self, draft: LintDraft, ctx: LintContext) -> LintDraft | None: ...


class LintReport(BaseModel):
    """Итог линтера: находки, исправленные правила и текст после автоисправлений."""

    text: str
    parts: list[str] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    autofixed: list[str] = Field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "error" and not f.autofixed]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "warn" and not f.autofixed]

    @property
    def ok(self) -> bool:
        return not self.errors


class Linter:
    def __init__(self, rules: Sequence[Rule] | None = None) -> None:
        raise NotImplementedError

    @property
    def rules(self) -> list[Rule]:
        raise NotImplementedError

    def run(self, draft: LintDraft, ctx: LintContext) -> LintReport:
        raise NotImplementedError


def default_rules() -> list[Rule]:
    """Правила v1 в порядке проверки (сначала исправляемые)."""
    raise NotImplementedError


__all__ = [
    "Finding",
    "FixableRule",
    "ForeignText",
    "Level",
    "LintContext",
    "LintDraft",
    "LintReport",
    "Linter",
    "Rule",
    "SoftRule",
    "default_rules",
]
