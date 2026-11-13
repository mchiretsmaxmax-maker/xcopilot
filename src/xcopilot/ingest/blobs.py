"""Временное хранилище файлов между приёмом (accept) и обработкой (process)."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class BlobStore(Protocol):
    async def put(self, key: str, data: bytes) -> None: ...
    async def get(self, key: str) -> bytes | None: ...
    async def delete(self, key: str) -> None: ...


class MemoryBlobStore:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes) -> None:
        raise NotImplementedError

    async def get(self, key: str) -> bytes | None:
        raise NotImplementedError

    async def delete(self, key: str) -> None:
        raise NotImplementedError


class FsBlobStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    async def put(self, key: str, data: bytes) -> None:
        raise NotImplementedError

    async def get(self, key: str) -> bytes | None:
        raise NotImplementedError

    async def delete(self, key: str) -> None:
        raise NotImplementedError
