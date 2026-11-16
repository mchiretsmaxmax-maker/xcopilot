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

import html
from dataclasses import dataclass, field
from uuid import UUID

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from xcopilot.bot import texts
from xcopilot.quality.policy import Decision
from xcopilot.quality.render import gate_line
from xcopilot.review import callbacks as cb
from xcopilot.review.callbacks import card_cb
from xcopilot.review.settings import TG_TEXT_LIMIT
from xcopilot.x.intents import MAX_WEIGHTED_LEN, weighted_len

SEPARATOR = "─────────────"
FORMAT_NAMES = {"post": "пост", "thread": "тред", "reply": "ответ", "quote": "цитата"}
NO_CHECK = "Проверка: нет данных ворот"
TEXT_ABOVE = "Текст варианта — в сообщениях выше ⬆️"
RU_UNAVAILABLE = "🇷🇺 перевод недоступен"


def _esc(value: str) -> str:
    return html.escape(value, quote=False)


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
    ru: list[str] | None = None  # русский перевод: по блоку на часть (пост — один блок)
    ru_unavailable: bool = False  # перевод нужен, но cheap недоступна → пометка в карточке


@dataclass(frozen=True)
class CardView:
    text: str  # сообщение с кнопками (HTML)
    keyboard: InlineKeyboardMarkup | None
    before: list[str] = field(default_factory=list)  # куски длинного текста до карточки

    @property
    def messages(self) -> list[str]:
        return [*self.before, self.text]


def _measure(chunk: str) -> int:
    return len(html.escape(chunk, quote=False))


def _pieces(text: str, sep: str) -> list[str]:
    parts = text.split(sep)
    return [p + sep for p in parts[:-1]] + [parts[-1]]


def split_text(text: str, limit: int = TG_TEXT_LIMIT) -> list[str]:
    """Разбить текст на куски, чьё HTML-экранирование не длиннее limit.

    Сначала по абзацам, затем по строкам и словам, иначе — по символам. Кусок режется только
    по исходному тексту, поэтому экранированные сущности (&amp; …) не разрываются.
    """
    if _measure(text) <= limit:
        return [text]
    for sep in ("\n\n", "\n", " "):
        pieces = _pieces(text, sep)
        if len(pieces) > 1:
            break
    else:
        pieces = list(text)
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if _measure(piece) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(split_text(piece, limit))
            continue
        if _measure(current + piece) > limit:
            chunks.append(current)
            current = piece
        else:
            current += piece
    if current:
        chunks.append(current)
    return [c for c in chunks if c.strip()] or [text[:limit]]


def _plural_parts(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} часть"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} части"
    return f"{n} частей"


def is_threaded(draft: CardDraft) -> bool:
    """Несколько частей (тред или длинный пост, нарезанный кодом)."""
    return len(draft.parts) > 1 or (draft.format == "thread" and bool(draft.parts))


def length_label(draft: CardDraft) -> str:
    """«212/280» для короткого поста, «4 части» для треда."""
    if is_threaded(draft):
        return _plural_parts(len(draft.parts))
    return f"{weighted_len(draft.text)}/{MAX_WEIGHTED_LEN}"


def body_text(draft: CardDraft) -> str:
    """Текст варианта как его видит владелец: части с номерами «1/4 · 250/280»."""
    if is_threaded(draft):
        n = len(draft.parts)
        return "\n\n".join(
            f"{i}/{n} · {weighted_len(p)}/{MAX_WEIGHTED_LEN}\n{p}"
            for i, p in enumerate(draft.parts, 1)
        )
    return draft.text


def ru_text(draft: CardDraft) -> str:
    """Перевод для чтения: у треда части с номерами «1/4 …»."""
    ru = draft.ru or []
    if is_threaded(draft) and len(ru) > 1:
        return "\n\n".join(f"{i}/{len(ru)} {p}" for i, p in enumerate(ru, 1))
    return "\n\n".join(ru)


def _quote(text: str) -> str:
    return f"<blockquote expandable>{_esc(text)}</blockquote>"


def card_keyboard(draft: CardDraft) -> InlineKeyboardMarkup:
    d = draft.draft_id
    first: list[InlineKeyboardButton] = [
        InlineKeyboardButton(text="✅ Одобрить", callback_data=card_cb(d, cb.APPROVE)),
        InlineKeyboardButton(text="✏️ Править", callback_data=card_cb(d, cb.EDIT)),
    ]
    if draft.total > 1:
        prev_no = (draft.variant_no - 2) % draft.total + 1
        next_no = draft.variant_no % draft.total + 1
        first.insert(
            0, InlineKeyboardButton(text="◀", callback_data=card_cb(d, cb.NAV, str(prev_no)))
        )
        first.append(InlineKeyboardButton(text="▶", callback_data=card_cb(d, cb.NAV, str(next_no))))
    second = [
        InlineKeyboardButton(text="🔁 Ещё вариант", callback_data=card_cb(d, cb.MORE)),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=card_cb(d, cb.REJECT)),
        InlineKeyboardButton(text="❓ Почему так", callback_data=card_cb(d, cb.WHY)),
    ]
    return InlineKeyboardMarkup(inline_keyboard=[first, second])


def render_card(draft: CardDraft, decision: Decision | None, *, active: bool = True) -> CardView:
    """Карточка варианта; active=False — без кнопок (карточка устарела или решена)."""
    kind = "thread" if is_threaded(draft) else draft.format
    fmt = FORMAT_NAMES.get(kind, kind)
    head = [f"📝 Вариант {draft.variant_no}/{draft.total} · {fmt} · {length_label(draft)}"]
    if draft.probation:
        head.append(f"🧪 Проверяю приём: {_esc(draft.probation)}")
    if draft.topic:
        head.append(f"Тема: {_esc(draft.topic)}")
    check = _esc(gate_line(decision)) if decision is not None else NO_CHECK
    body = body_text(draft)
    keyboard = card_keyboard(draft) if active else None
    ru = ru_text(draft) if draft.ru else ""
    note = _esc_note(draft)
    tail = [check, *([note] if note else [])]
    full = "\n".join(
        [*head, SEPARATOR, _esc(body), *([_quote(ru)] if ru else []), SEPARATOR, *tail]
    )
    if len(full) <= TG_TEXT_LIMIT:
        return CardView(text=full, keyboard=keyboard)
    before = [_esc(chunk) for chunk in split_text(body)]
    before += [_quote(chunk) for chunk in split_text(ru)] if ru else []
    short = "\n".join([*head, SEPARATOR, TEXT_ABOVE, SEPARATOR, *tail])
    return CardView(text=short, keyboard=keyboard, before=before)


def _esc_note(draft: CardDraft) -> str:
    return RU_UNAVAILABLE if draft.ru_unavailable and not draft.ru else ""


@dataclass(frozen=True)
class WhyItem:
    """Знание пакета варианта и отметка применения (True / False / None — не проверено)."""

    guidance: str
    slot: str
    applied: bool | None


_MARKS = {True: "✅", False: "➖", None: "·"}


def render_why(items: list[WhyItem], probation: str | None = None) -> str:
    """«Почему так»: знания пакета с отметками ✅ применено, ➖ не применено, · не проверено."""
    if not items and not probation:
        return texts.WHY_CARD_EMPTY
    lines = [texts.WHY_CARD_TITLE]
    lines += [f"{_MARKS[i.applied]} {_esc(i.guidance)} · {i.slot}" for i in items]
    if probation:
        lines.append(f"🧪 Испытание: {_esc(probation)}")
    lines.append("✅ применено · ➖ не применено · · не проверено")
    return "\n".join(lines)
