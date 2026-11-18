"""Inline-клавиатуры бота."""

from __future__ import annotations

from collections.abc import Sequence

from aiogram.types import (
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from xcopilot.bot import texts


def _kb(*rows: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text, callback_data=data) for text, data in row]
            for row in rows
        ]
    )


BTN_POST = "✍️ Пост"
BTN_TOPICS = "💡 Темы"
BTN_BRAIN = "🧠 Мозг"
BTN_STATS = "📊 Аналитика"
BTN_QUEUE = "🗂 Очередь"
BTN_MORE = "⚙️ Ещё"
BTN_REPLIES = "💬 Ответы"
REPLY_LABELS = (BTN_POST, BTN_REPLIES, BTN_TOPICS, BTN_BRAIN, BTN_STATS, BTN_QUEUE, BTN_MORE)


def reply_menu() -> ReplyKeyboardMarkup:
    """Постоянное меню под полем ввода (после онбординга, по /menu)."""
    rows = [REPLY_LABELS[:4], REPLY_LABELS[4:]]
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t) for t in row] for row in rows],
        resize_keyboard=True,
        is_persistent=True,
    )


def more_menu() -> InlineKeyboardMarkup:
    """Всё, чего нет в нижней клавиатуре (старые menu:* из сообщений по /start тоже работают)."""
    return _kb(
        [("📡 Что делает бот", "more:status"), ("❓ Помощь", "more:help")],
        [("⚙️ Настройки", "more:settings"), ("💰 Расход", "more:cost")],
        [("📥 Обучить", "menu:learn"), ("🎯 Хвост тренда", "menu:trend")],
        [("🎨 Стиль", "menu:style")],
        [("🔄 Перезапустить бота", "more:restart")],
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
        [("Мой аккаунт X", "set:handle"), ("Учёба на ответах", "set:reply_learning")],
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


def _outcome_row(source_id: str) -> list[tuple[str, str]]:
    return [
        ("🔥 Залетел", f"mo:{source_id}:hit"),
        ("👍 Норм", f"mo:{source_id}:normal"),
        ("👎 Не зашёл", f"mo:{source_id}:flop"),
        ("🔢 Цифры", f"mo:{source_id}:nums"),
    ]


def material_card(
    source_id: str, *, ask_outcome: bool = True, fix_outcome: bool = False
) -> InlineKeyboardMarkup:
    """Кнопки карточки разбора (ТЗ v3.1, 5.2): исход, отношение, убрать.

    fix_outcome — исход поставлен по публичным цифрам: вместо вопроса «✏️ Поправить оценку».
    """
    rows: list[list[tuple[str, str]]] = []
    if ask_outcome:
        rows.append(_outcome_row(source_id))
    elif fix_outcome:
        rows.append([(texts.BTN_FIX_OUTCOME, f"moe:{source_id}")])
    rows.append(
        [
            ("Это моё", f"ms:{source_id}:own"),
            ("Антипример", f"ms:{source_id}:anti_example"),
            ("🗑 Убрать", f"irm:{source_id}"),
        ]
    )
    return _kb(*rows)


def material_outcome(source_id: str) -> InlineKeyboardMarkup:
    """Только кнопки оценки (после «✏️ Поправить оценку»)."""
    return _kb(_outcome_row(source_id))


def batch_summary(batch: str, unrated: int) -> InlineKeyboardMarkup:
    """Кнопки сводки пачки (ADR 0033): mbs:<пачка>:show:<с какого>|own|inspiration|rate."""
    rows = [
        [(texts.BTN_BATCH_SHOW, f"mbs:{batch}:show:0")],
        [
            (texts.BTN_BATCH_OWN, f"mbs:{batch}:own"),
            (texts.BTN_BATCH_INSPIRATION, f"mbs:{batch}:inspiration"),
        ],
    ]
    if unrated:
        rows.append([(texts.btn_batch_rate(unrated), f"mbs:{batch}:rate")])
    return _kb(*rows)


def batch_more(batch: str, offset: int, left: int) -> InlineKeyboardMarkup:
    return _kb([(texts.btn_batch_more(left), f"mbs:{batch}:show:{offset}")])


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
# по две кнопки в ряд: на телефоне три-четыре подписи со счётчиком обрезаются
_BRAIN_ROWS = (
    ("posts", "replies"),
    ("threads", "quotes"),
    ("voice", "humanness"),
    ("hooks", "packaging_timing"),
)


def brain_menu(counts: dict[str, int] | None = None, conflicts: int = 0) -> InlineKeyboardMarkup:
    """Главное меню «Мозга»: сегменты со счётчиками, конфликты, сводка, поиск, экспорт, откат."""
    counts = counts or {}

    def seg(key: str) -> tuple[str, str]:
        return (f"{BRAIN_SEGMENT_LABELS[key]} {counts.get(key, 0)}", f"bn:seg:{key}:1")

    rows = [[seg(k) for k in row] for row in _BRAIN_ROWS]
    rows += [
        [seg("platform"), ("🎯 По цели", "bn:goals")],
        [(f"⚠️ Конфликты {conflicts}", "bn:cfl"), ("🆕 За неделю", "bn:w")],
        [("🔎 Поиск", "bn:search"), ("📤 Экспорт", "bn:export")],
        [("⏪ Откат", "bn:rb"), ("🎓 Обучить сейчас", "bn:learn")],
    ]
    return _kb(*rows)


def item_actions(item_id: object, *, own: bool = False) -> InlineKeyboardMarkup:
    """Кнопки карточки знания (ТЗ v3.1, 5.2); у знания владельца — «🎯 Где работает»."""
    i = str(item_id)
    pin_row = [("📌 Закрепить", f"bn:pin:{i}")]
    if own:
        pin_row.append((BRAIN_WHERE, f"bn:where:{i}"))
    return _kb(
        [("📎 Доказательства", f"bn:ev:{i}"), ("✏️ Изменить", f"bn:edit:{i}")],
        pin_row,
        [("✅ В работу", f"bn:ok:{i}"), ("🚫 Не моё", f"bn:nm:{i}"), ("🗑 Удалить", f"bn:del:{i}")],
    )


def brain_where(item_id: object) -> InlineKeyboardMarkup:
    """Выбор области по формату для знания владельца: везде или один формат."""
    i = str(item_id)
    return _kb(
        [("Везде", f"bn:wh:{i}:all")],
        [("Только посты", f"bn:wh:{i}:post"), ("Только треды", f"bn:wh:{i}:thread")],
        [("Только ответы", f"bn:wh:{i}:reply"), ("Только цитаты", f"bn:wh:{i}:quote")],
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


# раздел формата «Мозга» → формат области знания
BRAIN_SEGMENT_FORMATS: dict[str, str] = {
    "posts": "post",
    "threads": "thread",
    "replies": "reply",
    "quotes": "quote",
}
BRAIN_ADD_RULE = "➕ Добавить правило"
BRAIN_WHERE = "🎯 Где работает"


def brain_items(
    items: Sequence[tuple[object, str]], segment: str, page: int, pages: int
) -> InlineKeyboardMarkup:
    """Знания страницы сегмента кнопками и навигация; в разделе формата — «Добавить правило»."""
    rows = [[(label[:40], f"bn:item:{item_id}")] for item_id, label in items]
    if segment in BRAIN_SEGMENT_FORMATS:
        rows.append([(BRAIN_ADD_RULE, f"bn:add:{segment}")])
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


# --- режим «💬 Ответы» ---------------------------------------------------------------------


COPY_TEXT_LIMIT = 256  # предел CopyTextButton в Telegram


def reply_variant(text: str, post_id: str | None) -> InlineKeyboardMarkup | None:
    """Кнопки варианта ответа: «↩️ Ответить в X» (intent) и «📋 Скопировать».

    Intent нет (нет id поста или текст длиннее 280 для X) — без первой кнопки; текст длиннее
    предела Telegram для копирования — без второй. Ни одной — None (текст есть в сообщении).
    """
    from xcopilot.x.intents import build_reply_intent

    row: list[InlineKeyboardButton] = []
    url = build_reply_intent(text, post_id) if post_id else None
    if url is not None:
        row.append(InlineKeyboardButton(text="↩️ Ответить в X", url=url))
    if 0 < len(text) <= COPY_TEXT_LIMIT:
        row.append(InlineKeyboardButton(text="📋 Скопировать", copy_text=CopyTextButton(text=text)))
    return InlineKeyboardMarkup(inline_keyboard=[row]) if row else None


def reply_mode_actions(run_id: object | None = None) -> InlineKeyboardMarkup:
    """«🔁 Ещё варианты», «👎 Не то» (если известен запуск) и «✖️ Выйти из режима»."""
    first = [("🔁 Ещё варианты", "rpl:more")]
    if run_id is not None:
        first.append(("👎 Не то", f"rpl:nope:{run_id}"))
    return _kb(first, [("✖️ Выйти из режима", "rpl:exit")])


# --- fix/one-action: «✖️ Отменить» на каждом шаге ввода ------------------------------------


def cancel() -> InlineKeyboardMarkup:
    """Одна кнопка «✖️ Отменить» (общий callback: как /cancel + снятие кнопок)."""
    from xcopilot.bot.texts import CANCEL_BUTTON

    return _kb([(CANCEL_BUTTON, "act:cancel")])


def with_cancel(markup: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    """Та же клавиатура и строкой ниже «✖️ Отменить»."""
    return InlineKeyboardMarkup(
        inline_keyboard=[*markup.inline_keyboard, *cancel().inline_keyboard]
    )
