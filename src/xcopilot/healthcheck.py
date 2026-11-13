"""Проверка доступности базы данных."""

import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from xcopilot.config import load_settings


async def check_db(database_url: str) -> None:
    """Проверить подключение к базе данных."""
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as conn:
            async with asyncio.timeout(3.0):
                await conn.execute(text("SELECT 1"))
    finally:
        await engine.dispose()


def main() -> int:
    """Выполнить healthcheck базы данных."""
    settings = load_settings()
    try:
        asyncio.run(check_db(settings.database_url))
    except Exception as exc:
        print(f"healthcheck: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
