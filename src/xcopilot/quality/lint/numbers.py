"""Цифры в тексте (этап 11, `number_grounding`): извлечение, нормализация, сверка с фактами.

1.2M = 1,200,000 = 1.2 млн; проценты сверяются только с процентами. Не проверяются
(`skip`): нумерация списков («1.», «2)» в начале строки), метки треда («1/3»), годы.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

_NUM = re.compile(
    r"(?<![\w.,/])(?P<cur>[$€£])?(?P<num>\d+(?:[.,]\d{3}(?!\d))*(?:[.,]\d+)?)"
    r"(?:\s?(?P<mul>k|m|bn|b|тыс\.?|млн|млрд|трлн)(?!\w))?"
    r"(?:\s?(?P<pct>%|процент\w*|percent))?",
    re.IGNORECASE,
)
_MUL = {
    "k": Decimal(10**3),
    "тыс": Decimal(10**3),
    "m": Decimal(10**6),
    "млн": Decimal(10**6),
    "b": Decimal(10**9),
    "bn": Decimal(10**9),
    "млрд": Decimal(10**9),
    "трлн": Decimal(10**12),
}
_LIST_ITEM = re.compile(r"(?m)^[ \t]*(\d{1,2})[.)](?=\s)")


@dataclass(frozen=True)
class NumToken:
    raw: str
    value: Decimal
    unit: str = ""  # "%" — процент, "" — число
    start: int = 0
    skip: bool = False


def _parse_num(num: str) -> Decimal:
    if re.fullmatch(r"\d+(?:[.,]\d{3})+", num):  # только группы тысяч: 1,200,000
        return Decimal(re.sub(r"[.,]", "", num))
    groups = re.fullmatch(r"(\d+(?:[.,]\d{3})*)[.,](\d+)", num)
    if groups:  # 1,200.5 / 1.2 / 1,2
        return Decimal(re.sub(r"[.,]", "", groups.group(1)) + "." + groups.group(2))
    return Decimal(num)


def extract_numbers(text: str) -> list[NumToken]:
    list_starts = {m.start(1) for m in _LIST_ITEM.finditer(text)}
    out: list[NumToken] = []
    for m in _NUM.finditer(text):
        try:
            value = _parse_num(m.group("num"))
        except InvalidOperation:  # pragma: no cover - регулярка даёт только цифры
            continue
        mul = (m.group("mul") or "").lower().rstrip(".")
        if mul:
            value *= _MUL[mul]
        unit = "%" if m.group("pct") else ""
        start, end = m.span()
        thread_mark = text[end : end + 1] == "/" and text[end + 1 : end + 2].isdigit()
        plain = not (mul or unit or m.group("cur"))
        year = plain and re.fullmatch(r"(19[9]\d|20\d\d|2100)", m.group("num")) is not None
        skip = start in list_starts or thread_mark or year
        out.append(NumToken(m.group().strip(), value, unit, start, skip))
    return out


def normalize(token: NumToken | str) -> Decimal:
    if isinstance(token, NumToken):
        return token.value
    found = extract_numbers(token)
    if not found:
        raise ValueError(f"нет числа: {token!r}")
    return found[0].value


def is_grounded(token: NumToken, facts: Sequence[str], user_input: str) -> bool:
    """Число есть в фактах или во вводе пользователя (то же значение и та же единица)."""
    for source in (*facts, user_input):
        for known in extract_numbers(source):
            if known.unit == token.unit and known.value == token.value:
                return True
    return False
