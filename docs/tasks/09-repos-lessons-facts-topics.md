## Файлы
- src/xcopilot/db/repos/lessons.py
- src/xcopilot/db/repos/facts.py
- src/xcopilot/db/repos/topics.py

## Новые
- (нет)

## Контекст
- src/xcopilot/db/repos/base.py
- src/xcopilot/db/repos/sources.py
- src/xcopilot/db/models/derived.py
- src/xcopilot/db/models/content.py
- tests/integration/db/test_repos_api.py

## Задача
1. `lessons.py`, `LessonsRepo` (модель `Lesson`):
   - `async def list_by_status(self, status: str) -> list[Lesson]`: уроки своего аккаунта с этим статусом, по возрастанию `Lesson.id`.
   - `async def decide(self, lesson_id: UUID, *, status: str, decided_by: UUID | None, decided_at: datetime) -> Lesson`: `self.update(lesson_id, status=status, decided_by=decided_by, decided_at=decided_at)`.
2. `facts.py`, `FactsRepo` (модель `Fact`):
   - `async def list_valid(self, now: datetime) -> list[Fact]`: факты своего аккаунта, у которых `expires_at IS NULL` или `expires_at > now` (`or_(Fact.expires_at.is_(None), Fact.expires_at > now)`), по возрастанию `Fact.id`.
3. `topics.py`, `TopicsRepo` (модель `Topic`):
   - `async def list_by_status(self, status: str) -> list[Topic]`: по возрастанию `Topic.id`.
   - `async def set_status(self, topic_id: UUID, status: str) -> Topic`: `self.update(topic_id, status=status)`.
4. `datetime` — из `datetime`, `UUID` — из `uuid`.
5. Должен пройти `test_lessons_facts_topics` и `test_repo_cannot_update_other_account`.

Общие правила (для всех методов):
- Класс уже есть в файле: `class XRepo(TenantRepo[Model])` с `model = Model`. Не меняй его имя, базу и `model`.
- Образец стиля — `src/xcopilot/db/repos/sources.py`. Используй методы базы `TenantRepo` из `src/xcopilot/db/repos/base.py`: `self.scoped()` (SELECT, уже ограниченный своим account_id), `self.get`, `self.require` (NotFound для чужого/несуществующего id), `self.add(**values)`, `self.update(id_, **values)`, `self.list()`.
- Любой запрос к таблице модели строй через `self.scoped()` или `self.update()` — строки чужого аккаунта никогда не читаются и не меняются. Новую строку другой модели создавай с `account_id=self.account_id`, затем `self.session.add(obj)`, `await self.session.flush()`, `await self.session.refresh(obj)`.
- Импорты: `from __future__ import annotations`, модели — только из `xcopilot.db.models`, ошибки — из `xcopilot.errors`. SQL только через SQLAlchemy (select/update/insert), без строк `text(...)`.
- Каждый метод async, с type hints (mypy --strict), docstring одной строкой по-русски.
- Не добавляй других методов, файлов и зависимостей. Не меняй base.py, sources.py, модели и тесты.

Проверка: `PYTEST_ADDOPTS='tests/integration/db -k "test_lessons_facts_topics or test_repo_cannot_update"'`.

## Коммит
feat(db): LessonsRepo, FactsRepo, TopicsRepo
