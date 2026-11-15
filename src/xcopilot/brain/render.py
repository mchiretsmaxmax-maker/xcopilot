"""Рендер пакета знаний: один текст для любой модели, без цифр уверенности (ТЗ v3.1, 4.3).

Числа уверенности модель склонна переносить в пост, поэтому уверенность — словами. Идентификаторы,
версии, оценки и ранги в Markdown не попадают: одна и та же выдача даёт один и тот же текст.
"""

from __future__ import annotations

from xcopilot.brain.schemas import KnowledgePack, PackItem

INTRO = "Это наблюдения о том, что работает. Жёсткие правила паспорта важнее любого пункта ниже."
_FORMAT = {
    "post": "пост",
    "thread": "тред",
    "reply": "ответ",
    "quote": "цитата",
    "article": "статья",
}
_GOAL = {
    "reach": "охват",
    "engagement": "вовлечённость",
    "replies": "ответы",
    "saves": "сохранения",
}
_LEVEL = {"high": "высокая", "medium": "средняя", "low": "низкая"}

SECTIONS = (
    "pinned",
    "hook",
    "structure",
    "device",
    "closer",
    "platform",
    "anti",
    "probation",
)
_TITLES = {
    "pinned": "Закреплено владельцем",
    "hook": "Хук",
    "structure": "Структура",
    "device": "Приёмы",
    "closer": "Концовка",
    "platform": "Площадка и упаковка",
    "anti": "Чего избегать",
    "probation": "Испытание — только для варианта 3",
}


def section_of(item: PackItem) -> str:
    """Смысловой раздел знания (ТЗ 4.2)."""
    if item.slot in ("pinned", "anti", "probation"):
        return item.slot
    if item.kind == "structure":
        return "structure"
    if item.element == "hook":
        return "hook"
    if item.element == "device":
        return "device"
    if item.element == "closer":
        return "closer"
    return "platform"


def item_line(item: PackItem) -> str:
    """Строка знания: текст совета, каркас и словесная уверенность."""
    line = f"- {item.guidance.strip()}"
    if item.skeleton:
        line += f" Каркас: {item.skeleton.strip()}"
    if item.slot in ("pinned", "probation"):
        return line
    note = f"уверенность {_LEVEL[item.confidence]}"
    if item.proven_on_own_posts:
        note += "; проверено на постах владельца"
    return f"{line} ({note})"


def _header(pack: KnowledgePack) -> str:
    t = pack.task
    parts = [_FORMAT.get(t.format, t.format), f"цель — {_GOAL.get(t.goal, t.goal)}"]
    parts += [x for x in (t.niche, t.archetype, t.lang) if x]
    return f"## Знания под задачу: {' · '.join(parts)}"


def to_markdown(pack: KnowledgePack) -> str:
    out = [_header(pack), INTRO]
    for name in SECTIONS:
        rows = [i for i in pack.items if section_of(i) == name]
        if not rows:
            continue
        title = "Первая фраза" if name == "hook" and pack.task.format == "reply" else _TITLES[name]
        out += ["", f"### {title}", *(item_line(i) for i in rows)]
    return "\n".join(out) + "\n"


def to_json(pack: KnowledgePack) -> str:
    return pack.model_dump_json(indent=2)
