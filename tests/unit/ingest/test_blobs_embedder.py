"""Временное хранилище файлов, пачки и обёртка fastembed (без скачивания модели)."""

from __future__ import annotations

import sys
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from xcopilot.embeddings.embedder import batched
from xcopilot.embeddings.fastembed_impl import FastEmbedder
from xcopilot.ingest.blobs import FsBlobStore, MemoryBlobStore
from xcopilot.ingest.parsers import text


async def test_memory_and_fs_blob_stores(tmp_path: Path) -> None:
    for store in (MemoryBlobStore(), FsBlobStore(tmp_path / "blobs")):
        assert await store.get("a1") is None
        await store.put("a1", b"data")
        assert await store.get("a1") == b"data"
        await store.delete("a1")
        await store.delete("a1")
        assert await store.get("a1") is None
    fs = FsBlobStore(tmp_path)
    for bad in ("../x", ".hidden", "a/b", ""):
        with pytest.raises(ValueError):
            await fs.put(bad, b"x")


def test_batched() -> None:
    assert batched(list(range(70))) == [list(range(32)), list(range(32, 64)), list(range(64, 70))]
    assert batched([], 32) == []
    with pytest.raises(ValueError):
        batched([1], 0)


class _FakeTextEmbedding:
    instances = 0

    def __init__(self, model_name: str) -> None:
        type(self).instances += 1
        self.model_name = model_name

    def embed(self, texts: list[str], batch_size: int) -> Iterator[list[float]]:
        for t in texts:
            yield [float(len(t))] * (3 if t != "bad" else 2)


@pytest.fixture
def fake_fastembed(monkeypatch: pytest.MonkeyPatch) -> None:
    module: Any = types.ModuleType("fastembed")
    module.TextEmbedding = _FakeTextEmbedding
    monkeypatch.setitem(sys.modules, "fastembed", module)


@pytest.mark.usefixtures("fake_fastembed")
def test_fastembedder_lazy_and_checks_dim() -> None:
    _FakeTextEmbedding.instances = 0
    emb = FastEmbedder(model_name="m", dim=3)
    assert emb.embed([]) == []
    assert _FakeTextEmbedding.instances == 0  # модель грузится лениво
    assert emb.embed(["ab", "c"]) == [[2.0] * 3, [1.0] * 3]
    emb.embed(["x"])
    assert _FakeTextEmbedding.instances == 1
    with pytest.raises(ValueError):
        emb.embed(["bad"])


def test_text_parser_fallbacks() -> None:
    assert text.parse("﻿привет".encode())[0].text == "привет"
    assert text.parse(b"\x98\xff")[0].text  # не utf-8 и не cp1251 — с заменой символов


def test_blob_store_dir_from_settings(tmp_path: Path) -> None:
    from xcopilot.config import Settings
    from xcopilot.ingest.factory import build_blob_store

    settings = Settings(
        bot_token="123456:FAKE-TOKEN-for-tests",
        database_url="postgresql+asyncpg://unused",
        data_dir=tmp_path,
    )
    assert build_blob_store(settings).root == tmp_path / "ingest"
    default = Settings(bot_token="123456:FAKE-TOKEN-for-tests", database_url="postgresql://u")
    assert default.ingest_dir == Path("data") / "ingest"
