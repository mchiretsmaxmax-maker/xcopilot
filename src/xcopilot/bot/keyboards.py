"""Inline-клавиатуры бота."""

from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup


def main_menu() -> InlineKeyboardMarkup:
    raise NotImplementedError


def niche_presets() -> InlineKeyboardMarkup:
    raise NotImplementedError


def yes_no(prefix: str, yes: str = "Да", no: str = "Нет") -> InlineKeyboardMarkup:
    raise NotImplementedError


def settings_menu() -> InlineKeyboardMarkup:
    raise NotImplementedError
