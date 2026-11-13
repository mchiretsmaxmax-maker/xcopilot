# Схема БД

> Генерируется командой `make db-docs` из моделей (`xcopilot.db.docs`). Не редактировать вручную.

Таблиц: 31.

## Оглавление

- `account_members` — Кто к какому аккаунту имеет доступ и с какой ролью
- `account_metric_snapshots` — Метрики аккаунта X по дням
- `accounts` — X-аккаунты (тенанты); глобальные данные принадлежат аккаунту kind=global
- `bot_sessions` — Состояние диалогов бота (FSM) пользователя в аккаунте
- `budgets` — Лимиты расходов
- `chunks` — Куски документов для поиска (полнотекстового, триграммного, векторного)
- `competitors` — Аккаунты X, за которыми следим
- `documents` — Текст, извлечённый из источника
- `drafts` — Черновики и их ревизии (ревизия ссылается на родителя)
- `embedding_models` — Реестр моделей эмбеддингов (глобальный)
- `embeddings` — Векторы кусков (pgvector, HNSW по косинусу)
- `events` — Журнал всех событий продукта; только добавление, партиции по месяцам
- `facts` — Проверяемые факты с источником и сроком годности
- `generation_runs` — Один запуск генерации черновиков
- `insights` — Выводы аналитики со ссылками на данные
- `jobs` — Очередь фоновых задач (claim через FOR UPDATE SKIP LOCKED)
- `lessons` — Правила из правок и отказов владельца
- `llm_calls` — Журнал вызовов моделей; только добавление, партиции по месяцам
- `metric_definitions` — Каталог метрик; формулы грузятся из metrics/definitions/*.sql
- `metric_values` — Суточные и недельные свёртки метрик
- `post_metric_snapshots` — Временной ряд метрик поста X; только добавление, партиции по месяцам
- `prompts` — Версии промптов (глобальные)
- `publications` — Связь черновика с опубликованным постом X
- `reviews` — Решения владельца по черновикам; пары «до/после»
- `schedules` — Расписания периодических задач по поясу аккаунта
- `secrets` — Зашифрованные (Fernet) токены и ключи аккаунта
- `sources` — Всё, что пришло в систему: посты, образцы, файлы, заметки
- `style_passports` — Паспорт голоса аккаунта; версии, активна ровно одна
- `topics` — Темы для постов
- `users` — Люди в Telegram, которые работают с ботом
- `x_api_calls` — Журнал вызовов X API; только добавление, партиции по месяцам

## `account_members`

Кто к какому аккаунту имеет доступ и с какой ролью

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | Идентификатор (UUIDv7, упорядочен по времени) |
| `account_id` | UUID | нет |  | Аккаунт-владелец (тенант) |
| `user_id` | UUID | нет |  | Пользователь |
| `role` | VARCHAR | нет |  | Роль: owner / editor / viewer |
| `created_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Когда создано (UTC) |

Первичный ключ: `id`
Внешние ключи: `account_id` → `accounts.id`, `user_id` → `users.id`
Индексы:
- `ix_account_members_user_id` (user_id)

## `account_metric_snapshots`

Метрики аккаунта X по дням

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | Идентификатор (UUIDv7, упорядочен по времени) |
| `account_id` | UUID | нет |  | Аккаунт-владелец (тенант) |
| `date` | DATE | нет |  | День по поясу аккаунта |
| `followers` | INTEGER | да |  | Подписчики |
| `following` | INTEGER | да |  | Подписки |
| `posts_count` | INTEGER | да |  | Всего постов |
| `created_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Когда создано (UTC) |

Первичный ключ: `id`
Внешние ключи: `account_id` → `accounts.id`

## `accounts`

X-аккаунты (тенанты); глобальные данные принадлежат аккаунту kind=global

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | Идентификатор (UUIDv7, упорядочен по времени) |
| `kind` | VARCHAR | нет | `'x'` | Тип: x — аккаунт X, global — системный |
| `handle` | VARCHAR | нет |  | Имя в X без @ (у системного — global) |
| `x_user_id` | VARCHAR | да |  | Числовой ID пользователя в X |
| `niche` | VARCHAR | да |  | Ниша аккаунта своими словами |
| `lang` | VARCHAR | нет | `'ru'` | Основной язык постов |
| `tz` | VARCHAR | нет | `'Europe/Minsk'` | Часовой пояс IANA для отчётов и расписаний |
| `x_read_mode` | VARCHAR | нет | `'owned'` | Режим чтения X: off / owned / paid |
| `settings` | JSONB | нет | `'{}'::jsonb` | Настройки аккаунта (схема pydantic) |
| `status` | VARCHAR | нет | `'active'` | Состояние: active / paused / archived |
| `created_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Когда создано (UTC) |
| `updated_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Когда изменено (UTC) |

Первичный ключ: `id`

## `bot_sessions`

Состояние диалогов бота (FSM) пользователя в аккаунте

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | Идентификатор (UUIDv7, упорядочен по времени) |
| `account_id` | UUID | нет |  | Аккаунт диалога; до выбора аккаунта — системный global |
| `user_id` | UUID | нет |  | Пользователь |
| `state` | VARCHAR | да |  | Состояние FSM |
| `data` | JSONB | нет | `'{}'::jsonb` | Данные FSM |
| `updated_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Когда изменено (UTC) |

Первичный ключ: `id`
Внешние ключи: `account_id` → `accounts.id`, `user_id` → `users.id`

## `budgets`

Лимиты расходов

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | Идентификатор (UUIDv7, упорядочен по времени) |
| `account_id` | UUID | нет |  | Аккаунт-владелец (тенант) |
| `scope` | VARCHAR | нет |  | На что: llm / x_api / sniper |
| `period` | VARCHAR | нет |  | За какой период: day / month |
| `limit_micro_usd` | BIGINT | нет |  | Лимит, микродоллары |
| `hard` | BOOLEAN | нет | `true` | Жёсткий: при превышении вызовы блокируются |
| `created_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Когда создано (UTC) |
| `updated_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Когда изменено (UTC) |

Первичный ключ: `id`
Внешние ключи: `account_id` → `accounts.id`

## `chunks`

Куски документов для поиска (полнотекстового, триграммного, векторного)

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | Идентификатор (UUIDv7, упорядочен по времени) |
| `account_id` | UUID | нет |  | Аккаунт-владелец (тенант) |
| `document_id` | UUID | нет |  | Документ, из которого кусок |
| `idx` | INTEGER | нет |  | Порядковый номер куска в документе, с 0 |
| `text` | VARCHAR | нет |  | Текст куска |
| `tags` | TEXT[] | нет | `'{}'::text[]` | Метки (тема, формат, приём) |
| `lang` | VARCHAR | да |  | Язык куска |
| `tsv` | TSVECTOR | нет | `генерируется: to_tsvector('simple'::regconfig, text)` | Полнотекстовый вектор (генерируется из text) |
| `created_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Когда создано (UTC) |

Первичный ключ: `id`
Внешние ключи: `account_id` → `accounts.id`, `document_id` → `documents.id`
Индексы:
- `ix_chunks_text_trgm` (text) gin
- `ix_chunks_tsv` (tsv) gin

## `competitors`

Аккаунты X, за которыми следим

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | Идентификатор (UUIDv7, упорядочен по времени) |
| `account_id` | UUID | нет |  | Аккаунт-владелец (тенант) |
| `handle` | VARCHAR | нет |  | Имя в X без @ |
| `x_user_id` | VARCHAR | да |  | Числовой ID пользователя в X |
| `priority` | INTEGER | нет | `0` | Приоритет опроса (больше — чаще) |
| `active` | BOOLEAN | нет | `true` | Следим ли сейчас |
| `since_id` | VARCHAR | да |  | Последний прочитанный ID поста |
| `created_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Когда создано (UTC) |

Первичный ключ: `id`
Внешние ключи: `account_id` → `accounts.id`

## `documents`

Текст, извлечённый из источника

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | Идентификатор (UUIDv7, упорядочен по времени) |
| `account_id` | UUID | нет |  | Аккаунт-владелец (тенант) |
| `source_id` | UUID | нет |  | Источник текста |
| `text` | VARCHAR | нет |  | Исходный извлечённый текст |
| `text_clean` | VARCHAR | да |  | Текст после очистки |
| `token_count` | INTEGER | да |  | Число токенов в очищенном тексте |
| `created_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Когда создано (UTC) |

Первичный ключ: `id`
Внешние ключи: `account_id` → `accounts.id`, `source_id` → `sources.id`
Индексы:
- `ix_documents_account_id_source_id` (account_id, source_id)

## `drafts`

Черновики и их ревизии (ревизия ссылается на родителя)

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | Идентификатор (UUIDv7, упорядочен по времени) |
| `account_id` | UUID | нет |  | Аккаунт-владелец (тенант) |
| `run_id` | UUID | нет |  | Запуск генерации |
| `variant_no` | INTEGER | нет | `1` | Номер варианта в запуске, с 1 |
| `revision` | INTEGER | нет | `1` | Номер ревизии варианта, с 1 |
| `parent_draft_id` | UUID | да |  | Предыдущая ревизия |
| `format` | VARCHAR | нет |  | Формат: post / thread / reply / quote и т. п. |
| `text` | VARCHAR | нет |  | Текст черновика |
| `parts` | JSONB | нет | `'[]'::jsonb` | Части треда по порядку |
| `reply_to_url` | VARCHAR | да |  | Пост, на который отвечаем |
| `status` | VARCHAR | нет | `'generated'` | Статус по машине DraftStatus |
| `lint_report` | JSONB | да |  | Отчёт линтера |
| `judge_verdict` | JSONB | да |  | Вердикт судьи |
| `created_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Когда создано (UTC) |
| `updated_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Когда изменено (UTC) |

Первичный ключ: `id`
Внешние ключи: `account_id` → `accounts.id`, `parent_draft_id` → `drafts.id`, `run_id` → `generation_runs.id`
Индексы:
- `ix_drafts_account_id_parent_draft_id` (account_id, parent_draft_id)
- `ix_drafts_account_id_status_created_at` (account_id, status, created_at)

## `embedding_models`

Реестр моделей эмбеддингов (глобальный)

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | Идентификатор (UUIDv7, упорядочен по времени) |
| `name` | VARCHAR | нет |  | Имя модели, например в fastembed |
| `dim` | INTEGER | нет |  | Размерность вектора |
| `active` | BOOLEAN | нет | `false` | Используется для новых векторов и поиска |
| `created_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Когда создано (UTC) |

Первичный ключ: `id`

## `embeddings`

Векторы кусков (pgvector, HNSW по косинусу)

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | Идентификатор (UUIDv7, упорядочен по времени) |
| `account_id` | UUID | нет |  | Аккаунт-владелец (тенант) |
| `chunk_id` | UUID | нет |  | Кусок |
| `model_id` | UUID | нет |  | Модель, которой посчитан вектор |
| `vec` | VECTOR(384) | нет |  | Вектор (384 измерения) |
| `created_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Когда создано (UTC) |

Первичный ключ: `id`
Внешние ключи: `account_id` → `accounts.id`, `chunk_id` → `chunks.id`, `model_id` → `embedding_models.id`
Индексы:
- `ix_embeddings_vec_hnsw` (vec) hnsw

## `events`

Журнал всех событий продукта; только добавление, партиции по месяцам

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | Идентификатор (UUIDv7) |
| `account_id` | UUID | нет |  | Аккаунт-владелец (тенант) |
| `ts` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Когда произошло (UTC); ключ партиции |
| `type` | VARCHAR | нет |  | Тип события, например review.approved |
| `actor` | VARCHAR | нет |  | Кто: user / system / model |
| `user_id` | UUID | да |  | Пользователь, если actor = user |
| `entity_type` | VARCHAR | да |  | Тип сущности, например draft |
| `entity_id` | UUID | да |  | ID сущности |
| `props` | JSONB | нет | `'{}'::jsonb` | Свойства события |

Первичный ключ: `ts`, `id`
Внешние ключи: `account_id` → `accounts.id`
Индексы:
- `ix_events_account_id_entity` (account_id, entity_type, entity_id)
- `ix_events_account_id_type_ts` (account_id, type, ts)
Партиции: `RANGE (ts)` по месяцам + DEFAULT.

## `facts`

Проверяемые факты с источником и сроком годности

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | Идентификатор (UUIDv7, упорядочен по времени) |
| `account_id` | UUID | нет |  | Аккаунт-владелец (тенант) |
| `statement` | VARCHAR | нет |  | Утверждение |
| `value` | VARCHAR | да |  | Значение (число или текст) |
| `unit` | VARCHAR | да |  | Единица измерения |
| `source_url` | VARCHAR | нет |  | Где проверено |
| `observed_at` | TIMESTAMP WITH TIME ZONE | нет |  | Когда факт был верен (UTC) |
| `expires_at` | TIMESTAMP WITH TIME ZONE | да |  | После этого момента не использовать |
| `created_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Когда создано (UTC) |

Первичный ключ: `id`
Внешние ключи: `account_id` → `accounts.id`
Индексы:
- `ix_facts_account_id_expires_at` (account_id, expires_at)

## `generation_runs`

Один запуск генерации черновиков

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | Идентификатор (UUIDv7, упорядочен по времени) |
| `account_id` | UUID | нет |  | Аккаунт-владелец (тенант) |
| `topic_id` | UUID | да |  | Тема (у ответов может не быть) |
| `kind` | VARCHAR | нет |  | post / thread / reply / quote / trend_tail |
| `context_pack` | JSONB | нет | `'{}'::jsonb` | ID кусков и уроков, попавших в контекст |
| `passport_version` | INTEGER | да |  | Версия паспорта стиля |
| `prompt_id` | UUID | да |  | Версия промпта писателя |
| `status` | VARCHAR | нет | `'running'` | running / done / failed |
| `error` | VARCHAR | да |  | Текст ошибки (без секретов) |
| `created_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Когда создано (UTC) |
| `finished_at` | TIMESTAMP WITH TIME ZONE | да |  | Когда закончен (UTC) |

Первичный ключ: `id`
Внешние ключи: `account_id` → `accounts.id`, `prompt_id` → `prompts.id`, `topic_id` → `topics.id`
Индексы:
- `ix_generation_runs_account_id_created_at` (account_id, created_at)

## `insights`

Выводы аналитики со ссылками на данные

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | Идентификатор (UUIDv7, упорядочен по времени) |
| `account_id` | UUID | нет |  | Аккаунт-владелец (тенант) |
| `kind` | VARCHAR | нет |  | Тип вывода, например best_time или format_lift |
| `body` | JSONB | нет | `'{}'::jsonb` | Содержимое вывода (схема pydantic) |
| `source_refs` | JSONB | нет | `'[]'::jsonb` | Ссылки на данные, из которых сделан вывод |
| `confidence` | DOUBLE PRECISION | да |  | Уверенность 0..1 |
| `valid_until` | TIMESTAMP WITH TIME ZONE | да |  | До какого момента актуален |
| `created_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Когда создано (UTC) |

Первичный ключ: `id`
Внешние ключи: `account_id` → `accounts.id`
Индексы:
- `ix_insights_account_id_kind_created_at` (account_id, kind, created_at)

## `jobs`

Очередь фоновых задач (claim через FOR UPDATE SKIP LOCKED)

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | Идентификатор (UUIDv7, упорядочен по времени) |
| `account_id` | UUID | нет |  | Аккаунт-владелец (тенант) |
| `kind` | VARCHAR | нет |  | Тип задачи, например x.sync_own_posts |
| `payload` | JSONB | нет | `'{}'::jsonb` | Параметры задачи |
| `run_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Не раньше этого момента (UTC) |
| `status` | VARCHAR | нет | `'queued'` | queued / running / done / dead |
| `attempts` | INTEGER | нет | `0` | Сделано попыток |
| `max_attempts` | INTEGER | нет | `5` | После стольких неудач — dead |
| `idempotency_key` | VARCHAR | да |  | Ключ идемпотентности; повтор не создаёт новую задачу |
| `locked_by` | VARCHAR | да |  | Воркер, взявший задачу |
| `locked_at` | TIMESTAMP WITH TIME ZONE | да |  | Когда взята (UTC) |
| `last_error` | VARCHAR | да |  | Последняя ошибка (без секретов) |
| `created_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Когда создано (UTC) |
| `updated_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Когда изменено (UTC) |
| `finished_at` | TIMESTAMP WITH TIME ZONE | да |  | Когда завершена (UTC) |

Первичный ключ: `id`
Внешние ключи: `account_id` → `accounts.id`
Индексы:
- `ix_jobs_status_run_at` (status, run_at)

## `lessons`

Правила из правок и отказов владельца

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | Идентификатор (UUIDv7, упорядочен по времени) |
| `account_id` | UUID | нет |  | Аккаунт-владелец (тенант) |
| `kind` | VARCHAR | нет |  | do — делать, dont — не делать, pattern — приём |
| `text` | VARCHAR | нет |  | Формулировка урока |
| `status` | VARCHAR | нет | `'proposed'` | proposed / accepted / rejected / retired |
| `evidence` | JSONB | нет | `'[]'::jsonb` | ID ревью, из которых выведен урок |
| `weight` | DOUBLE PRECISION | нет | `1.0` | Вес урока при сборке инструкций |
| `decided_by` | UUID | да |  | Кто принял решение по уроку |
| `decided_at` | TIMESTAMP WITH TIME ZONE | да |  | Когда принято решение (UTC) |
| `created_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Когда создано (UTC) |

Первичный ключ: `id`
Внешние ключи: `account_id` → `accounts.id`, `decided_by` → `users.id`
Индексы:
- `ix_lessons_account_id_status` (account_id, status)

## `llm_calls`

Журнал вызовов моделей; только добавление, партиции по месяцам

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | Идентификатор (UUIDv7) |
| `account_id` | UUID | нет |  | Аккаунт-владелец (тенант) |
| `ts` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Когда вызвано (UTC); ключ партиции |
| `role` | VARCHAR | нет |  | Роль: writer / judge / cheap / embed |
| `provider` | VARCHAR | нет |  | Провайдер: anthropic / deepseek / jev |
| `model` | VARCHAR | нет |  | Модель у провайдера |
| `prompt_id` | UUID | да |  | Версия промпта |
| `purpose` | VARCHAR | нет |  | Зачем вызвали, например draft.generate |
| `input_tokens` | INTEGER | нет | `0` | Токены входа |
| `cached_tokens` | INTEGER | нет | `0` | Токены входа из кеша |
| `output_tokens` | INTEGER | нет | `0` | Токены ответа |
| `cost_micro_usd` | BIGINT | нет | `0` | Цена вызова, микродоллары |
| `latency_ms` | INTEGER | да |  | Длительность, мс |
| `status` | VARCHAR | нет |  | ok / error |
| `error` | VARCHAR | да |  | Класс ошибки (без секретов) |

Первичный ключ: `ts`, `id`
Внешние ключи: `account_id` → `accounts.id`, `prompt_id` → `prompts.id`
Индексы:
- `ix_llm_calls_account_id_role_ts` (account_id, role, ts)
Партиции: `RANGE (ts)` по месяцам + DEFAULT.

## `metric_definitions`

Каталог метрик; формулы грузятся из metrics/definitions/*.sql

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `key` | VARCHAR | нет |  | Ключ метрики |
| `version` | INTEGER | нет |  | Версия формулы |
| `title` | VARCHAR | нет |  | Название для людей |
| `description` | VARCHAR | да |  | Что показывает |
| `unit` | VARCHAR | нет |  | Единица: ratio / count / micro_usd / minutes |
| `grain` | VARCHAR | нет |  | Зерно: day / week |
| `dimensions` | JSONB | нет | `'[]'::jsonb` | Измерения (ключи dims) |
| `formula_sql` | VARCHAR | нет |  | SQL формулы (исполняется READ ONLY) |
| `formula_hash` | VARCHAR | нет |  | Хеш формулы для контроля изменений |
| `active` | BOOLEAN | нет | `true` | Считается ли метрика |
| `created_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Когда создано (UTC) |
| `updated_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Когда изменено (UTC) |

Первичный ключ: `key`

## `metric_values`

Суточные и недельные свёртки метрик

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `account_id` | UUID | нет |  | Аккаунт-владелец (тенант) |
| `metric_key` | VARCHAR | нет |  | Метрика |
| `grain` | VARCHAR | нет |  | day / week |
| `period_start` | DATE | нет |  | Начало периода по поясу аккаунта |
| `dims` | JSONB | нет | `'{}'::jsonb` | Значения измерений |
| `dims_hash` | VARCHAR | нет |  | Хеш dims для ключа |
| `value` | NUMERIC | да |  | Значение метрики |
| `sample_size` | INTEGER | нет |  | Размер выборки |
| `definition_version` | INTEGER | нет |  | Версия формулы, которой посчитано |
| `computed_at` | TIMESTAMP WITH TIME ZONE | нет |  | Когда посчитано (UTC) |

Первичный ключ: `account_id`, `metric_key`, `grain`, `period_start`, `dims_hash`
Внешние ключи: `account_id` → `accounts.id`, `metric_key` → `metric_definitions.key`

## `post_metric_snapshots`

Временной ряд метрик поста X; только добавление, партиции по месяцам

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | Идентификатор (UUIDv7) |
| `account_id` | UUID | нет |  | Аккаунт-владелец (тенант) |
| `x_post_id` | VARCHAR | нет |  | ID поста в X |
| `ts` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Когда снят снимок (UTC); ключ партиции |
| `age_minutes` | INTEGER | нет |  | Возраст поста на момент снимка, минут |
| `impressions` | BIGINT | да |  | Показы |
| `likes` | INTEGER | да |  | Лайки |
| `replies` | INTEGER | да |  | Ответы |
| `reposts` | INTEGER | да |  | Репосты |
| `quotes` | INTEGER | да |  | Цитаты |
| `bookmarks` | INTEGER | да |  | Закладки |
| `url_clicks` | INTEGER | да |  | Клики по ссылке |
| `profile_clicks` | INTEGER | да |  | Клики по профилю |
| `raw` | JSONB | нет | `'{}'::jsonb` | Ответ API как есть |

Первичный ключ: `ts`, `id`
Внешние ключи: `account_id` → `accounts.id`
Индексы:
- `ix_post_metric_snapshots_account_id_post_ts` (account_id, x_post_id, ts)
Партиции: `RANGE (ts)` по месяцам + DEFAULT.

## `prompts`

Версии промптов (глобальные)

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | Идентификатор (UUIDv7, упорядочен по времени) |
| `key` | VARCHAR | нет |  | Ключ промпта, например writer.post |
| `version` | INTEGER | нет |  | Версия, с 1 |
| `role` | VARCHAR | нет |  | Роль модели: writer / judge / cheap и т. п. |
| `template` | VARCHAR | нет |  | Текст шаблона |
| `output_schema` | JSONB | да |  | JSON-схема ожидаемого ответа |
| `content_hash` | VARCHAR | нет |  | Хеш шаблона и схемы |
| `created_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Когда создано (UTC) |

Первичный ключ: `id`

## `publications`

Связь черновика с опубликованным постом X

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | Идентификатор (UUIDv7, упорядочен по времени) |
| `account_id` | UUID | нет |  | Аккаунт-владелец (тенант) |
| `draft_id` | UUID | нет |  | Черновик |
| `x_post_id` | VARCHAR | нет |  | ID поста в X |
| `url` | VARCHAR | нет |  | Ссылка на пост |
| `published_at` | TIMESTAMP WITH TIME ZONE | нет |  | Когда опубликован (UTC) |
| `matched_by` | VARCHAR | нет |  | Как связали: user_link / auto / manual |
| `match_score` | DOUBLE PRECISION | да |  | Похожесть текста при автосверке |
| `final_text` | VARCHAR | нет |  | Текст, который вышел в X |
| `created_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Когда создано (UTC) |

Первичный ключ: `id`
Внешние ключи: `account_id` → `accounts.id`, `draft_id` → `drafts.id`

## `reviews`

Решения владельца по черновикам; пары «до/после»

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | Идентификатор (UUIDv7, упорядочен по времени) |
| `account_id` | UUID | нет |  | Аккаунт-владелец (тенант) |
| `draft_id` | UUID | нет |  | Черновик |
| `user_id` | UUID | нет |  | Кто решил |
| `action` | VARCHAR | нет |  | approve / edit / regenerate / reject |
| `reason_code` | VARCHAR | да |  | Код причины отказа или правки |
| `text_before` | VARCHAR | нет |  | Текст до решения |
| `text_after` | VARCHAR | да |  | Текст после правки |
| `edit_distance` | DOUBLE PRECISION | да |  | Нормированная дистанция до/после, 0..1 |
| `created_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Когда создано (UTC) |

Первичный ключ: `id`
Внешние ключи: `account_id` → `accounts.id`, `draft_id` → `drafts.id`, `user_id` → `users.id`
Индексы:
- `ix_reviews_account_id_draft_id` (account_id, draft_id)

## `schedules`

Расписания периодических задач по поясу аккаунта

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | Идентификатор (UUIDv7, упорядочен по времени) |
| `account_id` | UUID | нет |  | Аккаунт-владелец (тенант) |
| `kind` | VARCHAR | нет |  | Тип задачи, которую ставит расписание |
| `at_local_time` | TIME WITHOUT TIME ZONE | нет |  | Время запуска по поясу аккаунта |
| `weekday` | SMALLINT | да |  | День недели 0=пн..6=вс; NULL — каждый день |
| `enabled` | BOOLEAN | нет | `true` | Включено |
| `payload` | JSONB | нет | `'{}'::jsonb` | Параметры задачи |
| `last_run_at` | TIMESTAMP WITH TIME ZONE | да |  | Последний запуск (UTC) |
| `created_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Когда создано (UTC) |

Первичный ключ: `id`
Внешние ключи: `account_id` → `accounts.id`

## `secrets`

Зашифрованные (Fernet) токены и ключи аккаунта

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | Идентификатор (UUIDv7, упорядочен по времени) |
| `account_id` | UUID | нет |  | Аккаунт-владелец (тенант) |
| `kind` | VARCHAR | нет |  | Что за секрет, например x_oauth |
| `ciphertext` | BYTEA | нет |  | Шифротекст Fernet; открытый текст в БД не хранится |
| `key_version` | INTEGER | нет |  | Версия ключа SECRETS_KEY для ротации |
| `created_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Когда создано (UTC) |
| `updated_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Когда изменено (UTC) |

Первичный ключ: `id`
Внешние ключи: `account_id` → `accounts.id`

## `sources`

Всё, что пришло в систему: посты, образцы, файлы, заметки

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | Идентификатор (UUIDv7, упорядочен по времени) |
| `account_id` | UUID | нет |  | Аккаунт-владелец (тенант) |
| `kind` | VARCHAR | нет |  | Что это: own_post / liked_sample / competitor_post / file / chat / note |
| `origin` | VARCHAR | нет |  | Откуда: manual / x_api / oembed / radar |
| `external_id` | VARCHAR | да |  | ID во внешней системе (пост X) |
| `url` | VARCHAR | да |  | Ссылка на оригинал |
| `author_handle` | VARCHAR | да |  | Автор в X без @ |
| `content_hash` | VARCHAR | нет |  | Хеш нормализованного содержимого; уникален в аккаунте (дедуп ингеста) |
| `status` | VARCHAR | нет | `'new'` | Обработка: new / parsed / indexed / failed / excluded |
| `meta` | JSONB | нет | `'{}'::jsonb` | Прочие поля источника (схема pydantic) |
| `created_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Когда создано (UTC) |

Первичный ключ: `id`
Внешние ключи: `account_id` → `accounts.id`
Индексы:
- `ix_sources_account_id_kind_created_at` (account_id, kind, created_at)

## `style_passports`

Паспорт голоса аккаунта; версии, активна ровно одна

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | Идентификатор (UUIDv7, упорядочен по времени) |
| `account_id` | UUID | нет |  | Аккаунт-владелец (тенант) |
| `version` | INTEGER | нет |  | Номер версии в аккаунте, с 1 |
| `status` | VARCHAR | нет | `'draft'` | draft / active / archived |
| `body` | JSONB | нет | `'{}'::jsonb` | Паспорт: тон, приёмы, лексика (схема pydantic) |
| `hard_rules` | JSONB | нет | `'[]'::jsonb` | Жёсткие правила, проверяемые кодом |
| `source_refs` | JSONB | нет | `'[]'::jsonb` | ID источников, на которых построен паспорт |
| `created_by` | VARCHAR | нет |  | Кто создал: llm / user |
| `created_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Когда создано (UTC) |

Первичный ключ: `id`
Внешние ключи: `account_id` → `accounts.id`
Индексы:
- `uq_style_passports_one_active` (account_id) уникальный

## `topics`

Темы для постов

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | Идентификатор (UUIDv7, упорядочен по времени) |
| `account_id` | UUID | нет |  | Аккаунт-владелец (тенант) |
| `title` | VARCHAR | нет |  | Тема одной строкой |
| `angle` | VARCHAR | да |  | Угол подачи |
| `origin` | VARCHAR | нет |  | Откуда: suggested / manual / radar / plan / trend_tail |
| `status` | VARCHAR | нет | `'new'` | new / planned / in_progress / used / dropped |
| `planned_for` | TIMESTAMP WITH TIME ZONE | да |  | На когда запланирована (UTC) |
| `dedupe_vec` | VECTOR(384) | да |  | Вектор темы для поиска повторов |
| `created_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Когда создано (UTC) |

Первичный ключ: `id`
Внешние ключи: `account_id` → `accounts.id`
Индексы:
- `ix_topics_account_id_status_planned_for` (account_id, status, planned_for)

## `users`

Люди в Telegram, которые работают с ботом

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | Идентификатор (UUIDv7, упорядочен по времени) |
| `tg_user_id` | BIGINT | нет |  | ID пользователя в Telegram |
| `name` | VARCHAR | да |  | Имя для обращения |
| `is_admin` | BOOLEAN | нет | `false` | Администратор всего сервиса |
| `created_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Когда создано (UTC) |

Первичный ключ: `id`

## `x_api_calls`

Журнал вызовов X API; только добавление, партиции по месяцам

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | Идентификатор (UUIDv7) |
| `account_id` | UUID | нет |  | Аккаунт-владелец (тенант) |
| `ts` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Когда вызвано (UTC); ключ партиции |
| `endpoint` | VARCHAR | нет |  | Эндпоинт, например GET /2/users/:id/tweets |
| `resources` | INTEGER | нет | `0` | Сколько ресурсов (постов, профилей) оплачено |
| `cost_micro_usd` | BIGINT | нет | `0` | Цена вызова, микродоллары |
| `utc_day` | DATE | нет |  | День по UTC (для дневного лимита X) |
| `http_status` | INTEGER | да |  | HTTP-код ответа |
| `status` | VARCHAR | нет |  | ok / error |

Первичный ключ: `ts`, `id`
Внешние ключи: `account_id` → `accounts.id`
Индексы:
- `ix_x_api_calls_account_id_utc_day` (account_id, utc_day)
Партиции: `RANGE (ts)` по месяцам + DEFAULT.
