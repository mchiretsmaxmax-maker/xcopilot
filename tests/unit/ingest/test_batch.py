"""Debouncer: окно сдвигается каждым сообщением, ключи независимы, предел пачки."""

from __future__ import annotations

import asyncio

import pytest

from xcopilot.ingest.batch import Debouncer


async def test_debouncer_groups_by_window_and_key() -> None:
    flushed: list[tuple[str, list[int]]] = []

    async def flush(key: str, items: list[int]) -> None:
        flushed.append((key, items))

    deb: Debouncer[str, int] = Debouncer(flush, window_s=0.05)
    await deb.add("a", 1)
    await asyncio.sleep(0.01)
    await deb.add("a", 2)
    await deb.add("b", 3)
    assert deb.pending("a") == 2
    await deb.drain()
    assert sorted(flushed) == [("a", [1, 2]), ("b", [3])]
    await deb.add("a", 4)
    await deb.drain()
    assert flushed[-1] == ("a", [4])


async def test_debouncer_flushes_at_max_items_and_survives_errors() -> None:
    flushed: list[list[int]] = []

    async def flush(key: str, items: list[int]) -> None:
        flushed.append(items)
        if len(flushed) == 1:
            raise RuntimeError("boom")

    deb: Debouncer[str, int] = Debouncer(flush, window_s=10, max_items=2)
    await deb.add("k", 1)
    await deb.add("k", 2)
    await deb.drain()
    assert flushed == [[1, 2]]
    await deb.add("k", 3)
    await deb.add("k", 4)
    await deb.drain()
    assert flushed == [[1, 2], [3, 4]]


def test_debouncer_rejects_bad_settings() -> None:
    async def flush(key: str, items: list[int]) -> None:
        return None

    with pytest.raises(ValueError):
        Debouncer(flush, window_s=-1)
