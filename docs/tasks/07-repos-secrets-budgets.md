## Файлы
- src/xcopilot/db/repos/secrets.py
- src/xcopilot/db/repos/budgets.py

## Новые
- (нет)

## Контекст
- src/xcopilot/db/repos/base.py
- src/xcopilot/db/repos/sources.py
- src/xcopilot/db/models/access.py
- src/xcopilot/db/models/ops.py
- src/xcopilot/security/crypto.py
- tests/integration/db/test_repos_api.py

## Задача
1. `secrets.py`, класс `SecretsRepo` (модель `Secret`):
   - `async def put(self, kind: str, plain: str, box: SecretBox) -> Secret`: зашифровать `box.encrypt(plain)`; если строка своего аккаунта с этим `kind` уже есть — обновить её `ciphertext` и `key_version` через `self.update(...)`, иначе `self.add(kind=kind, ciphertext=..., key_version=box.key_version)`. Вернуть строку. Открытый текст никуда не пишется и не логируется.
   - `async def get_plain(self, kind: str, box: SecretBox) -> str | None`: найти строку своего аккаунта по `kind` (`self.scoped().where(Secret.kind == kind)`); нет — `None`; есть — `box.decrypt(row.ciphertext)`.
   - Вспомогательный `async def get_by_kind(self, kind: str) -> Secret | None` — тот же поиск; `put` и `get_plain` используют его.
   - `SecretBox` импортируй из `xcopilot.security.crypto`.
2. `budgets.py`, класс `BudgetsRepo` (модель `Budget`):
   - `async def get_limit(self, scope: str, period: str) -> Budget | None` — строка своего аккаунта по `scope` и `period`.
   - `async def upsert(self, scope: str, period: str, *, limit_micro_usd: int, hard: bool = True) -> Budget`: если `get_limit` нашёл строку — `self.update(id, limit_micro_usd=..., hard=...)`, иначе `self.add(scope=..., period=..., limit_micro_usd=..., hard=...)`. Вернуть строку.
3. Должны пройти `test_secrets_repo_stores_only_ciphertext` и `test_budgets` из контекста.

Общие правила (для всех методов):
- Класс уже есть в файле: `class XRepo(TenantRepo[Model])` с `model = Model`. Не меняй его имя, базу и `model`.
- Образец стиля — `src/xcopilot/db/repos/sources.py`. Используй методы базы `TenantRepo` из `src/xcopilot/db/repos/base.py`: `self.scoped()` (SELECT, уже ограниченный своим account_id), `self.get`, `self.require` (NotFound для чужого/несуществующего id), `self.add(**values)`, `self.update(id_, **values)`, `self.list()`.
- Любой запрос к таблице модели строй через `self.scoped()` или `self.update()` — строки чужого аккаунта никогда не читаются и не меняются. Новую строку другой модели создавай с `account_id=self.account_id`, затем `self.session.add(obj)`, `await self.session.flush()`, `await self.session.refresh(obj)`.
- Импорты: `from __future__ import annotations`, модели — только из `xcopilot.db.models`, ошибки — из `xcopilot.errors`. SQL только через SQLAlchemy (select/update/insert), без строк `text(...)`.
- Каждый метод async, с type hints (mypy --strict), docstring одной строкой по-русски.
- Не добавляй других методов, файлов и зависимостей. Не меняй base.py, sources.py, модели и тесты.

Проверка: `PYTEST_ADDOPTS='tests/integration/db -k "test_secrets_repo or test_budgets or test_repo_requires"'`.

## Коммит
feat(db): SecretsRepo и BudgetsRepo
