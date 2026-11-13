"""Клавиатуры бота: подписи и callback_data."""

from aiogram.types import InlineKeyboardMarkup

from xcopilot.bot import keyboards


def _labels(markup: InlineKeyboardMarkup) -> list[str]:
    return [b.text for row in markup.inline_keyboard for b in row]


def _data(markup: InlineKeyboardMarkup) -> list[str]:
    return [b.callback_data or "" for row in markup.inline_keyboard for b in row]


def test_main_menu_has_all_sections() -> None:
    assert _labels(keyboards.main_menu()) == [
        "✍️ Написать",
        "💡 Темы",
        "📥 Обучить",
        "🎯 Хвост тренда",
        "📊 Аналитика",
        "🎨 Стиль",
        "⚙️ Настройки",
    ]


def test_niche_presets() -> None:
    assert _labels(keyboards.niche_presets()) == ["Крипта", "ИИ", "Финансы", "Своё"]
    assert _data(keyboards.niche_presets()) == [
        "niche:crypto",
        "niche:ai",
        "niche:finance",
        "niche:custom",
    ]


def test_yes_no_uses_prefix_and_labels() -> None:
    kb = keyboards.yes_no("confirm", "Всё верно", "Изменить")
    assert _labels(kb) == ["Всё верно", "Изменить"]
    assert _data(kb) == ["confirm:yes", "confirm:no"]
    assert _labels(keyboards.yes_no("x")) == ["Да", "Нет"]


def test_settings_menu() -> None:
    assert _labels(keyboards.settings_menu()) == [
        "Часовой пояс",
        "Язык постов",
        "Дневные лимиты",
        "Доступ к X",
    ]


def test_callback_data_fits_telegram_limit() -> None:
    for kb in (keyboards.main_menu(), keyboards.niche_presets(), keyboards.settings_menu()):
        assert all(len(d.encode()) <= 64 for d in _data(kb))
