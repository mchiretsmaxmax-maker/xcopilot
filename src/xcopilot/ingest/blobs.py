"""Временное хранилище файлов между приёмом (accept) и обработкой (process)."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Protocol

_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class BlobStore(Protocol):
    async def put(self, key: str, data: bytes) -> None: ...
    async def get(self, key: str) -> bytes | None: ...
    async def delete(self, key: str) -> None: ...


class MemoryBlobStore:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes) -> None:
        self.data[key] = data

    async def get(self, key: str) -> bytes | None:
        return self.data.get(key)

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)


class FsBlobStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        if not _KEY_RE.match(key) or key.startswith("."):
            raise ValueError(f"недопустимый ключ: {key!r}")
        return self.root / key

    async def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        self.root.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, data)

    async def get(self, key: str) -> bytes | None:
        path = self._path(key)
        if not path.exists():
            return None
        return await asyncio.to_thread(path.read_bytes)

    async def delete(self, key: str) -> None:
        path = self._path(key)
        path.unlink(missing_ok=True)
