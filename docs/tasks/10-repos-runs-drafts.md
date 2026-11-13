## Файлы
- src/xcopilot/db/repos/runs.py
- src/xcopilot/db/repos/drafts.py

## Новые
- (нет)

## Контекст
- src/xcopilot/db/repos/base.py
- src/xcopilot/db/repos/sources.py
- src/xcopilot/db/models/content.py
- tests/integration/db/test_repos_api.py
- tests/integration/db/test_repos.py

## Задача
1. `runs.py`, `RunsRepo` (модель `GenerationRun`):
   - `async def finish(self, run_id: UUID, *, status: str, finished_at: datetime, error: str | None = None) -> GenerationRun`: `self.update(run_id, status=status, finished_at=finished_at, error=error)`.
2. `drafts.py`, `DraftsRepo` (модель `Draft`):
   - `async def list_by_run(self, run_id: UUID) -> list[Draft]`: черновики своего аккаунта этого запуска, порядок: `variant_no`, `revision`, `id` по возрастанию.
   - `async def add_revision(self, parent_id: UUID, *, text: str, parts: list[Any] | None = None) -> Draft`: `parent = await self.require(parent_id)` (чужой → NotFound); вернуть `await self.add(run_id=parent.run_id, variant_no=parent.variant_no, revision=parent.revision + 1, parent_draft_id=parent.id, format=parent.format, reply_to_url=parent.reply_to_url, text=text, parts=parts if parts is not None else [])`. Статус новой ревизии — по умолчанию из БД (`generated`), не задавай его.
   - `async def set_status(self, draft_id: UUID, new: DraftStatus) -> Draft`: `draft = await self.require(draft_id)`; `assert_transition(DraftStatus(draft.status), new)` (бросает InvalidTransition); `return await self.update(draft_id, status=new.value)`.
   - `DraftStatus`, `assert_transition` импортируй из `xcopilot.db.models.content`.
3. Должны пройти `test_runs_drafts_reviews_publications` не обязательно (там нужны ещё ReviewsRepo/PublicationsRepo), но обязательно `test_draft_revision_links_to_parent`.

Общие правила (для всех методов):
- Класс уже есть в файле: `class XRepo(TenantRepo[Model])` с `model = Model`. Не меняй его имя, базу и `model`.
- Образец стиля — `src/xcopilot/db/repos/sources.py`. Используй методы базы `TenantRepo` из `src/xcopilot/db/repos/base.py`: `self.scoped()` (SELECT, уже ограниченный своим account_id), `self.get`, `self.require` (NotFound для чужого/несуществующего id), `self.add(**values)`, `self.update(id_, **values)`, `self.list()`.
- Любой запрос к таблице модели строй через `self.scoped()` или `self.update()` — строки чужого аккаунта никогда не читаются и не меняются. Новую строку другой модели создавай с `account_id=self.account_id`, затем `self.session.add(obj)`, `await self.session.flush()`, `await self.session.refresh(obj)`.
- Импорты: `from __future__ import annotations`, модели — только из `xcopilot.db.models`, ошибки — из `xcopilot.errors`. SQL только через SQLAlchemy (select/update/insert), без строк `text(...)`.
- Каждый метод async, с type hints (mypy --strict), docstring одной строкой по-русски.
- Не добавляй других методов, файлов и зависимостей. Не меняй base.py, sources.py, модели и тесты.

Проверка: `PYTEST_ADDOPTS='tests/integration/db -k "test_draft_revision"'`.

## Коммит
feat(db): RunsRepo и DraftsRepo
