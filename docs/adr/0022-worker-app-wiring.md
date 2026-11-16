# ADR-0022: Воркер, планировщик и сборка процесса

Статус: принято (ветка `fix/worker-app-wiring`, 15.11.2026). Опирается на P13 (очередь в
PostgreSQL) и P25 (один процесс: бот + планировщик + воркер).

## Контекст
Этапы 2–10 оставили обработчики задач (`ingest.process`, `brain.*`, `writer.*`,
`x.recheck_metrics`, `metrics.rollup`, `db.ensure_partitions`, `bot.notify_budget`) и хуки
(`install_analysis_on_ingest`, `PromptRegistry.sync`, сид анти-AI правил), но в проекте не было
ни воркера, ни планировщика, ни сборки живого процесса: `main.serve` только ждал сигнала.

## Решения
1. **Воркер (`jobs/worker.py`) берёт задачи по одной.** `claim(limit=1)` под `SKIP LOCKED`:
   при остановке не остаётся взятых, но не начатых задач. `stop` проверяется между задачами —
   текущая доделывается. Пустая очередь — пауза `poll_interval` (по умолчанию 1 с, просыпается
   сразу по `stop`). Раз в `stale_check_every` итераций — `release_stale(30 мин)`.
2. **Итог задачи.** Успех → `complete`. Исключение из `HandlerSpec.permanent` или `NotFound`
   (общее для всех) → сразу `dead`: повтор бесполезен. Любое другое → `fail` с паузой
   `base_delay·2^(n−1)` (≤ 1 ч), после `max_attempts` — `dead`. Неизвестный `kind` → `dead`
   с ошибкой «неизвестный вид задачи: …». В `last_error` — тип и начало сообщения (≤ 500 знаков).
3. **`JobQueue.fail(..., retry=True) -> статус`.** Флаг `retry=False` — сразу `dead`; при смерти
   задачи в той же транзакции пишется событие `job.dead` (тип был в каталоге, но не писался).
   Схема не менялась.
4. **Писатель: `PERMANENT` → `dead`, а не `complete`** (как в `process_writer_job` этапа 10):
   ошибка остаётся видна в `jobs`. `WriterUnavailableError` — обычный повтор; владелец
   уведомлён обработчиком на первой попытке. `process_writer_job` оставлен для тестов этапа 10.
5. **Контекст аккаунта на задачу** — `structlog.contextvars` (`account_id`, `job_id`, `job_kind`)
   на время обработчика. RLS-контекст БД (`app.account_id`) добавит этап 17 в этом же месте.
6. **Реестр (`jobs/handlers.py`): `KNOWN_KINDS` + `build_registry(JobDeps)`** — все виды задач
   одной таблицей. Тест сканирует исходники (`enqueue("…")`, `kind="…"`, `*_KIND = "…"`) и сиды
   `schedules` в миграциях: каждый найденный вид обязан быть в реестре. Постоянные ошибки:
   писатель — `writer.PERMANENT`; `x.recheck_metrics` и `brain.history_import` — `XModeError`,
   `XAuthError` (и `XNotFound` через общий `NotFound`).
7. **Планировщик (`jobs/scheduler.py`) — тик раз в минуту.** Наступление считает `due_at` по
   поясу аккаунта (`accounts.tz`); пропущенный запуск догоняется в пределах тех же местных суток;
   пропущенный день и пропущенная недельная сводка не догоняются (AUD-015). Постановка — с
   ключом `sched:<kind>:<локальная дата>` и `last_run_at` в одной транзакции: дубля нет даже
   при потерянной отметке. Стандартные расписания каждого рабочего аккаунта
   (`DEFAULT_SCHEDULES`: `metrics.rollup` 00:30, `brain.nightly` 03:30) заводятся кодом
   (`ON CONFLICT DO NOTHING`), без миграции; у системного аккаунта — только засеянный
   `db.ensure_partitions` 03:00. SQL — в новом `db/repos/schedules.py`.
8. **Сборка (`xcopilot/app.py`).** `build_app(settings)` собирает всё без сети и БД;
   `App.startup()` — подписчики (`install_analysis_on_ingest`, `install_budget_notifications`),
   `PromptRegistry().sync`, `ensure_all_partitions`, `seed.load_global_seed`. X: `x_for(account)`
   строит `XService` в режиме аккаунта, API — только при `SECRETS_KEY` и сохранённых токенах,
   `jobs=queue` (перепроверка метрик), один `DailyDedup` и один `httpx.AsyncClient` на процесс.
   Писатель получает `BrainProbationPicker` (9A); знания — пока `NoKnowledge` (9B подключит
   `BrainKnowledge` здесь же).
9. **`serve_app`** — бот (long polling), воркер и планировщик в `TaskGroup`; падение одного
   ставит `stop`, остальные завершаются штатно, затем закрываются HTTP-клиент, сессия бота и
   движок. `main.serve(stop, settings)` = `serve_app(build_app(settings), stop)`; настройки
   передаются из `run()` через модульную переменную (сигнатура `_main()` без аргументов
   сохранена ради тестов каркаса). Нет `BOT_TOKEN` — прежняя ошибка конфигурации, код 2.

## Последствия
- Новый вид задачи = константа `*_KIND` + строка в `build_registry` + `KNOWN_KINDS`; иначе
  падает тест реестра.
- `brain.detect` и `brain.extract` зарегистрированы, но их пока никто не ставит (решит 9B/9C).
- Ингест и воркер делят папку `DATA_DIR/ingest` — в одном процессе это выполняется само.
