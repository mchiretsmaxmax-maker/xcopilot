"""Показ паспорта в Telegram и экспорт в .md."""

from __future__ import annotations

from xcopilot.style.passport import FORMAT_NAMES, FORMAT_TITLES, StylePassport

TG_LIMIT = 4096


EMPTY = "—"
CASING_MD = {"any": "любой", "lower": "строчные"}
LEVEL_MD = {"none": "нет", "rare": "редко", "free": "свободно"}
EXAMPLE_SEP = "---"
HINT = (
    "<!-- Паспорт стиля{version}. Правь текст под заголовками «# …» и пришли файл боту:"
    " «🎨 Стиль» → «📤 Загрузить свой». Пустой раздел — «—». Примеры разделяй строкой «---». -->"
)
FORMAT_HINT = "<!-- Что писать: правила только для {what}, своими словами. Пусто — «—». -->"
HARD_HINT = (
    "<!-- регистр: любой | строчные; эмодзи, хэштеги, ссылки: нет | редко | свободно;"
    " длина: число знаков; запрещённые фразы: через запятую. -->"
)


def _list(items: list[str]) -> str:
    return "\n".join(f"- {x}" for x in items) if items else EMPTY


def _labelled_list(title: str, items: list[str]) -> str:
    return f"{title}:\n{_list(items)}" if items else f"{title}: {EMPTY}"


def _blocks(items: list[str]) -> str:
    return f"\n\n{EXAMPLE_SEP}\n\n".join(items) if items else EMPTY


def _phrases(phrases: list[str]) -> str:
    return ", ".join(f"«{p}»" for p in phrases) if phrases else EMPTY


def hard_rules_md(passport: StylePassport) -> str:
    r = passport.hard_rules
    return "\n".join(
        [
            f"регистр: {CASING_MD[r.casing]}",
            f"эмодзи: {LEVEL_MD[r.emoji]}",
            f"хэштеги: {LEVEL_MD[r.hashtags]}",
            f"ссылки: {LEVEL_MD[r.links]}",
            f"длина: {r.max_len}",
            f"запрещённые фразы: {_phrases(r.banned_phrases)}",
        ]
    )


def export_md(passport: StylePassport, version: int | None = None, *, hints: bool = True) -> str:
    """Паспорт файлом .md, который владелец правит руками; `parse_md` — обратная операция.

    В файл не идут evidence, formats, rough, id кусков примеров и служебные поля версии:
    при разборе их берут из паспорта-основы.
    """
    s = passport.structure
    title = "" if version is None else f" v{version}"
    if passport.label:
        title += f" «{passport.label}»"
    sections = [
        ("Описание", passport.summary or EMPTY),
        ("Тон", passport.tone or EMPTY),
        (
            "Как пишу",
            "\n".join(
                [
                    f"Длина: {s.length or EMPTY}",
                    f"Переносы: {s.line_breaks or EMPTY}",
                    _labelled_list("Начала", s.openings),
                    _labelled_list("Концовки", s.endings),
                ]
            ),
        ),
        (
            "Слова: использую / избегаю",
            _labelled_list("Использую", passport.lexicon.use)
            + "\n"
            + _labelled_list("Избегаю", passport.lexicon.avoid),
        ),
        ("Фирменные приёмы", _list(passport.signature_patterns)),
        ("Мои правила", passport.owner_rules or EMPTY),
        *[
            (
                f"Правила для {FORMAT_TITLES[f]}",
                (FORMAT_HINT.format(what=FORMAT_TITLES[f]) + "\n" if hints else "")
                + (passport.format_rules.get(f) or EMPTY),
            )
            for f in FORMAT_NAMES
        ],
        ("Примеры", _blocks([e.text for e in passport.examples])),
        ("Антипримеры", _blocks(passport.anti_examples)),
        ("Жёсткие правила", (HARD_HINT + "\n" if hints else "") + hard_rules_md(passport)),
    ]
    lines = [HINT.format(version=title) if hints else f"Паспорт стиля{title}"]
    for name, body in sections:
        lines += ["", f"# {name}", "", body]
    return "\n".join(lines) + "\n"


def render(passport: StylePassport, version: int | None = None) -> list[str]:
    md = export_md(passport, version, hints=False).strip()
    if not md:
        return []

    paragraphs = md.split("\n\n")
    units: list[tuple[str, int]] = []
    for i, para in enumerate(paragraphs):
        if len(para) <= TG_LIMIT:
            sep_type = 2 if i > 0 else 0
            units.append((para, sep_type))
        else:
            lines = para.split("\n")
            for line in lines:
                if len(line) <= TG_LIMIT:
                    units.append((line, 1))
                else:
                    for j in range(0, len(line), TG_LIMIT):
                        units.append((line[j : j + TG_LIMIT], 1))

    parts: list[str] = []
    current = ""
    for text, sep_type in units:
        if not current:
            current = text
        else:
            sep = "\n" if sep_type == 1 else "\n\n"
            candidate = current + sep + text
            if len(candidate) <= TG_LIMIT:
                current = candidate
            else:
                parts.append(current)
                current = text
    if current:
        parts.append(current)

    return parts
