"""Форматы черновиков: длины и нарезка длинного текста на части (ТЗ, этап 10, задача 3).

С 16.11.2026 (требование владельца): пост не ограничен 280 знаками. Писатель пишет текст
естественной длины, а КОД режет его на части ≤ 280 по `weighted_len` (`split_post`); пост из
нескольких частей — тред. Тред — ≥ 1 части, каждая ≤ 280. Ответ и цитата — до 280 и только
при целевом посте.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from xcopilot.x.intents import weighted_len

X_LIMIT = 280


@dataclass(frozen=True)
class FormatSpec:
    name: str
    max_len: int = X_LIMIT
    is_thread: bool = False
    capped: bool = True  # False — потолка по длине нет (пост: длинный режется на части)
    needs_target: bool = False


FORMATS: dict[str, FormatSpec] = {
    "post": FormatSpec("post", capped=False),
    "thread": FormatSpec("thread", is_thread=True),
    "reply": FormatSpec("reply", needs_target=True),
    "quote": FormatSpec("quote", needs_target=True),
}


@dataclass(frozen=True)
class FormatCheck:
    """Что проверяется: формат, текст, части треда, целевой пост."""

    format: str
    text: str
    parts: tuple[str, ...] | list[str] = field(default_factory=tuple)
    reply_to_url: str | None = None
    has_target: bool = False


def validate_format(draft: FormatCheck) -> list[str]:
    """Коды нарушений формата; пустой список — формат соблюдён."""
    spec = FORMATS.get(draft.format)
    if spec is None:
        return [f"unknown_format:{draft.format}"]
    issues: list[str] = []
    if spec.needs_target and not (draft.reply_to_url or draft.has_target):
        issues.append("needs_target")
    if spec.is_thread:
        parts = [p for p in draft.parts if p.strip()]
        if not parts:
            issues.append("thread_too_few_parts")
        issues += [
            f"part_too_long:{i}" for i, p in enumerate(parts, 1) if weighted_len(p) > spec.max_len
        ]
        return issues
    if not draft.text.strip():
        issues.append("empty")
    elif spec.capped and weighted_len(draft.text) > spec.max_len:
        issues.append("too_long")
    return issues


# --- нарезка ---------------------------------------------------------------------------------

_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


def _hard_cut(word: str, limit: int) -> list[str]:
    """Слово длиннее части (не URL): режется по символам."""
    out: list[str] = []
    current = ""
    for ch in word:
        if weighted_len(current + ch) > limit:
            out.append(current)
            current = ch
        else:
            current += ch
    if current:
        out.append(current)
    return out


def _atoms(text: str, limit: int, level: int) -> list[tuple[str, str]]:
    """Куски (текст, разделитель перед ним) ≤ limit; уровни: абзацы → предложения → слова."""
    if level == 0:
        pieces, sep = re.split(r"\n\s*\n", text), "\n\n"
    elif level == 1:
        pieces, sep = _SENTENCE_END.split(text), " "
    else:
        pieces, sep = text.split(), " "
    out: list[tuple[str, str]] = []
    for piece in (p.strip() for p in pieces):
        if not piece:
            continue
        if weighted_len(piece) <= limit:
            out.append((piece, sep))
        elif level < 2:
            sub = _atoms(piece, limit, level + 1)
            out.append((sub[0][0], sep))
            out.extend(sub[1:])
        else:
            cut = _hard_cut(piece, limit)
            out.append((cut[0], sep))
            out.extend((c, "") for c in cut[1:])
    return out


def split_post(text: str, limit: int = X_LIMIT) -> list[str]:
    """Нарезать текст на части ≤ limit по `weighted_len`: детерминированно, без участия модели.

    Части заполняются жадно. Режем по границам абзацев; абзац, не влезающий в часть целиком,
    — по предложениям, предложение — по словам. Слова и URL не рвутся (кроме слова, которое
    само длиннее части).
    """
    text = text.strip()
    if not text:
        return []
    if weighted_len(text) <= limit:
        return [text]
    parts: list[str] = []
    current = ""
    for atom, sep in _atoms(text, limit, 0):
        joined = f"{current}{sep}{atom}" if current else atom
        if current and weighted_len(joined) > limit:
            parts.append(current)
            current = atom
        else:
            current = joined
    if current:
        parts.append(current)
    return parts


def normalize_parts(parts: list[str], limit: int = X_LIMIT) -> list[str]:
    """Части треда от модели: пустые убираются, слишком длинные режутся `split_post`."""
    out: list[str] = []
    for part in parts:
        out.extend(split_post(part, limit))
    return out
