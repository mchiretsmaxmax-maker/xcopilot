"""Inline-клавиатуры бота."""

from __future__ import annotations

from collections.abc import Sequence

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
        [("Точечное чтение", "xc:lookup")],
        [("Платное чтение", "xc:paid")],
    )


def x_paid_confirm() -> InlineKeyboardMarkup:
    return _kb([("Включить", "xc:paid_yes"), ("Отмена", "xc:cancel")])


def x_lookup_confirm() -> InlineKeyboardMarkup:
    return _kb([("Включить", "xc:lookup_yes"), ("Отмена", "xc:cancel")])


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


# --- этап 10: писатель ---------------------------------------------------------------------


def write_formats() -> InlineKeyboardMarkup:
    return _kb(
        [("Пост", "wfmt:post"), ("Тред", "wfmt:thread")],
        [("Ответ", "wfmt:reply"), ("Цитата", "wfmt:quote")],
    )


def topic_actions(topic_id: object, fmt: str = "post") -> InlineKeyboardMarkup:
    """Кнопки темы: [Писать] (формат темы) и [Не то]."""
    return _kb([("Писать", f"wtopic:{topic_id}:{fmt}"), ("Не то", "wdrop:" + str(topic_id))])


def topic_choices(topics: list[tuple[object, str]]) -> InlineKeyboardMarkup:
    """Темы на выбор при «✍️ Написать» (до пяти последних новых)."""
    return _kb(*([(title[:60], f"wpick:{tid}")] for tid, title in topics))


def material_card(source_id: str, *, ask_outcome: bool = True) -> InlineKeyboardMarkup:
    """Кнопки карточки разбора (ТЗ v3.1, 5.2): исход, отношение, убрать."""
    rows: list[list[tuple[str, str]]] = []
    if ask_outcome:
        rows.append(
            [
                ("🔥 Залетел", f"mo:{source_id}:hit"),
                ("👍 Норм", f"mo:{source_id}:normal"),
                ("👎 Не зашёл", f"mo:{source_id}:flop"),
                ("🔢 Цифры", f"mo:{source_id}:nums"),
            ]
        )
    rows.append(
        [
            ("Это моё", f"ms:{source_id}:own"),
            ("Антипример", f"ms:{source_id}:anti_example"),
            ("🗑 Убрать", f"irm:{source_id}"),
        ]
    )
    return _kb(*rows)


# --- этап 9C: «Мозг» -----------------------------------------------------------------------
# Данные кнопок: bn:<действие>[:<аргументы>]; длина не больше 64 байт (id — 36 знаков).

BRAIN_SEGMENT_LABELS: dict[str, str] = {
    "voice": "🎙 Голос",
    "humanness": "🧍 Человечность",
    "hooks": "🪝 Хуки",
    "posts": "📝 Посты",
    "threads": "🧵 Треды",
    "replies": "💬 Ответы",
    "quotes": "🔁 Цитаты",
    "packaging_timing": "📦 Упаковка",
    "platform": "⚙️ Механика X",
}
_BRAIN_ROWS = (
    ("voice", "humanness", "hooks"),
    ("posts", "threads", "replies", "quotes"),
)


def brain_menu(counts: dict[str, int] | None = None, conflicts: int = 0) -> InlineKeyboardMarkup:
    """Главное меню «Мозга»: сегменты со счётчиками, конфликты, сводка, поиск, экспорт, откат."""
    counts = counts or {}

    def seg(key: str) -> tuple[str, str]:
        return (f"{BRAIN_SEGMENT_LABELS[key]} {counts.get(key, 0)}", f"bn:seg:{key}:1")

    rows = [[seg(k) for k in row] for row in _BRAIN_ROWS]
    rows.append(
        [
            ("🎯 По цели", "bn:goals"),
            seg("packaging_timing"),
            (f"⚠️ Конфликты {conflicts}", "bn:cfl"),
        ]
    )
    rows.append([seg("platform")])
    rows.append(
        [
            ("🆕 За неделю", "bn:w"),
            ("🔎 Поиск", "bn:search"),
            ("📤 Экспорт", "bn:export"),
            ("⏪ Откат", "bn:rb"),
        ]
    )
    return _kb(*rows)


def item_actions(item_id: object) -> InlineKeyboardMarkup:
    """Кнопки карточки знания (ТЗ v3.1, 5.2)."""
    i = str(item_id)
    return _kb(
        [("📎 Доказательства", f"bn:ev:{i}"), ("✏️ Изменить", f"bn:edit:{i}")],
        [("📌 Закрепить", f"bn:pin:{i}")],
        [("✅ В работу", f"bn:ok:{i}"), ("🚫 Не моё", f"bn:nm:{i}"), ("🗑 Удалить", f"bn:del:{i}")],
    )


def digest_actions(week: str = "") -> InlineKeyboardMarkup:
    """Пакетное решение по сводке; `week` — начало недели YYYYMMDD."""
    return _kb([("Принять всё", f"bn:dall:{week}"), ("Разобрать по одному", f"bn:done:{week}")])


def brain_delete_confirm(item_id: object) -> InlineKeyboardMarkup:
    return _kb([("Да", f"bn:delyes:{item_id}"), ("Нет", f"bn:delno:{item_id}")])


def brain_class_a(item_id: object) -> InlineKeyboardMarkup:
    """Знание класса A ждёт явного ответа."""
    return _kb(
        [
            ("Да", f"bn:aok:{item_id}"),
            ("Нет", f"bn:ano:{item_id}"),
            ("Изменить", f"bn:aedit:{item_id}"),
        ]
    )


def brain_items(
    items: Sequence[tuple[object, str]], segment: str, page: int, pages: int
) -> InlineKeyboardMarkup:
    """Знания страницы сегмента кнопками и навигация."""
    rows = [[(label[:40], f"bn:item:{item_id}")] for item_id, label in items]
    nav: list[tuple[str, str]] = []
    if page > 1:
        nav.append(("‹ Назад", f"bn:seg:{segment}:{page - 1}"))
    if page < pages:
        nav.append(("Дальше ›", f"bn:seg:{segment}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([("‹ Мозг", "bn:menu")])
    return _kb(*rows)


def brain_goals(counts: dict[str, int]) -> InlineKeyboardMarkup:
    labels = (
        ("goal_reach", "Охват"),
        ("goal_engagement", "Вовлечённость"),
        ("goal_replies", "Ответы"),
        ("goal_saves", "Сохранения"),
    )
    return _kb(
        [(f"{t} {counts.get(k, 0)}", f"bn:seg:{k}:1") for k, t in labels[:2]],
        [(f"{t} {counts.get(k, 0)}", f"bn:seg:{k}:1") for k, t in labels[2:]],
        [("‹ Мозг", "bn:menu")],
    )


def brain_conflict(link_id: object) -> InlineKeyboardMarkup:
    i = str(link_id)
    return _kb(
        [("Первое", f"bn:cf:{i}:first"), ("Второе", f"bn:cf:{i}:second")],
        [("Оба: развести по целям", f"bn:cf:{i}:scope")],
    )


def brain_evidence(rows: Sequence[tuple[object, str]], item_id: object) -> InlineKeyboardMarkup:
    """По кнопке на доказательство («🗑 №N» убирает его) и возврат к карточке."""
    buttons = [[(f"🗑 Убрать №{n}", f"bn:evrm:{ev}")] for n, (ev, _) in enumerate(rows, start=1)]
    buttons.append([("‹ К знанию", f"bn:item:{item_id}")])
    return _kb(*buttons)


def brain_one_by_one(trials: Sequence[tuple[object, object, str]]) -> InlineKeyboardMarkup:
    """Автоиспытания по одному: (id знания, id перехода, подпись) → [Оставить] [Убрать]."""
    rows: list[list[tuple[str, str]]] = []
    for n, (item_id, transition_id, _) in enumerate(trials, start=1):
        rows.append(
            [(f"{n}. Оставить", f"bn:keep:{item_id}"), (f"{n}. Убрать", f"bn:drev:{transition_id}")]
        )
    return _kb(*rows)


def brain_rollback_dates() -> InlineKeyboardMarkup:
    return _kb(
        [("Вчера", "bn:rbd:1"), ("7 дней назад", "bn:rbd:7"), ("30 дней назад", "bn:rbd:30")]
    )


def brain_rollback_confirm(day: str) -> InlineKeyboardMarkup:
    return _kb([("Откатить", f"bn:rbok:{day}"), ("Отмена", "bn:rbno")])


def brain_rollback_undo(rollback_id: object) -> InlineKeyboardMarkup:
    return _kb([("Отменить откат", f"bn:rbundo:{rollback_id}")])


def brain_settings(autonomy: str) -> InlineKeyboardMarkup:
    if autonomy == "manual":
        return _kb([("Включить автоиспытания", "bn:set:auto")])
    return _kb([("Выключить автоиспытания", "bn:set:manual")])


def onboarding_rules() -> InlineKeyboardMarkup:
    return _kb([("Написать правила", "obr:start"), ("Пропустить", "obr:skip")])


def onboarding_rules_done() -> InlineKeyboardMarkup:
    return _kb([("Готово", "obr:done")])
