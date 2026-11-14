"""Карточка разбора в боте (ТЗ v3.1, раздел 5.2; этап 7A)."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from aiogram.types import InlineKeyboardMarkup


@dataclass(frozen=True)
class CardView:
    source_id: UUID
    format: str
    author_handle: str | None
    hook_title: str | None
    hook_text: str
    structure_title: str | None
    line_count: int
    device_titles: list[str] = field(default_factory=list)
    why: str | None = None
    matched_knowledge: int = 0
    new_observations: int = 0
    outcome_known: bool = False


def render_card(view: CardView) -> tuple[str, InlineKeyboardMarkup]:
    raise NotImplementedError
