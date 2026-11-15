"""Callback-данные карточек: `c1:<черновик>:<действие>:<аргумент>`, до 64 байт.

Префикс `c1` — версия схемы: старые кнопки после смены схемы не разбираются как новые.
ID черновика — 22 знака base64url (UUID без потерь), чтобы с аргументом (номер варианта,
причина, ID поста X до 20 цифр) влезть в предел Telegram.
"""

from __future__ import annotations

from uuid import UUID

from aiogram.filters.callback_data import CallbackData

# действия карточки
NAV = "nav"  # arg — номер варианта
APPROVE = "ok"
EDIT = "ed"
MORE = "more"  # подсказки «Ещё вариант»
REGEN = "rg"  # arg — код подсказки
REJECT = "no"  # причины отказа
REJECT_REASON = "rj"  # arg — код причины
WHY = "why"
COPY = "cp"
PUBLISHED = "pub"  # «✔️ Опубликовал»
FIND = "find"  # «Найди сам»
MATCH_YES = "my"  # arg — ID поста X
MATCH_NO = "mn"
SHOW = "show"  # показать карточку из очереди

REGEN_HINTS = {"short": "Короче", "hard": "Жёстче", "angle": "Другой заход"}
REGEN_OWN = "own"  # свой текст подсказки
REJECT_REASONS = {
    "not_my_voice": "Не мой голос",
    "off_topic": "Тема мимо",
    "sounds_ai": "Звучит как AI",
    "wrong_fact": "Факт неверный",
    "other": "Другое",
}


class CardCb(CallbackData, prefix="c1"):
    draft: str
    action: str
    arg: str = ""


def short_id(value: UUID) -> str:
    """UUID → 22 знака base64url."""
    raise NotImplementedError


def from_short(value: str) -> UUID:
    """22 знака base64url → UUID; мусор — ValueError."""
    raise NotImplementedError


def card_cb(draft_id: UUID, action: str, arg: str = "") -> str:
    """Упакованные callback-данные; длиннее 64 байт — ValueError."""
    raise NotImplementedError
