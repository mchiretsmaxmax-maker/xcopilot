"""Карточка разбора в боте (ТЗ v3.1, раздел 5.2; этап 7A)."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from aiogram.types import InlineKeyboardMarkup

from xcopilot.bot import keyboards, texts


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
    auto_outcome: str | None = None  # «Зашёл: 🔥 (…, по цифрам)» — исход по публичным цифрам


def render_card(view: CardView) -> tuple[str, InlineKeyboardMarkup]:
    """Текст и кнопки карточки. Исход известен (цифры) — вопроса «как зашёл» нет;
    исход по публичным цифрам показывается строкой «Зашёл: …» с кнопкой «Поправить»."""
    head = f"🧩 Разбор · {texts.MATERIAL_FORMATS.get(view.format, view.format)}"
    if view.author_handle:
        head += f" · @{view.author_handle}"
    lines = [head]
    if view.hook_title:
        hook = f"Хук: {view.hook_title}"
        if view.hook_text:
            hook += f" — «{view.hook_text}»"
        lines.append(hook)
    if view.structure_title:
        n = view.line_count
        lines.append(f"Структура: {view.structure_title}, {n} {texts.material_lines_word(n)}")
    if view.device_titles:
        lines.append("Приёмы: " + ", ".join(view.device_titles))
    if view.why:
        lines.append(f"Почему могло зайти (гипотеза): {view.why}")
    if view.matched_knowledge or view.new_observations:
        lines.append(
            f"Совпало с {view.matched_knowledge} знаниями · "
            f"новых наблюдений: {view.new_observations}"
        )
    if view.auto_outcome:
        lines += ["", view.auto_outcome]
    elif not view.outcome_known:
        lines += ["", texts.MATERIAL_ASK_OUTCOME]
    keyboard = keyboards.material_card(
        str(view.source_id),
        ask_outcome=not view.outcome_known and not view.auto_outcome,
        fix_outcome=bool(view.auto_outcome),
    )
    return "\n".join(lines), keyboard
