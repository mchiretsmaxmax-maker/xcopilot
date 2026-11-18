"""Краткий diff двух паспортов для владельца (заглушка этапа: тесты сначала)."""

from __future__ import annotations

from xcopilot.style.passport import StylePassport


def diff_lines(old: StylePassport | None, new: StylePassport) -> list[str]:
    raise NotImplementedError
