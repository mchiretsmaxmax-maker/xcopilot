# Схема БД

> Генерируется командой `make db-docs` из моделей (`xcopilot.db.docs`). Не редактировать вручную.

Таблиц: 46.

## Оглавление

- `account_members` — Кто к какому аккаунту имеет доступ и с какой ролью
- `account_metric_snapshots` — Метрики аккаунта X по дням
- `accounts` — X-аккаунты (тенанты); глобальные данные принадлежат аккаунту kind=global
- `authors` — Авторы материалов. Нужны для нормировки охвата, независимости доказательств и блокировки источников
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
- `knowledge_applications` — Какие знания были даны автору для черновика и какие реально применены в тексте. Связывает знание с решением владельца и с исходом поста
- `knowledge_embeddings` — Вектор текста знания (statement + guidance) для поиска похожих и подбора под задачу
- `knowledge_evidence` — Доказательства за и против знания. Строки не меняются и не удаляются: ошибочное доказательство отзывается (revoked_at). Из этой таблицы веса пересчитываются на любую дату
- `knowledge_item_scopes` — Где знание применимо. Одно знание — много строк; нет строк по измерению — применимо ко всем его значениям
- `knowledge_item_versions` — Все версии формулировки знания. Правка создаёт новую версию, старая не меняется
- `knowledge_items` — Единица знания: тезис, паттерн хука, структура, анти-паттерн, словарь, пример-эталон. Текущее состояние; история текста — в knowledge_item_versions, история весов — в knowledge_snapshots
- `knowledge_links` — Связи знаний: дубль, уточнение, обобщение, противоречие. Открытое противоречие убирает обе стороны из пакета до решения
- `knowledge_packs` — Пакет знаний, выданный под задачу. Хранит id и порядок знаний, чтобы любой черновик можно было объяснить и воспроизвести
- `knowledge_snapshots` — Состояние каждого знания на конец дня. Партиции по месяцам. Источник метрик «Мозга», сравнения «неделя к неделе» и отката на дату
- `knowledge_transitions` — Каждый переход статуса знания: кто, по какому правилу, с какими числами. Основа отката и недельной сводки
- `llm_calls` — Журнал вызовов моделей; только добавление, партиции по месяцам
- `material_analyses` — Разбор материала по полочкам. Производное: при новой версии промпта или таксономии создаётся новая строка, старая получает is_current = false
- `material_outcomes` — Исход материала по четырём целям. Считается кодом из сигналов, при смене метода пересчитывается; старые версии остаются с is_current = false
- `material_signals` — Наблюдения результата материала: цифры из API, со скриншота, вручную или оценка владельца. Несколько наблюдений на материал, строки не меняются
- `metric_definitions` — Каталог метрик; формулы грузятся из metrics/definitions/*.sql
- `metric_values` — Суточные и недельные свёртки метрик
- `post_metric_snapshots` — Временной ряд метрик поста X; только добавление, партиции по месяцам
- `prompts` — Версии промптов (глобальные)
- `publications` — Связь черновика с опубликованным постом X
- `reviews` — Решения владельца по черновикам; пары «до/после»
- `schedules` — Расписания периодических задач по поясу аккаунта
- `secrets` — Зашифрованные (Fernet) токены и ключи аккаунта
- `segments` — Сегмент — это фильтр по слою, элементу и области действия знаний. Знание попадает в несколько сегментов без дублирования
- `sources` — Всё, что пришло в систему: посты, образцы, файлы, заметки
- `style_passports` — Паспорт голоса аккаунта; версии, активна ровно одна
- `taxonomy_terms` — Словари разбора: форматы, жанры, типы хуков, структуры, приёмы, концовки, тон, цели, ниши. Общие значения принадлежат системному аккаунту global, свои — аккаунту
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

## `authors`

Авторы материалов. Нужны для нормировки охвата, независимости доказательств и блокировки источников

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | UUIDv7 |
| `account_id` | UUID | нет |  | Тенант |
| `handle` | VARCHAR | нет |  | Хэндл в X без @, в нижнем регистре |
| `x_user_id` | VARCHAR | да |  | ID пользователя X, если известен |
| `display_name` | VARCHAR | да |  | Отображаемое имя |
| `is_self` | BOOLEAN | нет | `false` | Это сам владелец аккаунта. Одна такая строка на аккаунт |
| `followers` | INTEGER | да |  | Число подписчиков на момент последнего наблюдения |
| `followers_observed_at` | TIMESTAMP WITH TIME ZONE | да |  | Когда наблюдали число подписчиков |
| `followers_origin` | VARCHAR | да |  | Откуда число: x_api, screenshot, manual |
| `niche` | VARCHAR | да |  | Ниша автора (ключ таксономии niche) |
| `trust` | SMALLINT | нет | `0` | -1 — заблокирован (материалы не учатся), 0 — обычный, 1 — доверенный |
| `note` | VARCHAR | да |  | Заметка владельца |
| `created_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Создано |
| `updated_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Изменено |

Первичный ключ: `id`
Внешние ключи: `account_id` → `accounts.id`
Индексы:
- `uq_authors_self` (account_id) уникальный

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
| `probation_item_id` | UUID | да |  | Знание на испытании, которое проверяется в этом варианте (пометка 🧪). NULL — обычный вариант |

Первичный ключ: `id`
Внешние ключи: `account_id` → `accounts.id`, `parent_draft_id` → `drafts.id`, `probation_item_id` → `knowledge_items.id`, `run_id` → `generation_runs.id`
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
| `dup_threshold` | NUMERIC | нет | `0.88` | Порог косинусного сходства «это одно и то же знание» для этой модели |
| `related_threshold` | NUMERIC | нет | `0.70` | Порог сходства «знания связаны, проверить судьёй» для этой модели |

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
| `knowledge_pack_id` | UUID | да |  | Пакет знаний, с которым писался этот запуск |

Первичный ключ: `id`
Внешние ключи: `account_id` → `accounts.id`, `knowledge_pack_id` → `knowledge_packs.id`, `prompt_id` → `prompts.id`, `topic_id` → `topics.id`
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

## `knowledge_applications`

Какие знания были даны автору для черновика и какие реально применены в тексте. Связывает знание с решением владельца и с исходом поста

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | UUIDv7 |
| `account_id` | UUID | нет |  | Тенант |
| `pack_id` | UUID | нет |  | Пакет, из которого пришло знание |
| `item_id` | UUID | нет |  | Знание |
| `draft_id` | UUID | нет |  | Черновик |
| `slot` | VARCHAR | нет |  | Роль в пакете: core, probation (испытание), anti (чего избегать), pinned |
| `applied` | BOOLEAN | да |  | Применено ли знание в тексте: true, false, NULL — не проверялось |
| `applied_score` | NUMERIC | да |  | Уверенность детектора, 0..1 |
| `checked_by` | VARCHAR | да |  | Чем проверено: feature (код) или rubric (судья) |
| `checked_at` | TIMESTAMP WITH TIME ZONE | да |  | Когда проверено |
| `created_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Создано |

Первичный ключ: `id`
Внешние ключи: `account_id` → `accounts.id`, `account_id` → `knowledge_items.account_id`, `account_id` → `knowledge_packs.account_id`, `draft_id` → `drafts.id`, `item_id` → `knowledge_items.id`, `pack_id` → `knowledge_packs.id`
Индексы:
- `ix_knowledge_applications_item` (account_id, item_id)

## `knowledge_embeddings`

Вектор текста знания (statement + guidance) для поиска похожих и подбора под задачу

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `item_id` | UUID | нет |  | Знание |
| `account_id` | UUID | нет |  | Тенант |
| `model_id` | UUID | нет |  | Модель эмбеддингов |
| `version` | INTEGER | нет |  | Версия текста знания, по которой посчитан вектор |
| `vec` | VECTOR(384) | нет |  | Вектор, 384 измерения |

Первичный ключ: `item_id`, `model_id`
Внешние ключи: `account_id` → `accounts.id`, `account_id` → `knowledge_items.account_id`, `item_id` → `knowledge_items.id`, `model_id` → `embedding_models.id`
Индексы:
- `ix_knowledge_embeddings_account` (account_id, item_id)
- `ix_knowledge_embeddings_hnsw` (vec) hnsw

## `knowledge_evidence`

Доказательства за и против знания. Строки не меняются и не удаляются: ошибочное доказательство отзывается (revoked_at). Из этой таблицы веса пересчитываются на любую дату

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | UUIDv7 |
| `account_id` | UUID | нет |  | Тенант |
| `item_id` | UUID | нет |  | Знание |
| `source_id` | UUID | да |  | Материал-доказательство (свой или чужой пост) |
| `draft_id` | UUID | да |  | Черновик, если доказательство — решение владельца по черновику |
| `dimension` | VARCHAR | нет |  | eff — результативность (исходы постов); fit — совместимость с владельцем (его решения) |
| `basis` | VARCHAR | нет |  | Основание: observed_outcome — исход чужого поста; own_outcome — исход своего; declared — заявлено в статье; owner_pick — владелец отобрал; review — решение по черновику; owner_decision — подтверждение знания |
| `polarity` | SMALLINT | нет |  | +1 подтверждает, −1 опровергает, 0 — знание встретилось, исход нейтрален |
| `detector_score` | NUMERIC | да |  | Насколько уверенно знание распознано в тексте, 0..1 |
| `outcome_tier` | VARCHAR | да |  | Исход материала по цели знания на момент записи |
| `target_metric` | VARCHAR | да |  | Цель, по которой брался исход |
| `format` | VARCHAR | да |  | Формат материала — для разреза весов по форматам |
| `author_id` | UUID | да |  | Автор материала — для счёта независимых авторов |
| `w_source` | NUMERIC | нет |  | Надёжность источника исхода |
| `w_scope` | NUMERIC | нет |  | Близость: свой пост 2.0, своя ниша 1.0, ниша неизвестна 0.8, чужая ниша 0.5 |
| `w_magnitude` | NUMERIC | нет |  | Сила исхода: hit и flop 1.0, above и below 0.5, normal 0 |
| `w_author` | NUMERIC | нет | `1` | Скидка за повтор автора: 1/k для k-го доказательства от того же автора |
| `weight` | NUMERIC | нет | `генерируется: w_source * w_scope * w_magnitude * w_author` | Итоговый вес без учёта свежести (свежесть применяется при пересчёте) |
| `observed_at` | TIMESTAMP WITH TIME ZONE | нет |  | Дата события, от которой считается свежесть: выход поста или решение владельца |
| `created_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Когда доказательство записано |
| `revoked_at` | TIMESTAMP WITH TIME ZONE | да |  | Когда отозвано |
| `revoked_reason` | VARCHAR | да |  | Почему отозвано: source_removed, author_blocked, rollback, reanalysis, owner |

Первичный ключ: `id`
Внешние ключи: `account_id` → `accounts.id`, `account_id` → `authors.account_id`, `account_id` → `knowledge_items.account_id`, `account_id` → `sources.account_id`, `author_id` → `authors.id`, `draft_id` → `drafts.id`, `item_id` → `knowledge_items.id`, `source_id` → `sources.id`
Индексы:
- `ix_evidence_item` (account_id, item_id, dimension)
- `ix_evidence_source` (account_id, source_id)
- `ix_evidence_time` (account_id, created_at)
- `uq_evidence_draft` (item_id, draft_id, basis, dimension) уникальный
- `uq_evidence_source` (item_id, source_id, basis, dimension) уникальный

## `knowledge_item_scopes`

Где знание применимо. Одно знание — много строк; нет строк по измерению — применимо ко всем его значениям

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `item_id` | UUID | нет |  | Знание |
| `account_id` | UUID | нет |  | Тенант |
| `facet` | VARCHAR | нет |  | Измерение: format, goal, niche, lang, archetype |
| `value` | VARCHAR | нет |  | Значение измерения (ключ таксономии) |

Первичный ключ: `item_id`, `facet`, `value`
Внешние ключи: `account_id` → `accounts.id`, `account_id` → `knowledge_items.account_id`, `item_id` → `knowledge_items.id`
Индексы:
- `ix_knowledge_scopes_lookup` (account_id, facet, value, item_id)

## `knowledge_item_versions`

Все версии формулировки знания. Правка создаёт новую версию, старая не меняется

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `item_id` | UUID | нет |  | Знание |
| `account_id` | UUID | нет |  | Тенант |
| `version` | INTEGER | нет |  | Номер версии |
| `statement` | VARCHAR | нет |  | Утверждение в этой версии |
| `guidance` | VARCHAR | нет |  | Указание в этой версии |
| `skeleton` | VARCHAR | да |  | Шаблон в этой версии |
| `detector` | JSONB | нет |  | Детектор в этой версии |
| `kind` | VARCHAR | нет |  | Тип знания в этой версии |
| `layer` | VARCHAR | нет |  | Слой в этой версии |
| `element` | VARCHAR | нет |  | Элемент в этой версии |
| `target_metric` | VARCHAR | нет |  | Цель в этой версии |
| `changed_by` | VARCHAR | нет |  | Кто изменил: system или owner |
| `user_id` | UUID | да |  | Пользователь, если изменил владелец |
| `reason` | VARCHAR | нет |  | Причина: extracted, merged, owner_edit, rollback |
| `provenance` | JSONB | нет | `'{}'::jsonb` | Происхождение: промпт, вызов модели, id материалов-входов |
| `created_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Создано |

Первичный ключ: `item_id`, `version`
Внешние ключи: `account_id` → `accounts.id`, `account_id` → `knowledge_items.account_id`, `item_id` → `knowledge_items.id`, `user_id` → `users.id`
Индексы:
- `ix_knowledge_item_versions_account` (account_id, item_id)

## `knowledge_items`

Единица знания: тезис, паттерн хука, структура, анти-паттерн, словарь, пример-эталон. Текущее состояние; история текста — в knowledge_item_versions, история весов — в knowledge_snapshots

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | UUIDv7 |
| `account_id` | UUID | нет |  | Тенант (global для общих шаблонов) |
| `kind` | VARCHAR | нет |  | Тип знания: thesis, hook_pattern, structure, anti_pattern, lexicon, exemplar |
| `layer` | VARCHAR | нет |  | Слой: voice — только мой голос; humanness — живой текст против AI; craft — переносимое ремесло; niche — конвенции ниши; platform — механика X |
| `element` | VARCHAR | нет |  | К какой части текста относится: hook, structure, device, closer, tone, lexicon, packaging, timing, general |
| `statement` | VARCHAR | нет |  | Проверяемое утверждение для владельца, 15–240 знаков |
| `guidance` | VARCHAR | нет |  | Как применять — короткое указание для модели-автора, 10–200 знаков |
| `skeleton` | VARCHAR | да |  | Шаблон со слотами для structure и exemplar. Чужой дословный текст сюда не попадает |
| `target_metric` | VARCHAR | нет | `'engagement'` | На какую цель работает знание: reach, engagement, replies, saves; quality — про качество текста, без исхода |
| `detector` | JSONB | нет | `'{"type": "none"}'::jsonb` | Как распознать знание в тексте: {type: feature, all: [{path, op, value}]} — по полям разбора; {type: rubric, question} — вопрос судье; {type: none} |
| `risk_class` | CHAR(1) | нет |  | A — меняет голос, только с подтверждением; B — ремесло, испытание в одном варианте; C — мягкий запрет, предупреждение линтера |
| `status` | VARCHAR | нет | `'candidate'` | Жизненный цикл: candidate, probation, active, weakened, conflicted, archived, rejected, merged |
| `owner_state` | VARCHAR | нет | `'none'` | Решение владельца: none, confirmed, pinned (всегда в пакете), disabled (не применять) |
| `origin` | VARCHAR | нет |  | Откуда появилось: materials, edits (правки черновиков), own_metrics, owner (ввёл сам), seed |
| `parent_item_id` | UUID | да |  | Общий шаблон из аккаунта global, от которого создана копия |
| `merged_into` | UUID | да |  | В какое знание влито (для status = merged) |
| `version` | INTEGER | нет | `1` | Номер действующей версии текста |
| `eff_alpha` | NUMERIC | нет | `1` | Результативность: априор 1 + сумма весов подтверждений |
| `eff_beta` | NUMERIC | нет | `1` | Результативность: априор 1 + сумма весов опровержений |
| `fit_alpha` | NUMERIC | нет | `2` | Совместимость с владельцем: априор 2 + сумма весов принятий |
| `fit_beta` | NUMERIC | нет | `1` | Совместимость: априор 1 + сумма весов отказов |
| `eff_conf` | NUMERIC | нет | `0.2113` | Уверенность в результативности: среднее минус одно стандартное отклонение, 0..1 |
| `fit_conf` | NUMERIC | нет | `0.4310` | Уверенность в совместимости, 0..1 |
| `quality` | NUMERIC | нет | `0.2992` | Итоговое качество для ранжирования: 0.6·eff_conf + 0.4·fit_conf (для target_metric = quality — только fit_conf) |
| `evidence_n` | INTEGER | нет | `0` | Число доказательств результативности с исходом |
| `independent_authors` | INTEGER | нет | `0` | Число разных авторов среди подтверждений |
| `own_support` | INTEGER | нет | `0` | Подтверждений на своих постах |
| `own_contra` | INTEGER | нет | `0` | Опровержений на своих постах |
| `occurrences` | INTEGER | нет | `0` | Сколько раз знание встретилось в материалах вообще |
| `last_evidence_at` | TIMESTAMP WITH TIME ZONE | да |  | Дата самого свежего доказательства |
| `scores_updated_at` | TIMESTAMP WITH TIME ZONE | да |  | Когда пересчитаны веса |
| `probation_started_at` | TIMESTAMP WITH TIME ZONE | да |  | Когда знание взято на испытание |
| `created_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Создано |
| `updated_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Изменено |

Первичный ключ: `id`
Внешние ключи: `account_id` → `accounts.id`, `account_id` → `knowledge_items.account_id`, `merged_into` → `knowledge_items.id`
Индексы:
- `ix_knowledge_items_pack` (account_id, layer, element)
- `ix_knowledge_items_parent` (parent_item_id)
- `ix_knowledge_items_status` (account_id, status)
- `ix_knowledge_items_trgm` (statement) gin

## `knowledge_links`

Связи знаний: дубль, уточнение, обобщение, противоречие. Открытое противоречие убирает обе стороны из пакета до решения

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | UUIDv7 |
| `account_id` | UUID | нет |  | Тенант |
| `from_item_id` | UUID | нет |  | Знание-источник связи |
| `to_item_id` | UUID | нет |  | Знание-цель связи |
| `relation` | VARCHAR | нет |  | Тип связи: duplicates, refines, generalizes, contradicts, related |
| `confidence` | NUMERIC | нет |  | Уверенность судьи в связи |
| `decided_by` | VARCHAR | нет |  | Кто установил связь: judge, owner, system |
| `status` | VARCHAR | нет | `'open'` | open — требует решения; resolved — решено |
| `resolution` | VARCHAR | да |  | Как решено: kept_from, kept_to, split_scope, both |
| `created_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Создано |
| `resolved_at` | TIMESTAMP WITH TIME ZONE | да |  | Когда решено |

Первичный ключ: `id`
Внешние ключи: `account_id` → `accounts.id`, `account_id` → `knowledge_items.account_id`, `account_id` → `knowledge_items.account_id`, `from_item_id` → `knowledge_items.id`, `to_item_id` → `knowledge_items.id`
Индексы:
- `ix_knowledge_links_open` (account_id, relation)
- `ix_knowledge_links_to` (account_id, to_item_id)

## `knowledge_packs`

Пакет знаний, выданный под задачу. Хранит id и порядок знаний, чтобы любой черновик можно было объяснить и воспроизвести

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | UUIDv7 |
| `account_id` | UUID | нет |  | Тенант |
| `consumer` | VARCHAR | нет |  | Кому выдан: writer, export, mcp, eval |
| `task` | JSONB | нет |  | Задача (TaskSpec): формат, цель, ниша, язык, жанр, тема |
| `schema_version` | INTEGER | нет |  | Версия схемы KnowledgePack |
| `taxonomy_version` | INTEGER | нет |  | Версия таксономии на момент сборки |
| `items` | JSONB | нет |  | Состав: [{item_id, version, slot, rank, score}] |
| `token_count` | INTEGER | нет |  | Оценка размера пакета в токенах |
| `created_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Создано |

Первичный ключ: `id`
Внешние ключи: `account_id` → `accounts.id`
Индексы:
- `ix_knowledge_packs_time` (account_id)

## `knowledge_snapshots`

Состояние каждого знания на конец дня. Партиции по месяцам. Источник метрик «Мозга», сравнения «неделя к неделе» и отката на дату

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `snapshot_date` | DATE | нет |  | Дата снимка (по поясу аккаунта) |
| `account_id` | UUID | нет |  | Тенант |
| `item_id` | UUID | нет |  | Знание |
| `status` | VARCHAR | нет |  | Статус на дату |
| `owner_state` | VARCHAR | нет |  | Решение владельца на дату |
| `version` | INTEGER | нет |  | Версия текста на дату |
| `layer` | VARCHAR | нет |  | Слой знания |
| `eff_alpha` | NUMERIC | нет |  | eff_alpha на дату |
| `eff_beta` | NUMERIC | нет |  | eff_beta на дату |
| `fit_alpha` | NUMERIC | нет |  | fit_alpha на дату |
| `fit_beta` | NUMERIC | нет |  | fit_beta на дату |
| `eff_conf` | NUMERIC | нет |  | eff_conf на дату |
| `fit_conf` | NUMERIC | нет |  | fit_conf на дату |
| `quality` | NUMERIC | нет |  | quality на дату |
| `evidence_n` | INTEGER | нет |  | Число доказательств на дату |
| `own_support` | INTEGER | нет |  | Подтверждений на своих постах на дату |
| `own_contra` | INTEGER | нет |  | Опровержений на своих постах на дату |

Первичный ключ: `snapshot_date`, `account_id`, `item_id`
Внешние ключи: `account_id` → `accounts.id`
Индексы:
- `ix_knowledge_snapshots_item` (account_id, item_id)
Партиции: `RANGE (snapshot_date)` по месяцам + DEFAULT.

## `knowledge_transitions`

Каждый переход статуса знания: кто, по какому правилу, с какими числами. Основа отката и недельной сводки

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | UUIDv7 |
| `account_id` | UUID | нет |  | Тенант |
| `item_id` | UUID | нет |  | Знание |
| `ts` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Когда |
| `from_status` | VARCHAR | да |  | Прежний статус (NULL при создании) |
| `to_status` | VARCHAR | нет |  | Новый статус |
| `actor` | VARCHAR | нет |  | system — автоматически; owner — решение владельца |
| `user_id` | UUID | да |  | Пользователь, если решил владелец |
| `rule` | VARCHAR | нет |  | Правило перехода: auto_probation, promote_by_reviews, promote_by_own_evidence, weaken_low_quality, archive_stale, conflict_opened, owner_confirm, owner_reject, rollback |
| `reason` | VARCHAR | да |  | Пояснение для владельца |
| `payload` | JSONB | нет | `'{}'::jsonb` | Числа на момент перехода: веса, счётчики, пороги |
| `reverted_at` | TIMESTAMP WITH TIME ZONE | да |  | Когда переход откатили |
| `reverted_by` | UUID | да |  | Кто откатил |

Первичный ключ: `id`
Внешние ключи: `account_id` → `accounts.id`, `account_id` → `knowledge_items.account_id`, `item_id` → `knowledge_items.id`, `reverted_by` → `users.id`, `user_id` → `users.id`
Индексы:
- `ix_knowledge_transitions_auto` (account_id)
- `ix_knowledge_transitions_item` (account_id, item_id)

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
| `entity_type` | VARCHAR | да |  | Тип сущности, ради которой вызвали, например draft |
| `entity_id` | UUID | да |  | ID сущности |

Первичный ключ: `ts`, `id`
Внешние ключи: `account_id` → `accounts.id`, `prompt_id` → `prompts.id`
Индексы:
- `ix_llm_calls_account_id_entity` (account_id, entity_type, entity_id)
- `ix_llm_calls_account_id_role_ts` (account_id, role, ts)
Партиции: `RANGE (ts)` по месяцам + DEFAULT.

## `material_analyses`

Разбор материала по полочкам. Производное: при новой версии промпта или таксономии создаётся новая строка, старая получает is_current = false

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | UUIDv7 |
| `account_id` | UUID | нет |  | Тенант |
| `source_id` | UUID | нет |  | Материал |
| `analyzer_version` | VARCHAR | нет |  | Ключ и версия промпта разбора |
| `taxonomy_version` | INTEGER | нет |  | Версия таксономии, в которой сделан разбор |
| `model` | VARCHAR | нет |  | Модель, сделавшая разбор |
| `llm_call_id` | UUID | да |  | Вызов в журнале llm_calls |
| `is_current` | BOOLEAN | нет | `true` | Действующий разбор материала |
| `format` | VARCHAR | нет |  | Формат: post, thread, reply, quote, article |
| `archetype` | VARCHAR | да |  | Жанр (таксономия archetype) |
| `lang` | VARCHAR | да |  | Язык текста |
| `hook_type` | VARCHAR | да |  | Тип хука (таксономия hook_type) |
| `hook_text` | VARCHAR | да |  | Первая строка или фраза-хук — дословная цитата из материала, проверяется кодом |
| `structure` | VARCHAR | да |  | Структура (таксономия structure) |
| `structure_outline` | JSONB | нет | `'[]'::jsonb` | Шаги структуры: [{step, role, quote}] |
| `tone` | TEXT[] | нет | `'{}'::text[]` | Тон (таксономия tone), до трёх значений |
| `devices` | TEXT[] | нет | `'{}'::text[]` | Приёмы удержания (таксономия device) |
| `closer` | VARCHAR | да |  | Тип концовки (таксономия closer) |
| `human_markers` | TEXT[] | нет | `'{}'::text[]` | Что делает текст живым: дословные фрагменты |
| `ai_markers` | TEXT[] | нет | `'{}'::text[]` | Что звучит как AI: дословные фрагменты |
| `features` | JSONB | нет | `'{}'::jsonb` | Признаки, посчитанные кодом: длина, число строк, цифра в первой строке, вопрос, эмодзи, ссылка, медиа, первое лицо и т. д. |
| `why_hypotheses` | JSONB | нет | `'[]'::jsonb` | Гипотезы «почему зашло или не зашло»: [{claim, quote}]. Не доказательство |
| `declared_claims` | JSONB | нет | `'[]'::jsonb` | Утверждения о ремесле, которые материал заявляет сам (для статей и разборов) |
| `proposed_terms` | JSONB | нет | `'[]'::jsonb` | Предложенные новые значения таксономии |
| `quality_flags` | TEXT[] | нет | `'{}'::text[]` | Флаги качества: truncated, low_content, non_text, parent_missing |
| `created_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Создано |

Первичный ключ: `id`
Внешние ключи: `account_id` → `accounts.id`, `account_id` → `sources.account_id`, `source_id` → `sources.id`
Индексы:
- `ix_material_analyses_devices` (devices) gin
- `ix_material_analyses_facets` (account_id, format, hook_type, structure)
- `ix_material_analyses_features` (features) gin
- `uq_material_analyses_current` (source_id) уникальный

## `material_outcomes`

Исход материала по четырём целям. Считается кодом из сигналов, при смене метода пересчитывается; старые версии остаются с is_current = false

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | UUIDv7 |
| `account_id` | UUID | нет |  | Тенант |
| `source_id` | UUID | нет |  | Материал |
| `method_version` | INTEGER | нет |  | Версия метода расчёта (пороги, формулы) |
| `basis` | VARCHAR | нет |  | На чём основан исход: own_baseline — перцентиль среди своих постов; rates — доли от просмотров; owner_label — кнопка; owner_pick — только факт отбора; none — данных нет |
| `signal_origin` | VARCHAR | нет |  | Источник использованного сигнала (см. material_signals.origin) |
| `reliability` | NUMERIC(3, 2) | нет |  | Надёжность исхода 0..1. Становится множителем w_source в доказательствах |
| `engagement_rate` | NUMERIC | да |  | (лайки + репосты + цитаты) / просмотры |
| `reply_rate` | NUMERIC | да |  | ответы / просмотры |
| `save_rate` | NUMERIC | да |  | закладки / просмотры |
| `reach_ratio` | NUMERIC | да |  | просмотры / подписчики автора |
| `pct_reach` | NUMERIC | да |  | Перцентиль охвата среди своих постов того же формата за 90 дней (только для своих) |
| `pct_engagement` | NUMERIC | да |  | Перцентиль вовлечённости среди своих постов (только для своих) |
| `pct_replies` | NUMERIC | да |  | Перцентиль доли ответов среди своих постов (только для своих) |
| `pct_saves` | NUMERIC | да |  | Перцентиль доли закладок среди своих постов (только для своих) |
| `tier_reach` | VARCHAR | нет | `'unknown'` | Исход по цели «охват»: hit, above, normal, below, flop, unknown |
| `tier_engagement` | VARCHAR | нет | `'unknown'` | Исход по цели «вовлечённость» |
| `tier_replies` | VARCHAR | нет | `'unknown'` | Исход по цели «ответы» |
| `tier_saves` | VARCHAR | нет | `'unknown'` | Исход по цели «сохранения» |
| `is_current` | BOOLEAN | нет | `true` | Действующая версия исхода |
| `computed_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Когда посчитано |

Первичный ключ: `id`
Внешние ключи: `account_id` → `accounts.id`, `account_id` → `sources.account_id`, `source_id` → `sources.id`
Индексы:
- `ix_material_outcomes_account` (account_id)
- `uq_material_outcomes_current` (source_id) уникальный

## `material_signals`

Наблюдения результата материала: цифры из API, со скриншота, вручную или оценка владельца. Несколько наблюдений на материал, строки не меняются

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | UUIDv7 |
| `account_id` | UUID | нет |  | Тенант |
| `source_id` | UUID | нет |  | Материал |
| `observed_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Когда сняты цифры |
| `origin` | VARCHAR | нет |  | Источник: x_api, screenshot, manual_numbers, owner_label (кнопка), owner_pick (сам факт отбора), own_sync (свой пост из этапа 14) |
| `post_age_hours` | NUMERIC | да |  | Возраст поста на момент наблюдения. Моложе 24 часов — исход не считается |
| `views` | BIGINT | да |  | Просмотры |
| `likes` | INTEGER | да |  | Лайки |
| `replies` | INTEGER | да |  | Ответы |
| `reposts` | INTEGER | да |  | Репосты |
| `quotes` | INTEGER | да |  | Цитаты |
| `bookmarks` | INTEGER | да |  | Закладки |
| `author_followers` | INTEGER | да |  | Подписчики автора на момент наблюдения |
| `owner_label` | VARCHAR | да |  | Оценка владельца: hit, normal, flop |
| `read_confidence` | NUMERIC(3, 2) | нет | `1` | Уверенность чтения цифр (для скриншота — от модели, после подтверждения владельцем = 1) |
| `confirmed_by` | UUID | да |  | Кто подтвердил цифры |
| `raw` | JSONB | нет | `'{}'::jsonb` | Сырой ответ источника |

Первичный ключ: `id`
Внешние ключи: `account_id` → `accounts.id`, `account_id` → `sources.account_id`, `confirmed_by` → `users.id`, `source_id` → `sources.id`
Индексы:
- `ix_material_signals_latest` (account_id, source_id)

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

## `segments`

Сегмент — это фильтр по слою, элементу и области действия знаний. Знание попадает в несколько сегментов без дублирования

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | UUIDv7 |
| `account_id` | UUID | нет |  | Тенант |
| `key` | VARCHAR | нет |  | Ключ сегмента |
| `title` | VARCHAR | нет |  | Название в боте |
| `filter` | JSONB | нет |  | Фильтр: {layer: [...], element: [...], kind: [...], format: [...], goal: [...], niche: [...], lang: [...]}. Пустой ключ — без ограничения |
| `position` | INTEGER | нет | `0` | Порядок в меню |
| `is_default` | BOOLEAN | нет | `false` | Создан системой при онбординге |
| `created_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Создано |

Первичный ключ: `id`
Внешние ключи: `account_id` → `accounts.id`

## `sources`

Всё, что пришло в систему: посты, образцы, файлы, заметки

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | Идентификатор (UUIDv7, упорядочен по времени) |
| `account_id` | UUID | нет |  | Аккаунт-владелец (тенант) |
| `kind` | VARCHAR | нет |  | Что это физически: post, thread, reply, quote, repost, article, note, file, chat |
| `origin` | VARCHAR | нет |  | Канал получения: manual_text, link_oembed, x_api, screenshot, tg_forward, file, url_fetch, history_backfill, radar |
| `external_id` | VARCHAR | да |  | ID во внешней системе (пост X) |
| `url` | VARCHAR | да |  | Ссылка на оригинал |
| `author_handle` | VARCHAR | да |  | Автор в X без @ |
| `content_hash` | VARCHAR | нет |  | Хеш нормализованного содержимого; уникален в аккаунте (дедуп ингеста) |
| `status` | VARCHAR | нет | `'new'` | Обработка: new / parsed / indexed / failed / excluded |
| `meta` | JSONB | нет | `'{}'::jsonb` | Прочие поля источника (схема pydantic) |
| `created_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Когда создано (UTC) |
| `stance` | VARCHAR | нет |  | Отношение владельца: own — моё, inspiration — нравится, competitor — конкурент, anti_example — так не надо, reference — материал и факты |
| `author_id` | UUID | да |  | Автор материала |
| `parent_source_id` | UUID | да |  | Пост, на который это ответ или цитата. Нужен как контекст разбора |
| `thread_root_id` | UUID | да |  | Первый пост треда, если материал — часть треда |
| `posted_at` | TIMESTAMP WITH TIME ZONE | да |  | Когда пост вышел в X (не когда попал в базу). От него считается свежесть доказательств |
| `lang` | VARCHAR | да |  | Язык материала (ISO 639-1) |
| `owner_note` | VARCHAR | да |  | Что владелец написал про материал при отправке |
| `analysis_status` | VARCHAR | нет | `'pending'` | Состояние разбора: pending, analyzed, skipped, failed, quarantined |
| `quarantine_reason` | VARCHAR | да |  | Почему материал в карантине (инъекция, не контент, автор заблокирован) |
| `removed_at` | TIMESTAMP WITH TIME ZONE | да |  | Владелец убрал материал. Его доказательства отзываются, веса пересчитываются |

Первичный ключ: `id`
Внешние ключи: `account_id` → `accounts.id`, `account_id` → `authors.account_id`, `account_id` → `sources.account_id`, `account_id` → `sources.account_id`, `author_id` → `authors.id`, `parent_source_id` → `sources.id`, `thread_root_id` → `sources.id`
Индексы:
- `ix_sources_account_id_kind_created_at` (account_id, kind, created_at)
- `ix_sources_author` (account_id, author_id)
- `ix_sources_learning` (account_id, stance, kind, analysis_status)
- `ix_sources_parent` (account_id, parent_source_id)

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

## `taxonomy_terms`

Словари разбора: форматы, жанры, типы хуков, структуры, приёмы, концовки, тон, цели, ниши. Общие значения принадлежат системному аккаунту global, свои — аккаунту

| Колонка | Тип | NULL | По умолчанию | Описание |
|---|---|---|---|---|
| `id` | UUID | нет | `uuidv7()` | UUIDv7 |
| `account_id` | UUID | нет |  | Тенант (global для общих значений) |
| `facet` | VARCHAR | нет |  | Какой словарь |
| `key` | VARCHAR | нет |  | Ключ значения, латиницей |
| `title` | VARCHAR | нет |  | Название для владельца |
| `description` | VARCHAR | нет |  | Определение, по которому модель и человек отличают значение от соседних |
| `status` | VARCHAR | нет | `'active'` | active — используется; proposed — предложено моделью, ждёт решения; deprecated — заменено |
| `replaced_by` | VARCHAR | да |  | Ключ значения-преемника для deprecated |
| `since_version` | INTEGER | нет | `1` | С какой версии таксономии значение действует |
| `created_by` | VARCHAR | нет | `'seed'` | Кто добавил: seed, model, owner |
| `created_at` | TIMESTAMP WITH TIME ZONE | нет | `now()` | Создано |

Первичный ключ: `id`
Внешние ключи: `account_id` → `accounts.id`
Индексы:
- `ix_taxonomy_active` (account_id, facet)

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
