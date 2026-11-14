"""Проверка материала на входе (ТЗ v3.1, этап 7): допуск или карантин."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from xcopilot.llm.schemas import Question, Verdict


class JudgeLike(Protocol):
    async def decide(
        self,
        *,
        state: str,
        questions: Mapping[str, Question],
        account_id: UUID,
        purpose: str,
        entity: tuple[str, UUID] | None = None,
    ) -> Verdict: ...


@dataclass(frozen=True, slots=True)
class ScreenVerdict:
    admit: bool
    reason: str | None = None
    by: str = "judge"


class Screener:
    def __init__(self, judge: JudgeLike) -> None:
        self._judge = judge

    async def screen(
        self, text: str, *, account_id: UUID, entity: tuple[str, UUID] | None = None
    ) -> ScreenVerdict:
        raise NotImplementedError
