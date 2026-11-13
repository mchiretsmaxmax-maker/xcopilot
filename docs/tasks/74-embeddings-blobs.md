## Файлы
- src/xcopilot/embeddings/embedder.py
- src/xcopilot/embeddings/fastembed_impl.py
- src/xcopilot/ingest/blobs.py

## Новые
- (нет)

## Контекст
- tests/slow/test_real_embedder.py
- tests/fakes/embedder.py

## Задача
Сигнатуры и Protocol не меняй.

1. `embeddings/embedder.py`: `batched(items, size=EMBED_BATCH)` → список последовательных пачек длиной `size` (последняя короче), как `list[list[T]]`; `size < 1` → `ValueError`.
2. `embeddings/fastembed_impl.py` (`FastEmbedder`): модель грузится лениво при первом `embed`:
   `from fastembed import TextEmbedding` внутри метода `_model()`, хранить в `self._impl` (в `__init__` `self._impl: Any = None`).
   `embed(texts)`: пустой список → `[]`; иначе `[[float(x) for x in v] for v in self._model().embed(list(texts), batch_size=len(texts))]`; проверить, что длина каждого вектора == `self.dim`, иначе `ValueError(f"размерность {len(v)} != {self.dim}")`.
   Если mypy ругается на fastembed без типов — `# type: ignore[import-untyped]` на строку импорта.
3. `ingest/blobs.py`:
   - `MemoryBlobStore`: put → `self.data[key] = data`; get → `self.data.get(key)`; delete → `self.data.pop(key, None)`.
   - `FsBlobStore`: путь файла `self.root / key`; ключ должен соответствовать `^[A-Za-z0-9_.-]+$` и не начинаться с точки, иначе `ValueError`. put: `root.mkdir(parents=True, exist_ok=True)`, запись через `asyncio.to_thread(path.write_bytes, data)`; get: файла нет → None, иначе `await asyncio.to_thread(path.read_bytes)`; delete: `path.unlink(missing_ok=True)`.

## Коммит
feat(embeddings): батчи, fastembed, временное хранилище файлов
