"""Inline-клавиатуры бота."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def _kb(*rows: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text, callback_data=data) for text, data in row]
            for row in rows
        ]
    )


def main_menu() -> InlineKeyboardMarkup:
    return _kb(
        [("✍️ Написать", "menu:write"), ("💡 Темы", "menu:topics")],
        [("📥 Обучить", "menu:learn"), ("🎯 Хвост тренда", "menu:trend")],
        [("📊 Аналитика", "menu:analytics"), ("🎨 Стиль", "menu:style")],
        [("⚙️ Настройки", "menu:settings")],
    )


def niche_presets() -> InlineKeyboardMarkup:
    return _kb(
        [("Крипта", "niche:crypto"), ("ИИ", "niche:ai")],
        [("Финансы", "niche:finance"), ("Своё", "niche:custom")],
    )


def yes_no(prefix: str, yes: str = "Да", no: str = "Нет") -> InlineKeyboardMarkup:
    return _kb([(yes, f"{prefix}:yes"), (no, f"{prefix}:no")])


def lang_choice(prefix: str) -> InlineKeyboardMarkup:
    return _kb([("English", f"{prefix}:en"), ("Русский", f"{prefix}:ru")])


def single(label: str, data: str) -> InlineKeyboardMarkup:
    return _kb([(label, data)])


def settings_menu() -> InlineKeyboardMarkup:
    return _kb(
        [("Часовой пояс", "set:tz"), ("Язык постов", "set:lang")],
        [("Дневные лимиты", "set:limits"), ("Доступ к X", "xc:open")],
    )


def limits_menu() -> InlineKeyboardMarkup:
    return _kb([("Лимит моделей", "setlim:llm"), ("Лимит X", "setlim:x")])


def x_access_menu() -> InlineKeyboardMarkup:
    return _kb(
        [("Выключен", "xc:off")],
        [("Только мой аккаунт", "xc:owned")],
        [("Платное чтение", "xc:paid")],
    )


def x_paid_confirm() -> InlineKeyboardMarkup:
    return _kb([("Включить", "xc:paid_yes"), ("Отмена", "xc:cancel")])


def budget_actions() -> InlineKeyboardMarkup:
    return _kb([("Поднять на сегодня", "budget:raise"), ("Ждать до завтра", "budget:wait")])
