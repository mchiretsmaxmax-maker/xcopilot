"""Пороги этапа 12: срок карточки, окно и пороги автосверки, дневной предел карточек."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

TG_TEXT_LIMIT = 4096  # предел текста одного сообщения Telegram
CALLBACK_LIMIT = 64  # предел callback_data в байтах


@dataclass(frozen=True)
class ReviewSettings:
    card_ttl: timedelta = timedelta(hours=48)  # карточка старше — expired, кнопки сняты
    match_window: timedelta = timedelta(hours=48)  # окно автосверки после решения
    auto_match: float = 0.85  # от этого сходства — публикация найдена сама
    ask_match: float = 0.60  # от этого до auto_match — вопрос владельцу
    daily_card_limit: int = 10  # карточек (запусков) в день; сверх — в /queue
