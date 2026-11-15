"""Форматы черновиков: длины и разбивка треда (ТЗ, этап 10, задача 3).

Пост — до 280 знаков, длинный — только если паспорт разрешил (`hard_rules.max_len > 280`);
тред — 3–8 частей по 280; ответ и цитата — до 280 и только при целевом посте.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

X_LIMIT = 280


@dataclass(frozen=True)
class FormatSpec:
    name: str
    max_len: int = X_LIMIT
    min_parts: int = 0
    max_parts: int = 0
    needs_target: bool = False


FORMATS: dict[str, FormatSpec] = {
    "post": FormatSpec("post"),
    "thread": FormatSpec("thread", min_parts=3, max_parts=8),
    "reply": FormatSpec("reply", needs_target=True),
    "quote": FormatSpec("quote", needs_target=True),
}


@dataclass(frozen=True)
class FormatCheck:
    """Что проверяется: формат, текст, части треда, целевой пост, длинный пост паспорта."""

    format: str
    text: str
    parts: tuple[str, ...] | list[str] = field(default_factory=tuple)
    reply_to_url: str | None = None
    has_target: bool = False
    long_max_len: int | None = None


def long_post_limit(hard_max_len: int | None) -> int | None:
    """Потолок длинного поста по паспорту или None, если длинные посты не разрешены."""
    return hard_max_len if hard_max_len is not None and hard_max_len > X_LIMIT else None


def validate_format(draft: FormatCheck) -> list[str]:
    """Коды нарушений формата; пустой список — формат соблюдён."""
    spec = FORMATS.get(draft.format)
    if spec is None:
        return [f"unknown_format:{draft.format}"]
    issues: list[str] = []
    if spec.needs_target and not (draft.reply_to_url or draft.has_target):
        issues.append("needs_target")
    if spec.max_parts:
        parts = [p for p in draft.parts if p.strip()]
        if len(parts) < spec.min_parts:
            issues.append("thread_too_few_parts")
        if len(parts) > spec.max_parts:
            issues.append("thread_too_many_parts")
        issues += [f"part_too_long:{i}" for i, p in enumerate(parts, 1) if len(p) > spec.max_len]
        return issues
    limit = spec.max_len
    if draft.format == "post" and draft.long_max_len:
        limit = max(limit, draft.long_max_len)
    if not draft.text.strip():
        issues.append("empty")
    elif len(draft.text) > limit:
        issues.append("too_long")
    return issues


def split_thread(text: str, *, max_len: int = X_LIMIT) -> list[str]:
    """Разбить текст на части треда: по абзацам, длинный абзац — по предложениям и словам."""
    parts: list[str] = []
    for para in (p.strip() for p in re.split(r"\n\s*\n", text)):
        if not para:
            continue
        current = ""
        for piece in _pieces(para, max_len):
            joined = f"{current} {piece}".strip()
            if len(joined) <= max_len:
                current = joined
            else:
                parts.append(current)
                current = piece
        if current:
            parts.append(current)
    return parts


def _pieces(para: str, max_len: int) -> list[str]:
    out: list[str] = []
    for sentence in re.split(r"(?<=[.!?…])\s+", para):
        if len(sentence) <= max_len:
            out.append(sentence)
            continue
        current = ""
        for word in sentence.split():
            joined = f"{current} {word}".strip()
            if len(joined) <= max_len:
                current = joined
            else:
                if current:
                    out.append(current)
                current = word[:max_len]
        if current:
            out.append(current)
    return out
