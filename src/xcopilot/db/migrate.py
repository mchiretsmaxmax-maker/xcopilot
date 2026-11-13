"""Запуск миграций Alembic из кода и из командной строки."""

from __future__ import annotations

from typing import Any

from alembic.config import Config


def alembic_config(database_url: str) -> Config:
    """Конфиг Alembic для указанной БД."""
    raise NotImplementedError


def include_object(
    obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any
) -> bool:
    """Фильтр автогенерации: дочерние партиции не сравниваются с моделями."""
    raise NotImplementedError
