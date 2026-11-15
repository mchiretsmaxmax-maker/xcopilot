"""Тексты и клавиатуры «Мозга» (ТЗ v3.1, раздел 5.2) — чистые функции над данными 9C."""

from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup

from xcopilot.brain.digest import Digest


def render_digest(digest: Digest) -> tuple[str, InlineKeyboardMarkup]:
    raise NotImplementedError
