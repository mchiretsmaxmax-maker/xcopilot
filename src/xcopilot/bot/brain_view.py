"""Тексты и клавиатуры «Мозга» (ТЗ v3.1, раздел 5.2) — чистые функции над данными 9C."""

from __future__ import annotations

from datetime import date

from aiogram.types import InlineKeyboardMarkup

from xcopilot.bot import keyboards
from xcopilot.brain.digest import Digest
from xcopilot.brain.segments import (
    BrainCounts,
    EvidenceRow,
    ItemView,
    Page,
    SegmentItem,
)

ASK_RULES = (
    "Что у тебя точно работает? Напиши 3–5 своих правил — по одному на строку. "
    "Они станут первыми знаниями базы с пометкой «от владельца»."
)
RULES_SEND = "Присылай правила: каждая строка — отдельное правило. Закончишь — нажми «Готово»."
RULES_SKIPPED = "Хорошо. Свои правила можно добавить позже: /brain add <текст>."
RULES_DONE = "Готово. Правила уже в базе: посмотреть их можно в /brain."
NOT_OWNER = "Менять знания может только владелец аккаунта."
NOTHING_FOUND = "Ничего не нашёл."
EXPORT_LATER = "Экспорт появится после этапа «Пакет знаний» (9B)."
SEARCH_HINT = "Пришли: /brain search <текст>"
EDIT_ASK = "Пришли новый текст знания — одной фразой. Прежняя версия сохранится в истории."
DELETE_ASK = "Удалить знание и не предлагать похожие 30 дней?"
ROLLBACK_PICK = "На какую дату откатить базу? Выбери или пришли /brain undo ГГГГ-ММ-ДД."

MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
STATUS = {
    "candidate": "кандидат",
    "probation": "на испытании",
    "active": "в работе",
    "weakened": "ослабло",
    "conflicted": "противоречие",
    "archived": "в архиве",
    "rejected": "удалено",
    "merged": "влито",
}
LAYER = {
    "voice": "голос",
    "humanness": "человечность",
    "craft": "ремесло",
    "niche": "ниша",
    "platform": "площадка",
}
ELEMENT = {
    "hook": "🪝 Хук",
    "structure": "🧱 Структура",
    "device": "✨ Приём",
    "closer": "🏁 Концовка",
    "tone": "🎚 Тон",
    "lexicon": "🔤 Словарь",
    "packaging": "📦 Упаковка",
    "timing": "⏰ Время",
    "general": "💡 Общее",
}
FORMAT = {
    "post": "посты",
    "thread": "треды",
    "reply": "ответы",
    "quote": "цитаты",
    "article": "статьи",
}
GOAL = {
    "reach": "охват",
    "engagement": "вовлечённость",
    "replies": "ответы",
    "saves": "сохранения",
    "quality": "качество текста",
}


def plural(n: int, one: str, few: str, many: str) -> str:
    """«1 пост», «2 поста», «5 постов»."""
    if n % 10 == 1 and n % 100 != 11:
        form = one
    elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        form = few
    else:
        form = many
    return f"{n} {form}"


def human_date(day: date) -> str:
    return f"{day.day} {MONTHS[day.month - 1]}"


def render_menu(counts: BrainCounts) -> tuple[str, InlineKeyboardMarkup]:
    by = counts.by_status
    head = (
        f"🧠 Мозг · {plural(counts.materials, 'материал', 'материала', 'материалов')}"
        f" · {plural(counts.items_total, 'знание', 'знания', 'знаний')}"
    )
    line = (
        f"В работе {by.get('active', 0)} · на испытании {by.get('probation', 0)}"
        f" · кандидатов {by.get('candidate', 0)}"
    )
    seg = {s.key: s.count for s in counts.segments}
    return f"{head}\n{line}", keyboards.brain_menu(seg, counts.conflicts)


def render_segment(title: str, key: str, page: Page) -> tuple[str, InlineKeyboardMarkup]:
    if not page.items:
        text = f"{title}: пока пусто."
    else:
        lines = [
            f"{n}. «{i.statement}» — {STATUS.get(i.status, i.status)} · {i.quality:.2f}"
            for n, i in enumerate(page.items, start=1)
        ]
        text = f"{title} · {plural(page.total, 'знание', 'знания', 'знаний')}"
        if page.pages > 1:
            text += f" · стр. {page.page}/{page.pages}"
        text += "\n" + "\n".join(lines)
    items = [(i.id, i.statement) for i in page.items]
    return text, keyboards.brain_items(items, key, page.page, page.pages)


def render_search(query: str, found: list[SegmentItem]) -> tuple[str, InlineKeyboardMarkup | None]:
    if not found:
        return f"{NOTHING_FOUND} Запрос: «{query}».", None
    lines = [
        f"{n}. «{i.statement}» — {STATUS.get(i.status, i.status)}" for n, i in enumerate(found, 1)
    ]
    return "🔎 Нашёл:\n" + "\n".join(lines), keyboards.brain_items(
        [(i.id, i.statement) for i in found], "none", 1, 1
    )


def _fit_phrase(view: ItemView) -> str:
    if not view.fit_known:
        return "пока мало данных"
    word = "да" if view.fit_conf >= 0.6 else "возможно" if view.fit_conf >= 0.4 else "скорее нет"
    return f"{word} ({view.fit_conf:.2f})"


def _where(view: ItemView) -> str:
    parts: list[str] = []
    formats = view.scopes.get("format")
    parts.append(", ".join(FORMAT.get(f, f) for f in formats) if formats else "везде")
    goals = view.scopes.get("goal")
    goal = ", ".join(GOAL.get(g, g) for g in goals) if goals else GOAL.get(view.target_metric)
    if goal:
        parts.append(f"цель: {goal}")
    for facet, label in (("niche", "ниша"), ("lang", "язык")):
        if view.scopes.get(facet):
            parts.append(f"{label}: {', '.join(view.scopes[facet])}")
    return " · ".join(parts)


def render_card(view: ItemView) -> tuple[str, InlineKeyboardMarkup]:
    item = view.item
    mark = {"pinned": " · 📌", "disabled": " · не моё"}.get(item.owner_state, "")
    authors = view.authors
    author_word = "автора" if authors % 10 == 1 and authors % 100 != 11 else "авторов"
    lines = [
        f"{ELEMENT.get(item.element, item.element)} · {LAYER.get(item.layer, item.layer)}"
        f" · {STATUS.get(item.status, item.status)}{mark}",
        f"«{item.statement}»",
        f"Как применять: {item.guidance}",
        "",
        f"Уверенность: {view.confidence_word} ({item.quality:.2f})",
        f"Подходит тебе: {_fit_phrase(view)}",
        f"Доказательства: {plural(view.posts, 'пост', 'поста', 'постов')}"
        f" от {authors} {author_word}"
        f" · на твоих постах: {view.own_support} за, {view.own_contra} против",
        f"Где работает: {_where(view)}",
    ]
    if view.origin == "owner":
        lines.append("Источник: от владельца")
    return "\n".join(lines), keyboards.item_actions(item.id)


def render_evidence(view: ItemView, rows: list[EvidenceRow]) -> tuple[str, InlineKeyboardMarkup]:
    if not rows:
        return f"Доказательств у знания «{view.item.statement}» нет.", keyboards.brain_evidence(
            [], view.item.id
        )
    lines = [f"📎 Доказательства · «{view.item.statement}»"]
    marks = {1: "за", -1: "против", 0: "нейтрально"}
    for n, r in enumerate(rows, start=1):
        who = f"@{r.author_handle}" if r.author_handle else "владелец"
        what = r.url or (f"«{r.snippet}»" if r.snippet else r.basis)
        tier = f" · {r.outcome_tier}" if r.outcome_tier else ""
        lines.append(f"{n}. {who} — {marks.get(r.polarity, '')}{tier}: {what}")
    return "\n".join(lines), keyboards.brain_evidence([(r.id, "") for r in rows], view.item.id)


def render_class_a(item: SegmentItem) -> tuple[str, InlineKeyboardMarkup]:
    text = (
        f"🎙 Заметил: {item.statement}\nДобавить правило «{item.guidance}»?\n"
        "Без твоего «да» оно в черновики не попадёт."
    )
    return text, keyboards.brain_class_a(item.id)


def render_digest(digest: Digest) -> tuple[str, InlineKeyboardMarkup]:
    lines = [
        "🧠 Неделя Мозга",
        f"Материалов: +{digest.materials} · новых знаний: {digest.new_items}"
        f" · усилились: {digest.strengthened} · ослабло: {digest.weakened}",
    ]
    n = 0
    if digest.auto_trials:
        lines += ["", "Взял на испытание сам:"]
        for t in digest.auto_trials:
            n += 1
            lines.append(f"{n}. «{t.statement}»")
    if digest.class_a:
        lines += ["", "Ждёт твоего решения (голос):"]
        for a in digest.class_a:
            n += 1
            lines.append(f"{n}. «{a.statement}»")
    if digest.conflicts:
        lines += ["", "Конфликт:"]
        for c in digest.conflicts:
            n += 1
            lines.append(f"{n}. «{c.first}» ↔ «{c.second}»")
    lines += ["", "Результат:"]
    post_word = plural(digest.applied_posts, "пост", "поста", "постов")
    note = " — мало данных" if digest.low_sample else ""
    lines.append(f"— с применёнными знаниями: {post_word}{note}")
    keyboard = keyboards.digest_actions(digest.week_start.strftime("%Y%m%d"))
    return "\n".join(lines), keyboard


def render_conflict(first: str, second: str, link_id: object) -> tuple[str, InlineKeyboardMarkup]:
    return (
        f"Конфликт:\n1. «{first}»\n2. «{second}»\nЧто оставить?",
        keyboards.brain_conflict(link_id),
    )


def render_rollback_preview(
    day: date, created: int, changed: int, evidence: int
) -> tuple[str, InlineKeyboardMarkup]:
    text = (
        f"⏪ Откат на {human_date(day)}\n"
        f"Будет отменено: {plural(created, 'новое знание', 'новых знания', 'новых знаний')}, "
        f"{plural(changed, 'изменение', 'изменения', 'изменений')}, "
        f"{plural(evidence, 'доказательство', 'доказательства', 'доказательств')}.\n"
        "Материалы и разборы останутся."
    )
    return text, keyboards.brain_rollback_confirm(day.strftime("%Y%m%d"))


def render_settings(autonomy: str) -> tuple[str, InlineKeyboardMarkup]:
    if autonomy == "manual":
        state = "выключены — новые знания ждут твоего решения"
    else:
        state = "включены — база сама берёт на испытание проверенные знания, ты видишь это в сводке"
    return f"⚙️ Настройки Мозга\nАвтоиспытания: {state}.", keyboards.brain_settings(autonomy)
