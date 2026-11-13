## Файлы
- src/xcopilot/db/repos/reviews.py
- src/xcopilot/db/repos/publications.py

## Новые
- (нет)

## Контекст
- src/xcopilot/db/repos/base.py
- src/xcopilot/db/repos/sources.py
- src/xcopilot/db/models/content.py
- tests/integration/db/test_repos_api.py

## Задача
1. `reviews.py`, `ReviewsRepo` (модель `Review`):
   - `async def list_by_draft(self, draft_id: UUID) -> list[Review]`: ревью своего аккаунта этого черновика по возрастанию `Review.id`.
2. `publications.py`, `PublicationsRepo` (модель `Publication`):
   - `async def get_by_x_post_id(self, x_post_id: str) -> Publication | None`.
   - `async def get_by_draft(self, draft_id: UUID) -> Publication | None`.
   Оба — через `self.scoped().where(...)`, `scalar_one_or_none()`.
3. Должен пройти `test_runs_drafts_reviews_publications`.

Общие правила (для всех методов):
- Класс уже есть в файле: `class XRepo(TenantRepo[Model])` с `model = Model`. Не меняй его имя, базу и `model`.
- Образец стиля — `src/xcopilot/db/repos/sources.py`. Используй методы базы `TenantRepo` из `src/xcopilot/db/repos/base.py`: `self.scoped()` (SELECT, уже ограниченный своим account_id), `self.get`, `self.require` (NotFound для чужого/несуществующего id), `self.add(**values)`, `self.update(id_, **values)`, `self.list()`.
- Любой запрос к таблице модели строй через `self.scoped()` или `self.update()` — строки чужого аккаунта никогда не читаются и не меняются. Новую строку другой модели создавай с `account_id=self.account_id`, затем `self.session.add(obj)`, `await self.session.flush()`, `await self.session.refresh(obj)`.
- Импорты: `from __future__ import annotations`, модели — только из `xcopilot.db.models`, ошибки — из `xcopilot.errors`. SQL только через SQLAlchemy (select/update/insert), без строк `text(...)`.
- Каждый метод async, с type hints (mypy --strict), docstring одной строкой по-русски.
- Не добавляй других методов, файлов и зависимостей. Не меняй base.py, sources.py, модели и тесты.

Проверка: `PYTEST_ADDOPTS='tests/integration/db -k "test_runs_drafts"'`.

## Коммит
feat(db): ReviewsRepo и PublicationsRepo
