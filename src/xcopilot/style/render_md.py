"""Показ паспорта в Telegram и экспорт в .md."""

from __future__ import annotations

from xcopilot.style.passport import StylePassport

TG_LIMIT = 4096


def export_md(passport: StylePassport, version: int | None = None) -> str:
    lines = []
    if version is None:
        lines.append("# Паспорт стиля")
    else:
        lines.append(f"# Паспорт стиля v{version}")
    if passport.rough:
        lines.append("_Черновой: примеров меньше 5._")
    lines.append("")

    def add_section(title: str, content: str) -> None:
        lines.append(f"## {title}")
        lines.append("")
        lines.extend(content.split("\n"))
        lines.append("")

    summary = passport.summary if passport.summary else "—"
    add_section("Описание", summary)

    tone = passport.tone if passport.tone else "—"
    add_section("Тон", tone)

    use = ", ".join(passport.lexicon.use) if passport.lexicon.use else "—"
    avoid = ", ".join(passport.lexicon.avoid) if passport.lexicon.avoid else "—"
    add_section("Лексика", f"Использовать: {use}\nИзбегать: {avoid}")

    length = passport.structure.length if passport.structure.length else "—"
    line_breaks = passport.structure.line_breaks if passport.structure.line_breaks else "—"
    openings = "; ".join(passport.structure.openings) if passport.structure.openings else "—"
    endings = "; ".join(passport.structure.endings) if passport.structure.endings else "—"
    add_section(
        "Структура",
        f"Длина: {length}\nПереносы: {line_breaks}\nЗачины: {openings}\nКонцовки: {endings}",
    )

    if passport.formats:
        formats_content = "\n".join(
            f"- {f.format}: {round(f.share * 100)}%" for f in passport.formats
        )
    else:
        formats_content = "—"
    add_section("Форматы", formats_content)

    hard_lines = []
    casing = "только строчные" if passport.hard_rules.casing == "lower" else "любой"
    hard_lines.append(f"- Регистр: {casing}")
    level_map = {"none": "нет", "rare": "редко", "free": "свободно"}
    emoji = level_map.get(passport.hard_rules.emoji, "свободно")
    hard_lines.append(f"- Эмодзи: {emoji}")
    hashtags = level_map.get(passport.hard_rules.hashtags, "свободно")
    hard_lines.append(f"- Хэштеги: {hashtags}")
    links = level_map.get(passport.hard_rules.links, "свободно")
    hard_lines.append(f"- Ссылки: {links}")
    hard_lines.append(f"- Длина: до {passport.hard_rules.max_len} знаков")
    for phrase in passport.hard_rules.banned_phrases:
        hard_lines.append(f"- Запрет: «{phrase}»")
    add_section("Жёсткие правила", "\n".join(hard_lines))

    if passport.signature_patterns:
        patterns_content = "\n".join(f"- {p}" for p in passport.signature_patterns)
    else:
        patterns_content = "—"
    add_section("Фирменные приёмы", patterns_content)

    if passport.examples:
        examples_content = "\n".join(
            f"{i}. {ex.text}" for i, ex in enumerate(passport.examples, start=1)
        )
    else:
        examples_content = "—"
    add_section("Примеры", examples_content)

    if passport.anti_examples:
        anti_content = "\n".join(f"- {text}" for text in passport.anti_examples)
    else:
        anti_content = "—"
    add_section("Анти-примеры", anti_content)

    if passport.evidence:
        evidence_content = "\n".join(
            f"- {ev.claim}: {', '.join(ev.chunk_ids)}" for ev in passport.evidence
        )
    else:
        evidence_content = "—"
    add_section("Доказательства", evidence_content)

    while lines and lines[-1] == "":
        lines.pop()

    return "\n".join(lines)


def render(passport: StylePassport, version: int | None = None) -> list[str]:
    md = export_md(passport, version)
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
