"""Запуск миграций Alembic из кода и из командной строки.

`python -m xcopilot.db.migrate` — `alembic upgrade head` + месячные партиции журналов.
URL берётся из DATABASE_URL.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config

from xcopilot.clock import SystemClock
from xcopilot.db.engine import create_engine
from xcopilot.db.partitions import ensure_all_partitions, is_partition_name

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def alembic_config(database_url: str) -> Config:
    """Конфиг Alembic для указанной БД (без alembic.ini)."""
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return cfg


def include_object(
    obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any
) -> bool:
    """Фильтр автогенерации: дочерние партиции журналов не сравниваются с моделями."""
    return not (type_ == "table" and name is not None and is_partition_name(name))


async def _partitions(database_url: str) -> dict[str, list[str]]:
    engine = create_engine(database_url)
    try:
        async with engine.begin() as conn:
            return await ensure_all_partitions(conn, today=SystemClock().now().date())
    finally:
        await engine.dispose()


def upgrade(database_url: str, revision: str = "head") -> dict[str, list[str]]:
    """Накатить миграции и создать партиции на текущий и два следующих месяца."""
    command.upgrade(alembic_config(database_url), revision)
    return asyncio.run(_partitions(database_url))


def main() -> int:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("migrate: не задан DATABASE_URL", file=sys.stderr)
        return 2
    created = upgrade(url)
    total = sum(len(v) for v in created.values())
    print(f"migrate: схема на head, новых партиций: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
