"""Клавиатуры бота: подписи и callback_data."""

from aiogram.types import InlineKeyboardMarkup

from xcopilot.bot import keyboards


def _labels(markup: InlineKeyboardMarkup) -> list[str]:
    return [b.text for row in markup.inline_keyboard for b in row]


def _data(markup: InlineKeyboardMarkup) -> list[str]:
    return [b.callback_data or "" for row in markup.inline_keyboard for b in row]


def test_main_menu_has_all_sections() -> None:
    """Главное меню теперь = нижняя клавиатура + «⚙️ Ещё»; здесь — состав «Ещё»."""
    assert _labels(keyboards.more_menu()) == [
        "📡 Что делает бот",
        "❓ Помощь",
        "⚙️ Настройки",
        "💰 Расход",
        "📥 Обучить",
        "🎯 Хвост тренда",
        "🎨 Стиль",
        "🔄 Перезапустить бота",
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
        "Мой аккаунт X",
    ]


def test_callback_data_fits_telegram_limit() -> None:
    for kb in (keyboards.more_menu(), keyboards.niche_presets(), keyboards.settings_menu()):
        assert all(len(d.encode()) <= 64 for d in _data(kb))


def test_brain_menu_fits_phone_width() -> None:
    """Меню «Мозга» на телефоне: не больше двух кнопок в ряд, подписи не режутся."""
    counts = {k: 999 for k in keyboards.BRAIN_SEGMENT_LABELS}
    kb = keyboards.brain_menu(counts, conflicts=99)
    for row in kb.inline_keyboard:
        assert len(row) <= 2, [b.text for b in row]
        if len(row) == 2:
            assert all(len(b.text) <= 20 for b in row), [b.text for b in row]
    data = set(_data(kb))
    assert {f"bn:seg:{k}:1" for k in keyboards.BRAIN_SEGMENT_LABELS} <= data
    assert {"bn:goals", "bn:cfl", "bn:w", "bn:search", "bn:export", "bn:rb", "bn:learn"} <= data
