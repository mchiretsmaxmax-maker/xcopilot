"""Отрисовка карточки варианта (ТЗ, этап 12, задачи 1, 3, 12; Soft «карточка»).

```
📝 Вариант 2/3 · пост · 212/280
🧪 Проверяю приём: …
Тема: …
─────────────
<текст черновика>
─────────────
Проверка: линтер ✅ · судья ✅ голос 2.6/3, AI 2.8/3
[◀] [✅ Одобрить] [✏️ Править] [▶]
[🔁 Ещё вариант] [❌ Отклонить] [❓ Почему так]
```

Разметка — HTML (aiogram parse_mode=HTML), текст черновика экранируется. Если карточка не
влезает в 4096 знаков, текст варианта уходит отдельными сообщениями перед карточкой
(`CardView.before`), а сообщение с кнопками остаётся коротким — его и редактирует навигация.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from aiogram.types import InlineKeyboardMarkup

from xcopilot.quality.policy import Decision

SEPARATOR = "─────────────"
FORMAT_NAMES = {"post": "пост", "thread": "тред", "reply": "ответ", "quote": "цитата"}


@dataclass(frozen=True)
class CardDraft:
    """Что нужно карточке о варианте (финальная ревизия после ворот)."""

    draft_id: UUID
    variant_no: int
    total: int
    format: str
    text: str
    parts: list[str] = field(default_factory=list)
    topic: str | None = None
    probation: str | None = None  # guidance испытуемого приёма (🧪)


@dataclass(frozen=True)
class CardView:
    text: str  # сообщение с кнопками (HTML)
    keyboard: InlineKeyboardMarkup | None
    before: list[str] = field(default_factory=list)  # куски длинного текста до карточки

    @property
    def messages(self) -> list[str]:
        return [*self.before, self.text]


def split_text(text: str, limit: int = 4096) -> list[str]:
    """Разбить текст на куски не длиннее limit: по абзацам, строкам, словам, иначе жёстко."""
    raise NotImplementedError


def length_label(draft: CardDraft) -> str:
    """«212/280» для поста, «4 части» для треда."""
    raise NotImplementedError


def render_card(draft: CardDraft, decision: Decision | None, *, active: bool = True) -> CardView:
    """Карточка варианта; active=False — без кнопок (карточка устарела или решена)."""
    raise NotImplementedError


@dataclass(frozen=True)
class WhyItem:
    """Знание пакета варианта и отметка применения (True / False / None — не проверено)."""

    guidance: str
    slot: str
    applied: bool | None


def render_why(items: list[WhyItem], probation: str | None = None) -> str:
    """«Почему так»: знания пакета с отметками ✅ применено, ➖ не применено, · не проверено."""
    raise NotImplementedError
