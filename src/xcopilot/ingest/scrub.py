"""Вычистка персональных данных (e-mail, телефоны, номера карт) в переписках и файлах.

Свои посты не чистятся: хэндлы (@name) и тикеры ($ETH) — часть стиля, а не персональные данные.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

EMAIL_MASK = "[email]"
PHONE_MASK = "[телефон]"
CARD_MASK = "[карта]"

_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")
# 13–19 цифр группами по 4 (или слитно), разделители — пробел или дефис
_CARD = re.compile(r"(?<![\w$])(?:\d{4}[ -]?){3}\d{1,7}(?![\w])")
# телефон: обязательно «+код» или ведущая 8, затем 10 цифр с разделителями
_PHONE = re.compile(
    r"(?<![\w$+])(?:\+\d{1,3}|8)[ -]?\(?\d{3}\)?[ -]?\d{3}[ -]?\d{2}[ -]?\d{2}(?![\w])"
)


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


@dataclass(frozen=True, slots=True)
class ScrubResult:
    text: str
    emails: int = 0
    phones: int = 0
    cards: int = 0

    @property
    def total(self) -> int:
        return self.emails + self.phones + self.cards


def scrub_pii(text: str) -> ScrubResult:
    """Заменить e-mail, телефоны и номера карт (проверка Луна) заглушками; посчитать замены."""
    counts = {"email": 0, "phone": 0, "card": 0}

    def card(m: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            counts["card"] += 1
            return CARD_MASK
        return m.group(0)

    def sub(kind: str, mask: str) -> Callable[[re.Match[str]], str]:
        def repl(_: re.Match[str]) -> str:
            counts[kind] += 1
            return mask

        return repl

    out = _EMAIL.sub(sub("email", EMAIL_MASK), text)
    out = _CARD.sub(card, out)
    out = _PHONE.sub(sub("phone", PHONE_MASK), out)
    return ScrubResult(out, emails=counts["email"], phones=counts["phone"], cards=counts["card"])
