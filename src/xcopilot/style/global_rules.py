"""Глобальный пакет анти-AI правил v1 (RU и EN): чтение файлов данных.

В БД пакет не пишется на этапе 8: его грузит этап 9A как знания слоя humanness (ТЗ v3.1).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from xcopilot.errors import ConfigError

SEED_DIR = Path(__file__).resolve().parent / "seed"
SEED_FILES = ("anti_ai_ru.yaml", "anti_ai_en.yaml")
RULE_KINDS = frozenset({"do", "dont", "pattern"})


@dataclass(frozen=True)
class SeedRule:
    kind: str
    text: str
    lang: str
    version: int


def read_seed(directory: Path = SEED_DIR) -> list[SeedRule]:
    """Правила всех файлов пакета; битый файл — ConfigError."""
    rules: list[SeedRule] = []
    for name in SEED_FILES:
        data: Any = yaml.safe_load((directory / name).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
            raise ConfigError(f"{name}: нужен список rules")
        version, lang = int(data.get("version", 1)), str(data.get("lang", ""))
        for item in data["rules"]:
            kind, text = item.get("kind"), str(item.get("text", "")).strip()
            if kind not in RULE_KINDS or not text or lang not in ("ru", "en"):
                raise ConfigError(f"{name}: неверное правило {item!r}")
            rules.append(SeedRule(kind=kind, text=text, lang=lang, version=version))
    return rules
