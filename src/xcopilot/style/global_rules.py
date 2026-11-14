"""Глобальный пакет анти-AI правил v1 (RU и EN) → принятые уроки системного аккаунта."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from xcopilot.db.repos import AccountsRepo, LessonsRepo
from xcopilot.db.uow import DbSession
from xcopilot.errors import ConfigError

SEED_DIR = Path(__file__).resolve().parent / "seed"
SEED_FILES = ("anti_ai_ru.yaml", "anti_ai_en.yaml")
LESSON_KINDS = frozenset({"do", "dont", "pattern"})


def read_seed(directory: Path = SEED_DIR) -> list[tuple[str, str, int]]:
    """Правила всех файлов пакета: (вид, текст, версия пакета); битый файл — ConfigError."""
    rules: list[tuple[str, str, int]] = []
    for name in SEED_FILES:
        data: Any = yaml.safe_load((directory / name).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
            raise ConfigError(f"{name}: нужен список rules")
        version = int(data.get("version", 1))
        for item in data["rules"]:
            kind, text = item.get("kind"), str(item.get("text", "")).strip()
            if kind not in LESSON_KINDS or not text:
                raise ConfigError(f"{name}: неверное правило {item!r}")
            rules.append((kind, text, version))
    return rules


async def load_seed(session: DbSession, directory: Path = SEED_DIR) -> int:
    """Записать правила пакета в уроки аккаунта global как принятые; вернуть число новых.

    Идемпотентно: правило с тем же текстом повторно не добавляется (любой статус урока).
    """
    global_account = await AccountsRepo(session).get_global()
    repo = LessonsRepo(session, global_account.id)
    known = {
        lesson.text
        for status in ("proposed", "accepted", "rejected", "retired")
        for lesson in await repo.list_by_status(status)
    }
    added = 0
    for kind, text, version in read_seed(directory):
        if text in known:
            continue
        await repo.add(
            kind=kind,
            text=text,
            status="accepted",
            evidence=[{"seed": f"anti_ai_v{version}"}],
        )
        known.add(text)
        added += 1
    return added
