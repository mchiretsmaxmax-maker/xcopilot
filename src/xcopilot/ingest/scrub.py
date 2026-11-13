"""Вычистка персональных данных (e-mail, телефоны, номера карт) в переписках и файлах."""

from __future__ import annotations

from dataclasses import dataclass

EMAIL_MASK = "[email]"
PHONE_MASK = "[телефон]"
CARD_MASK = "[карта]"


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
    raise NotImplementedError
