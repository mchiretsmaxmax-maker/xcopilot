# ADR-0006: БД-ядро — решения этапа 2

Статус: принято (этап 2).

## Решения
1. **Тенант.** `account_id NOT NULL` + FK на `accounts` + индекс, начинающийся с него, — во всех таблицах, кроме глобальных `users`, `embedding_models`, `metric_definitions`, `prompts` и корня тенантов `accounts`. `bot_sessions` тоже несёт `account_id` (до выбора аккаунта — системный `global`).
2. **Слои.** SQL и импорт моделей — только в пакете `xcopilot.db` (репозитории, миграции, партиции, генератор docs). Прикладной код ходит в БД через `xcopilot.db.repos` + `UnitOfWork`; модуль `jobs/queue.py` — через `db/repos/jobs.py`. Проверяет `tests/unit/test_layering.py`. Healthcheck перенесён на `db.engine.ping`.
3. **Репозитории.** `TenantRepo[M]`: все чтения и изменения с `account_id = :свой`; чужая строка — `get → None`, `update → NotFound`; `id`/`account_id` менять нельзя. `AccountsRepo`, `UsersRepo`, `JobsRepo` — глобальные.
4. **Ключи.** `DEFAULT uuidv7()` в БД (PG 18). Журналы (`events`, `post_metric_snapshots`, `llm_calls`, `x_api_calls`) — PK `(ts, id)`, партиции по `ts`.
5. **Партиции.** Миграция создаёт только `<таблица>_default`; месячные `<таблица>_pYYYYMM` создаёт `ensure_partitions` (при `python -m xcopilot.db.migrate` и ежедневным расписанием `db.ensure_partitions` в 03:00, сид). Строки, попавшие в DEFAULT, переносятся в новую партицию при её создании. Автогенерация Alembic партиции игнорирует (`include_object`).
6. **Сиды** — миграция `0002`: аккаунт `global`, модель `paraphrase-multilingual-MiniLM-L12-v2` (384), лимиты LLM $5/день и X $1/день (микродоллары), расписание партиций.
7. **Секреты.** `SecretBox`: Fernet/MultiFernet, `SECRETS_KEY` = «ключ» или «1:k1,2:k2»; шифруем старшей версией, читаем любой; `key_version` в `secrets`.
8. **Очередь.** `claim` — `UPDATE … WHERE id IN (SELECT … FOR UPDATE SKIP LOCKED)`, попытка считается при взятии; пауза `base·2^(n−1)` ≤ `max_delay`; после `max_attempts` — `dead`; `release_stale` возвращает просроченные блокировки (исчерпавшие попытки — `dead`). Время — из `Clock`.
9. **Бэкап.** `pg_dump -Fc`/`pg_restore` всегда внутри контейнера БД (версия клиента = сервера). Восстановление — одной транзакцией через `psql -1` с пересозданием схемы `public` (`pg_restore --clean` ломается на партициях).
10. **Миграции в контейнере.** `make migrate` / `make db-reset` запускают `python -m xcopilot.db.migrate` в образе `app` (порт БД наружу не публикуется). Имя проекта compose — `xcopilot`.
