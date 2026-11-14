# ADR-0015: «Мозг» в БД — ретрофит схемы под ТЗ v3.1

Статус: принято (этап 02r, ветка `stage/02r-brain-db`, 14.11.2026).
Источник: `docs/brain-v3.1.md`, разделы 2.1–2.4, дельты этапов 2 и 3, приложения A, B, C.

## Что сделано
1. **Миграция `0004_brain_db`.** Всё DDL приложения A и B сразу, а не по этапам 7A/9A/9B:
   `authors`; `sources` + `stance`, `author_id`, `parent_source_id`, `thread_root_id`, `posted_at`,
   `lang`, `owner_note`, `analysis_status`, `quarantine_reason`, `removed_at`; 15 новых таблиц
   (`material_signals`, `material_outcomes`, `taxonomy_terms`, `material_analyses`,
   `knowledge_items`, `knowledge_item_versions`, `knowledge_item_scopes`, `knowledge_evidence`,
   `knowledge_links`, `knowledge_transitions`, `knowledge_embeddings`, `knowledge_snapshots`,
   `knowledge_packs`, `knowledge_applications`, `segments`) — с `authors` их 16;
   `generation_runs.knowledge_pack_id`, `drafts.probation_item_id`,
   `embedding_models.dup_threshold/related_threshold`; триггер `voice_evidence_own_only`;
   функции `knowledge_recompute_scores`, `knowledge_state_on`; представления
   `v_knowledge_usable`, `v_knowledge_cards`, `v_segment_items`, `v_knowledge_evidence_summary`,
   `v_material_best_signal`. `COMMENT ON` — на каждой таблице, колонке, представлении, функции.
   `lessons` удалена; downgrade возвращает её (пустой) и старые `kind`/`origin`.
2. **Перенос `sources.kind` (данные).**

   | было `kind` | стало `kind` | `stance` |
   |---|---|---|
   | `own_post` | `post` | `own` |
   | `liked_sample` | `post` | `inspiration` |
   | `competitor_post` | `post` | `competitor` |
   | `note` | `note` | `reference` |
   | `file` | `file` | `reference` |
   | `chat` | `chat` | `reference` |

   `origin`: `manual` → `manual_text`, `oembed` → `link_oembed`, `x_api`/`radar` без изменений.
   Обратно (downgrade, с потерями): `note/file/chat` как были; `article` → `note`; остальные виды
   по stance: `own` → `own_post`, `competitor` → `competitor_post`, `inspiration`/`anti_example` →
   `liked_sample`, `reference` → `note`. Новые каналы → `manual`, `history_backfill` → `x_api`.
3. **Код.** В боте и теггере остаётся словарь «материала» (`own_post`, `liked_sample`,
   `competitor_post`, `note`: кнопки, callback-данные, промпт `ingest.tag`, `props.kind`
   события `source.ingested`). На границе БД — `db/repos/sources.py`:
   `material_kind_stance()` и обратная `source_material()`. Файлы ингеста получают
   `origin = file`. Ингест и онбординг заполняют `author_id` через `AuthorsRepo.get_or_create`
   (свой хэндл — `set_self`); строка не похожая на хэндл X — без автора.
4. **Предикат «свой голос»** — одна функция `own_voice()` в `db/repos/sources.py`
   (`stance = 'own'`), константа `OWN_STANCE`. Этапы 8 и 9 зовут её, а не сравнивают сами.
5. **Репозитории.** `AuthorsRepo` (`get_or_create`, `by_handle`, `set_self`, `self_author`,
   `block` — с отзывом доказательств автора), `SourcesRepo` (`set_parent`, `quarantine`,
   `mark_removed` — отзыв доказательств и пересчёт весов, `count_documents(stance=…)`),
   `KnowledgeItemsRepo` (создание с версией 1 и областью, `edit` новой версией, `transition` /
   `revert_transition` с журналом, `recompute`, `usable`), `KnowledgeEvidenceRepo`,
   `KnowledgeLinksRepo`, `KnowledgeSnapshotsRepo` (`take`, `state_on`), `KnowledgeEmbeddingsRepo`,
   `KnowledgePacksRepo`, `KnowledgeApplicationsRepo`, `SegmentsRepo`, `TaxonomyRepo`,
   `MaterialAnalysesRepo`/`MaterialOutcomesRepo` (`add_current`), `MaterialSignalsRepo` (`best`).
   `LessonsRepo` и модель `Lesson` удалены.
6. **События и метрики.** 13 типов дельты этапа 3 — в конце `EventType`, схемы props в
   `PROPS_SCHEMA`. Метрика `lessons_accepted_week` удалена, добавлены 12 метрик приложения C
   (все опираются только на БД). Каталог: 28 метрик.

## Отступления от приложения A и почему
- **Имена ограничений — по конвенции проекта** (`ck_<таблица>_<имя>`, `uq_<таблица>_<колонки>`):
  `ck_sources_kind_allowed` вместо `sources_kind_check`, `uq_sources_account_id_id` вместо
  `uq_sources_account_id`. Составные внешние ключи названы явно (`fk_sources_parent`,
  `fk_<таблица>_<колонка>`), иначе по конвенции у двух FK `sources` совпали бы имена.
- **Строки — `varchar`, а не `text`**, как во всей схеме (модели `str` → `VARCHAR`); для PG
  разницы нет. Массивы — `text[]`, как `chunks.tags`.
- **Индексы по `account_id`** добавлены в `knowledge_item_versions`
  (`ix_knowledge_item_versions_account`) и `knowledge_embeddings`
  (`ix_knowledge_embeddings_account`): правило 0.4 (тест `test_every_business_table_has_account_id`).
- **`knowledge_snapshots`** — в `PARTITIONED_TABLES` (`db/partitions.py`), ключ `snapshot_date`
  (`PARTITION_KEYS`); миграция создаёт `knowledge_snapshots_default`, месячные — `ensure_partitions`.
- **`sources.author_handle` оставлен** (приложение A его не удаляет; ингест этапа 7 читает его).
- **Метрики приложения C:** `unit: usd_micro` → `micro_usd` (единица каталога), добавлена
  единица `distance` (`brain_voice_drift`); шапка `dimensions:` → `dims:` (формат каталога).
- **SQL миграции** разбит на отдельные команды (asyncpg не принимает несколько команд в одном
  запросе); тела функций `$$…$$` не режутся.
- **События `lesson.*`** оставлены в `EventType` (новые типы — только добавлением в конец);
  писать их больше некому.

## Сиды (миграция `0005_brain_seeds`, решение владельца 14.11: всё про БД — сейчас)
- `taxonomy_terms` аккаунта `global`: 70 стартовых значений раздела 2.1 — `hook_type` (14),
  `structure` (11), `device` (15), `archetype` (9), `closer` (7), `tone` (9) и измерения
  `format` (5), `goal` (4). `status = active`, `created_by = seed`, `since_version = 1`.
- Берётся только то, что есть в ТЗ. Названия — где документ их даёт (7 жанров, цели, форматы);
  определения — только у форматов (из таблицы `kind`). У остальных `title = key`,
  `description = ''`: определений в v3.1 нет, их пишет этап 7A (промпт разбора опирается на них).
  Словарь `niche` — без стартовых значений (в документе их нет).
- `segments` аккаунта `global` (`is_default = true`) — 9 сегментов с готовым фильтром из таблицы
  «Сегменты из ТЗ и добавленные»: Голос, Человечность, Посты, Треды, Ответы, Цитаты и репосты
  («слой не voice» = humanness/craft/niche/platform), Хуки, Упаковка и время, Механика X.
  Сегменты с параметром («по цели», «ниша», «язык», «жанр») и копирование шаблонов в аккаунт
  при онбординге (`brain/segments.py ensure_defaults`) — этап 9C.
- Повторный upgrade не дублирует (`ON CONFLICT DO NOTHING`), downgrade удаляет только сиды.

## Не сделано здесь
- Задача ежедневного снимка (`KnowledgeSnapshotsRepo.take`) в расписании — этап 9A.
