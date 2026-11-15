"""Текст разделов промпта писателя: знания (ТЗ v3.1, brain 4.3), пакет, чужой пост, разбор."""

from __future__ import annotations

from collections.abc import Sequence

from xcopilot.llm.untrusted import wrap_untrusted
from xcopilot.retrieval.context_pack import ContextPack, KnowledgeItem, TaskSpec
from xcopilot.writer.schemas import TargetPost, WhyItWorked

NO_PASSPORT = "# Паспорт стиля\nПаспорта пока нет: пиши коротко, просто и без штампов."

# слот знания → заголовок раздела (порядок показа); неизвестный слот — «Приёмы»
SLOT_TITLES: dict[str, str] = {
    "pinned": "Закреплено владельцем",
    "hook": "Первая фраза",
    "structure": "Структура",
    "core": "Приёмы",
    "device": "Приёмы",
    "platform": "Площадка",
    "anti": "Чего избегать",
    "avoid": "Чего избегать",
}
_ORDER = ["Закреплено владельцем", "Первая фраза", "Структура", "Приёмы", "Площадка"]
_AVOID = "Чего избегать"


def application_slot(slot: str) -> str:
    """Слот строки `knowledge_applications` (core / anti / pinned) для слота знания в пакете."""
    if slot == "pinned":
        return "pinned"
    if slot in ("anti", "avoid"):
        return "anti"
    return "core"


def _line(item: KnowledgeItem) -> str:
    line = f"- {item.guidance}"
    return line + (f"\n  Каркас: {item.skeleton}" if item.skeleton else "")


def render_knowledge(
    task: TaskSpec, items: Sequence[KnowledgeItem], probation: KnowledgeItem | None = None
) -> str:
    """Раздел знаний: справочные наблюдения ниже жёстких правил; без цифр уверенности."""
    if not items and probation is None:
        return ""
    dims = [task.format, f"цель — {task.goal}", *(d for d in (task.niche, task.lang) if d)]
    lines = [
        "## Знания под задачу: " + " · ".join(dims),
        "Это наблюдения о том, что работает. Жёсткие правила паспорта важнее любого пункта ниже.",
    ]
    groups: dict[str, list[KnowledgeItem]] = {}
    for item in items:
        groups.setdefault(SLOT_TITLES.get(item.slot, "Приёмы"), []).append(item)
    for title in [*_ORDER, *(t for t in groups if t not in (*_ORDER, _AVOID)), _AVOID]:
        if title in groups:
            lines += ["", f"### {title}", *(_line(i) for i in groups[title])]
    if probation is not None:
        lines += ["", "### Испытание — только для этого варианта", _line(probation)]
    return "\n".join(lines)


def pack_sections(pack: ContextPack) -> dict[str, str]:
    """Секции пакета этапа 9 по именам; паспорт с жёсткими правилами — отдельно (кэш)."""
    sections = dict(pack.sections())
    sections.setdefault("passport", NO_PASSPORT)
    if "facts" in sections:
        sections["facts"] += "\n(Цифры и имена — только отсюда.)"
    else:
        sections["facts"] = "# Факты\nФактов нет — значит, без цифр."
    return sections


def render_task(task: TaskSpec, *, topic_title: str | None = None) -> str:
    lines = ["# Задача", f"Тема: {topic_title or task.topic}", f"Формат: {task.format}"]
    lines += [f"Язык: {task.lang}", f"Цель: {task.goal}"]
    if task.niche:
        lines.append(f"Ниша: {task.niche}")
    return "\n".join(lines)


def render_hint(hint: str | None) -> str:
    return f"# Пожелание владельца\n{hint.strip()}" if hint and hint.strip() else ""


def render_target(target: TargetPost | None, fmt: str) -> str:
    if target is None:
        return ""
    title = "Пост, на который отвечаем" if fmt == "reply" else "Пост, который цитируем"
    who = f" (автор: @{target.author})" if target.author else ""
    return f"# {title}{who}\n" + wrap_untrusted(target.text, "target_post")


def render_original(text: str) -> str:
    return "# Оригинал тренда\n" + wrap_untrusted(text, "trend_original")


def render_analysis(why: WhyItWorked) -> str:
    if not why.available:
        return "# Почему зашёл (гипотеза)\nРазбора нет: опирайся на оригинал и паспорт."
    lines = ["# Почему зашёл (гипотеза — не доказательство)"]
    if why.hook_type:
        hook = f" — «{why.hook_text}»" if why.hook_text else ""
        lines.append(f"Зачин: {why.hook_type}{hook}")
    if why.structure:
        lines.append(f"Структура: {why.structure}")
    if why.devices:
        lines.append("Приёмы: " + ", ".join(why.devices))
    if why.closer:
        lines.append(f"Концовка: {why.closer}")
    lines += [f"- {h}" for h in why.hypotheses]
    return "\n".join(lines)


def render_avoid(texts: Sequence[str], *, original: str | None = None) -> str:
    """Чего не повторять при перегенерации."""
    lines: list[str] = []
    if texts:
        lines.append("# Не повторяй уже написанные варианты")
        lines += [f"- {t}" for t in texts]
    if original:
        lines.append("Прошлый вариант вышел слишком похожим на оригинал: напиши совсем иначе.")
    return "\n".join(lines)
