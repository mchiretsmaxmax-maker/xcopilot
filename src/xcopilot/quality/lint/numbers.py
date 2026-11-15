"""Цифры в тексте (этап 11, `number_grounding`): извлечение, нормализация, сверка с фактами.

1.2M = 1,200,000 = 1.2 млн; проценты сверяются только с процентами; нумерация списков,
годы и маленькие счётные числа без единиц не проверяются.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class NumToken:
    raw: str
    value: Decimal
    unit: str = ""  # "%" — процент, "" — число
    start: int = 0


def extract_numbers(text: str) -> list[NumToken]:
    raise NotImplementedError


def normalize(token: NumToken | str) -> Decimal:
    raise NotImplementedError


def is_grounded(token: NumToken, facts: Sequence[str], user_input: str) -> bool:
    raise NotImplementedError
