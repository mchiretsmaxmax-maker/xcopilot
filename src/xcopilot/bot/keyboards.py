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


def ingest_kind(batch_id: str, default: str | None) -> InlineKeyboardMarkup:
    """«Это что?»: вид материала для всей партии; догадка тегировщика отмечена ✓."""
    from xcopilot.bot.texts import KIND_LABELS

    def btn(kind: str) -> tuple[str, str]:
        label = KIND_LABELS[kind]
        return (f"✓ {label}" if kind == default else label, f"ikind:{batch_id}:{kind}")

    return _kb(
        [btn("own_post"), btn("liked_sample")],
        [btn("competitor_post"), btn("note")],
    )


def ingest_foreign_handle() -> InlineKeyboardMarkup:
    return _kb([("Пришлю текстом", "ingest:text")], [("Включить платное чтение", "xc:paid")])


def ingest_relation(
    batch_id: str, default: str | None, *, remove: bool = True
) -> InlineKeyboardMarkup:
    """Отношение к материалам партии (ТЗ v3.1): текущее отмечено ✓; ниже — «Убрать»."""
    from xcopilot.bot.texts import INGEST_REMOVE_LABEL, RELATION_LABELS

    def btn(stance: str) -> tuple[str, str]:
        label = RELATION_LABELS[stance]
        return (f"✓ {label}" if stance == default else label, f"irel:{batch_id}:{stance}")

    rows = [
        [btn("own"), btn("inspiration"), btn("competitor")],
        [btn("anti_example"), btn("reference")],
    ]
    if remove:
        rows.append([(INGEST_REMOVE_LABEL, f"irmb:{batch_id}")])
    return _kb(*rows)


def screenshot_doubt(
    source_id: str, name: str, options: list[tuple[str, int]]
) -> InlineKeyboardMarkup:
    """Варианты сомнительной цифры со скриншота и «Не важно»."""
    buttons = [(shown, f"iscr:{source_id}:{name}:{value}") for shown, value in options]
    buttons.append(("Не важно", f"iscr:{source_id}:{name}:skip"))
    return _kb(buttons)


def screenshot_not_post(source_id: str) -> InlineKeyboardMarkup:
    return _kb([("Разобрать как текст", f"iscrt:{source_id}"), ("Убрать", f"irm:{source_id}")])
