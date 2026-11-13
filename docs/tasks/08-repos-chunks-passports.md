## Файлы
- src/xcopilot/db/repos/chunks.py
- src/xcopilot/db/repos/passports.py

## Новые
- (нет)

## Контекст
- src/xcopilot/db/repos/base.py
- src/xcopilot/db/repos/sources.py
- src/xcopilot/db/models/knowledge.py
- src/xcopilot/db/models/derived.py
- tests/integration/db/test_repos_api.py
- tests/integration/db/test_repos.py

## Задача
1. `chunks.py`, класс `ChunksRepo` (модель `Chunk`; ещё нужны `Document`, `Embedding`):
   - Создание куска — унаследованный `self.add(document_id=..., idx=..., text=..., tags=..., lang=...)`; отдельный метод не нужен.
   - `async def list_by_document(self, document_id: UUID) -> list[Chunk]`: куски своего аккаунта этого документа, по возрастанию `Chunk.idx`.
   - `async def add_embedding(self, chunk_id: UUID, *, model_id: UUID, vec: Sequence[float]) -> Embedding`: сначала `await self.require(chunk_id)` (чужой кусок → NotFound); затем создать `Embedding(account_id=self.account_id, chunk_id=chunk_id, model_id=model_id, vec=list(vec))`, add/flush/refresh, вернуть. `Sequence` — из `collections.abc`.
2. `passports.py`, класс `PassportsRepo` (модель `StylePassport`):
   - `async def get_active(self) -> StylePassport | None`: паспорт своего аккаунта со `status == "active"`.
   - `async def next_version(self) -> int`: `max(version) + 1` по своему аккаунту, `1` если паспортов нет (`select(func.max(StylePassport.version)).where(StylePassport.account_id == self.account_id)`).
   - `async def activate(self, passport_id: UUID) -> StylePassport`: `await self.require(passport_id)`; текущий активный (если он есть и это не тот же id) перевести в `status="archived"` через `self.update`, затем выполнить `await self.session.flush()`, затем `self.update(passport_id, status="active")` и вернуть результат. Порядок важен: частичный уникальный индекс допускает только один active.
3. Должны пройти `test_sources_documents_chunks_embeddings`, `test_passports_activate`, `test_only_one_active_passport_per_account`.

Общие правила (для всех методов):
- Класс уже есть в файле: `class XRepo(TenantRepo[Model])` с `model = Model`. Не меняй его имя, базу и `model`.
- Образец стиля — `src/xcopilot/db/repos/sources.py`. Используй методы базы `TenantRepo` из `src/xcopilot/db/repos/base.py`: `self.scoped()` (SELECT, уже ограниченный своим account_id), `self.get`, `self.require` (NotFound для чужого/несуществующего id), `self.add(**values)`, `self.update(id_, **values)`, `self.list()`.
- Любой запрос к таблице модели строй через `self.scoped()` или `self.update()` — строки чужого аккаунта никогда не читаются и не меняются. Новую строку другой модели создавай с `account_id=self.account_id`, затем `self.session.add(obj)`, `await self.session.flush()`, `await self.session.refresh(obj)`.
- Импорты: `from __future__ import annotations`, модели — только из `xcopilot.db.models`, ошибки — из `xcopilot.errors`. SQL только через SQLAlchemy (select/update/insert), без строк `text(...)`.
- Каждый метод async, с type hints (mypy --strict), docstring одной строкой по-русски.
- Не добавляй других методов, файлов и зависимостей. Не меняй base.py, sources.py, модели и тесты.

Проверка: `PYTEST_ADDOPTS='tests/integration/db -k "test_sources_documents or test_passports or test_only_one_active"'`.

## Коммит
feat(db): ChunksRepo и PassportsRepo
