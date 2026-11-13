"""Проверка доступности базы данных."""

import asyncio
import sys

from xcopilot.config import load_settings
from xcopilot.db.engine import ping


async def check_db(database_url: str) -> None:
    """Проверить подключение к базе данных."""
    await ping(database_url)


def main() -> int:
    """Выполнить healthcheck базы данных."""
    try:
        settings = load_settings()
        asyncio.run(check_db(settings.database_url))
    except Exception as exc:
        print(f"healthcheck: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
