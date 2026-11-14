"""Показ паспорта в Telegram и экспорт в .md."""

from __future__ import annotations

from xcopilot.style.passport import StylePassport

TG_LIMIT = 4096


def export_md(passport: StylePassport, version: int | None = None) -> str:
    raise NotImplementedError


def render(passport: StylePassport, version: int | None = None) -> list[str]:
    raise NotImplementedError
