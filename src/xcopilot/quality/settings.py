"""Настройки ворот из файлов и паспорта: жёсткие правила, глобальные запреты."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from xcopilot.style.passport import HardRules

BANNED = Path(__file__).resolve().parents[3] / "config" / "banned_phrases.toml"
_LEVELS = ("none", "rare", "free")


def _level(value: Any) -> str | None:
    if isinstance(value, bool):
        return "free" if value else "none"
    return value if value in _LEVELS else None


def hard_rules_of(raw: Any) -> HardRules:
    """Жёсткие правила паспорта в любом виде (модель, словарь, emoji: false) → HardRules."""
    if isinstance(raw, HardRules):
        return raw
    if isinstance(raw, list):  # колонка hard_rules: [{name, value}]
        raw = {r.get("name"): r.get("value") for r in raw if isinstance(r, Mapping)}
    if not isinstance(raw, Mapping):
        return HardRules()
    data: dict[str, Any] = {}
    if raw.get("casing") in ("lower", "any"):
        data["casing"] = raw["casing"]
    for key in ("emoji", "hashtags", "links"):
        level = _level(raw.get(key))
        if level is not None:
            data[key] = level
    if isinstance(raw.get("max_len"), int) and not isinstance(raw.get("max_len"), bool):
        data["max_len"] = raw["max_len"]
    banned = raw.get("banned_phrases")
    if isinstance(banned, list):
        data["banned_phrases"] = [str(b) for b in banned if str(b).strip()]
    try:
        return HardRules.model_validate(data)
    except ValidationError:
        return HardRules()


@cache
def global_banned_phrases(path: Path = BANNED) -> tuple[str, ...]:
    """Глобальные запреты (все языки) из `config/banned_phrases.toml`."""
    if not path.exists():
        return ()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return tuple(str(p) for lang in data.values() if isinstance(lang, dict)
                 for p in lang.get("phrases", []))  # fmt: skip
