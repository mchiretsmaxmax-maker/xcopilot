"""Знания класса C для линтера (ТЗ v3.1, этап 11, задача 13): мягкие запреты → предупреждения."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from xcopilot.db.uow import DbSession
from xcopilot.quality.lint.engine import SoftRule


class ClassCRules(Protocol):
    async def load(
        self, session: DbSession, account_id: UUID, lang: str | None
    ) -> list[SoftRule]: ...


class NoClassC:
    async def load(self, session: DbSession, account_id: UUID, lang: str | None) -> list[SoftRule]:
        return []


class DbClassCRules:
    """Рабочие знания класса C аккаунта и общего аккаунта: фразы в «…» / "…" из утверждения."""

    async def load(self, session: DbSession, account_id: UUID, lang: str | None) -> list[SoftRule]:
        raise NotImplementedError


def phrases_of(statement: str) -> tuple[str, ...]:
    raise NotImplementedError
