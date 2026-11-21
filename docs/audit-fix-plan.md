# План исправлений по аудиту 16.11.2026

Источник: независимый аудит на коммите `586e03e` (main `36ce706` + `docs/overview.md`). План написан для исполнителя-модели: каждая задача самодостаточна, читать весь план не нужно — только «Общие правила» и свою задачу.

**База для всех веток — текущий `main` (не ниже `6817d0f`).** После аудита в main слиты `fix/bot-ux-progress` и `fix/long-posts-ru-translation`. Они уже закрыли часть находок — см. «Что уже исправлено». Номера строк в плане — по коммиту аудита; в main они сдвинулись, ищи по именам функций.

## Что уже исправлено в main (не делать повторно)

| Находка | Что сделано в main | Что осталось |
|---|---|---|
| AUD-001 (молчание при постоянной ошибке писателя) | `jobs/worker.py`: `PermanentProviderError` в `ALWAYS_PERMANENT`, `on_dead=DeadJobReporter(...)` шлёт сообщение владельцу | Писатель в Docker-образе всё ещё не работает — решение владельца (раздел «Нужно от владельца») |
| AUD-008 (частично) | то же | Классификация кодов выхода CLI — задача A2 |
| Алерт «бэкапа не было» на dev | `ops/alerts.py`: `env != "dev"` | — |

## Общие правила для исполнителя

1. Одна задача = одна ветка от свежего `main` в свободном слоте: `scripts/slot.sh <N> <ветка>`. Работать только в папке слота.
2. **TDD (раздел 1.0 ТЗ):** первый коммит — только новые красные тесты (и пустые заглушки, если без них не импортируется). Убедись, что они падают по нужной причине. Затем код. Существующие тесты не править; если существующий тест закрепляет старое (ошибочное) поведение — отдельный коммит `test-fix: …` с объяснением в сообщении.
3. Перед началом каждой задачи **перечитай названные файлы в main** и убедись, что дефект ещё есть. Если уже исправлен — сообщи главному чату и остановись.
4. Правила слоёв: `sqlalchemy` импортируется только внутри `xcopilot/db/`; SQL — только в `db/repos/*`. Каждое новое действие пишет событие через `metrics.events.emit`, если это шаг конвейера.
5. Готово = `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest` зелёные, покрытие новых строк ≥ 85%, `scripts/check_tdd.sh` ок.
6. Не сливать в main, не деплоить, не трогать `.env`, живого бота и его БД. По готовности — сообщение главному чату: «готово, ветка X», список изменённых файлов, что не сделано.
7. Вопросы и план — главному чату через SendMessage; ответа владельца не ждать.
8. Не расширять задачу. Замеченное рядом — списком в отчёте.

## Порядок и параллельность

| Волна | Задачи (ветки) | Можно параллельно? |
|---|---|---|
| 1 | A `fix/llm-resilience`, B `fix/account-handle`, C `fix/ops-rollback-docs` | да, файлы не пересекаются |
| 2 | D `fix/telegram-delivery`, E `fix/worker-lanes`, F `fix/ops-hygiene`, H `feat/device-transfer` | да; D стартует после слияния B (оба трогают `bot/texts.py`); H — после слияния C (оба трогают `Makefile`, `README.md`, `docs/runbook.md`) |
| 3 | I `feat/heartbeat` | после слияния E (оба трогают `app.py`) |
| позже | G — данные и ключи владельца, этап 15b | когда владелец пришлёт 30 тем, 30 постов и ключи X |

Модель и усилие — в заголовке каждой задачи (по таблице из `AGENTS.md`).

---

## Задача A. Устойчивость LLM-шлюза — `fix/llm-resilience` (Sonnet, medium)

Закрывает AUD-003 и остаток AUD-008. Файлы: `src/xcopilot/llm/resilience.py`, `src/xcopilot/llm/gateway.py`, `src/xcopilot/llm/providers/claude_cli.py`, тесты в `tests/unit/llm/` (найди существующие файлы тестов предохранителя и `claude_cli`, добавляй рядом).

### A1. Предохранитель залипает после неудачной пробы (AUD-003)

**Дефект.** `CircuitBreaker.before_call()` в состоянии «разомкнут, пауза прошла» ставит `_probing = True`. Сбросить флаг могут только `on_success()` и `on_failure()`. `LLMGateway._invoke` зовёт `on_failure()` только для `TransientProviderError`, `on_success()` — только при успехе. Если проба кончилась `PermanentProviderError`, любым другим исключением или отменой (`CancelledError`) — флаг остаётся, и `before_call()` бросает `CircuitOpen` до перезапуска процесса.

**Красные тесты (сначала):**
- `test_breaker_probe_aborted_allows_next_probe_after_reset` (unit, `CircuitBreaker` + `FakeClock`): 5 × `on_failure` → +31 с → `before_call()` → `on_probe_aborted()` → сразу `before_call()` бросает `CircuitOpen` → +31 с → `before_call()` проходит. `open_since` не изменился.
- `test_gateway_permanent_error_on_probe_does_not_stick_breaker` (уровень шлюза, на подделке провайдера): 5 временных сбоев, пауза, проба с `PermanentProviderError`, пауза, следующий вызов доходит до провайдера (а не падает `CircuitOpen`).
- то же для произвольного исключения (`RuntimeError`) из провайдера.

**Код:**
1. В `CircuitBreaker` добавить метод `on_probe_aborted()`: если `_probing` — `_probing = False`, `_opened_at = now` (новая пауза `reset_after`), `_open_since` не трогать, `_failures` не трогать. Если не проба — ничего не делать.
2. В `LLMGateway._invoke`: обернуть вызов так, чтобы при любом исходе, кроме успеха и `TransientProviderError`, вызывался `breaker.on_probe_aborted()`. Практически: флаг `settled = False`; в ветке успеха и в ветке `Transient` ставить `True`; в `finally` — `if not settled: breaker.on_probe_aborted()`. `finally` должен отработать и при `CancelledError`, и при не-`ProviderError`.
3. Поведение при обычной (не пробной) постоянной ошибке не меняется: счётчик сбоев не растёт.

### A2. Коды выхода Claude CLI (остаток AUD-008)

**Дефект.** `ClaudeCliProvider._parse`: любой ненулевой код выхода → `SubscriptionLimit` (владелец видит «лимит подписки», шлюз повторяет и размыкает предохранитель), даже если причина — «не выполнен вход» или неверный флаг. `SubprocessRunner` ловит только `FileNotFoundError`; `PermissionError` и прочие `OSError` уходят «сырыми».

**Красные тесты:**
- ненулевой код + маркер лимита в `result`/stderr → `SubscriptionLimit(kind="limit")` (как сейчас);
- ненулевой код + маркер входа (`LOGIN_MARKERS`) в `result` или stderr → `PermanentProviderError(kind="not_logged_in")`;
- ненулевой код без маркеров → `TransientProviderError(kind="exit_code")`, и это **не** `SubscriptionLimit` (проверь `type(exc) is TransientProviderError` или отсутствие наследования — посмотри иерархию в `llm/errors.py`);
- `PermissionError` при запуске → `PermanentProviderError(kind="cli_not_runnable")`.

**Код:** поправить ветку `output.returncode != 0` в `_parse` в порядке: лимит → вход → прочее; в `complete` добавить `except OSError` после `except FileNotFoundError`. Тексты ошибок остаются фиксированными метками (вывод CLI в ошибку не класть).

Существующий тест, ожидающий `SubscriptionLimit` на «голый» код выхода, — править отдельным коммитом `test-fix:` (смена поведения по аудиту AUD-008).

**Готово:** все новые тесты зелёные; `docs/adr/0011-subscription-writer.md` — одна строка об изменении классификации.

---

## Задача B. Имя аккаунта X — `fix/account-handle` (Sonnet, medium)

Закрывает AUD-005. Файлы: `src/xcopilot/bot/routers/x_connect.py`, `src/xcopilot/bot/routers/settings.py`, `src/xcopilot/bot/states.py`, `src/xcopilot/bot/keyboards.py`, `src/xcopilot/bot/texts.py`, `src/xcopilot/db/repos/accounts.py`, `src/xcopilot/db/repos/authors.py` (только если нужен новый метод), тесты в `tests/integration/bot/`.

**Дефект.** `accounts.handle` задаётся один раз в `onboarding.create_account`: первый пример вида «@имя», иначе заглушка `user<tg_id>`. Больше нигде не обновляется. При подключении X `_fetch_me` возвращает настоящий хэндл, но `_set_account` пишет только `x_user_id`. Следствие: `review/publish.py::confirm_published` считает ссылку на собственный пост чужой (`ForeignLinkError`), ингест не считает свои посты своими.

**Красные тесты:**
- `test_x_connect_updates_account_handle`: аккаунт с хэндлом `user123`, подключение X (подделка `/2/users/me` → `RealName`) → в БД `handle == "realname"` (нормализация как в `bot/parsing.normalize_handle`), автор «свой» (`authors.set_self`) указывает на новый хэндл.
- `test_x_connect_keeps_handle_when_taken`: хэндл занят другим аккаунтом `kind='x'` → хэндл не меняется, подключение не падает, в лог `account_handle_conflict`.
- `test_settings_set_handle`: Настройки → «Мой аккаунт X» → владелец шлёт `@NewName` → хэндл обновлён, ответ с подтверждением; мусор → подсказка, состояние не сбрасывается; `/cancel` выходит.
- `test_publish_link_accepted_after_handle_update`: после обновления хэндла `confirm_published` со ссылкой `https://x.com/RealName/status/1` не бросает `ForeignLinkError`.

**Код:**
1. `AccountsRepo`: метод `set_handle(account_id, handle) -> bool` — проверяет `get_by_handle`, при конфликте возвращает `False`, иначе обновляет.
2. `x_connect.py`: после `_fetch_me`, если `me_handle` есть — `set_handle` + `AuthorsRepo.set_self(handle)` в одной транзакции с `_set_account`.
3. Настройки: кнопка «Мой аккаунт X» в `keyboards.settings_menu()`, состояние `SettingsFlow.handle`, обработчик по образцу `SettingsFlow.tz`. Показать текущий хэндл в `texts.settings_summary`. Событие через `rec("settings.handle")`.
4. В `onboarding.create_account` ничего не менять.

**Готово:** тесты зелёные; `docs/bot.md` — строка про новую кнопку.

---

## Задача C. Откат миграций, сборка в CI, README — `fix/ops-rollback-docs` (Sonnet, medium)

Закрывает AUD-006, AUD-011, AUD-017, AUD-018. Файлы: `src/xcopilot/db/migrate.py`, `docs/runbook.md`, `README.md`, `Makefile`, `compose.yaml`, `compose.prod.yaml`, `.github/workflows/ci.yml`, тесты в `tests/integration/db/` и `tests/unit/ops/`.

### C1. Откат миграции из контейнера (AUD-006)

**Дефект.** Runbook §3 велит `docker compose run … app alembic downgrade <ревизия>`, но в образ не копируется `alembic.ini` — команда не стартует.

**Красные тесты:**
- `test_migrate_cli_downgrade_to_revision` (`empty_db_url`): `main([])` → head; `main(["downgrade", "0009"])` → версия в `alembic_version` = `0009`, код 0.
- `test_migrate_cli_downgrade_requires_revision`: `main(["downgrade"])` → код 2, БД не тронута.
- `test_migrate_cli_refuses_base_without_flag`: `main(["downgrade", "base"])` → код 2; с `--yes` — выполняется.
- `test_runbook_has_no_bare_alembic_command` (unit): в `docs/runbook.md` нет подстроки `app alembic `.

**Код:** `db/migrate.py::main(argv: list[str] | None = None)`: без аргументов — как сейчас; `downgrade <rev> [--yes]` — `command.downgrade(alembic_config(url), rev)`, партиции не создавать. `if __name__` передаёт `sys.argv[1:]`. Runbook §3, шаг 2: заменить команду на `python -m xcopilot.db.migrate downgrade <ревизия>`.

### C2. Dev-compose и имя хоста (AUD-017)

- `compose.yaml`, сервис `app`: том `appdata:/app/data`, переменные `DATA_DIR=/app/data`, `FASTEMBED_CACHE_PATH=/app/data/fastembed` (как в проде); объявить том.
- Оба compose-файла, сервис `app`: `hostname: xcopilot-app` — чтобы `Worker.recover()` находил задачи прошлого контейнера после пересоздания.
- Проверь, что `tests/unit/ops/test_release_config.py` и `xcopilot.ops.security` по-прежнему зелёные; добавь проверку `hostname` в тест конфигурации.

### C3. CI (AUD-018)

В `.github/workflows/ci.yml` добавить отдельный job `image`: `docker build -t xcopilot:ci .` и `docker run --rm xcopilot:ci python -c "import xcopilot.app"`. Без `continue-on-error`. Сканеры зависимостей не добавлять (решение владельца, раздел G).

### C4. README и Makefile (AUD-011)

- `README.md`: ссылка на ТЗ → `docs/architecture-v3.1-plan.md`; добавить ссылку на `docs/overview.md` и раздел «Быстрый старт» (5–7 строк): `cp .env.example .env` → заполнить `BOT_TOKEN`, `OWNER_TG_IDS`, `SECRETS_KEY`, `DEEPSEEK_API_KEY` → `make up` → `/start` в боте; тесты — `make test` (нужен Docker). Отметить, что писатель в контейнере пока не работает без решения из раздела G.
- `Makefile`, цель `up`: `docker compose up -d --build --wait db`, затем `docker compose run --rm --no-deps app python -m xcopilot.db.migrate`, затем `docker compose up -d --build`.
### C5. Сканер зависимостей (AUD-018, решение владельца 16.11: «как считаешь нужным» → да, но не блокирующий)

Новый файл `.github/workflows/audit.yml`: запуск по расписанию (понедельник, 06:00 UTC), вручную (`workflow_dispatch`) и при изменении `uv.lock`. Шаги: checkout → `astral-sh/setup-uv` (та же версия, что в `ci.yml`) → `uv export --frozen --no-dev --no-emit-project -o /tmp/req.txt` → `uvx pip-audit -r /tmp/req.txt --strict`. Упавший запуск — письмо владельцу от GitHub; обычные пуши он не блокирует (отдельный workflow, не job в `ci.yml`). Проверь точные флаги `uv export` и `pip-audit` по их `--help`, не по памяти. В отчёте главному чату — результат первого запуска (список уязвимостей, если есть; сами обновления зависимостей — отдельная задача).

- `docs/README.md` не трогать (его ведёт главный чат). В отчёте напомни: таблица «Очередь этапов» устарела (этапы 10–15 помечены «—»), `publications.match` идёт только в 21:00.

---

## Задача D. Доставка в Telegram — `fix/telegram-delivery` (Opus, medium)

Закрывает AUD-004. Стартовать после слияния задачи B. Файлы: `src/xcopilot/bot/notify.py`, `src/xcopilot/review/presenter.py`, `src/xcopilot/ops/alerts.py`, `src/xcopilot/bot/routers/cards.py`, `src/xcopilot/bot/texts.py`, тесты в `tests/integration/review/`, `tests/integration/ops/`, `tests/unit/bot/`. Сначала прочитай текущий `bot/notify.py` в main — он менялся в `fix/bot-ux-progress`.

**Дефект.** Нигде нет повтора отправки (ТЗ 0.3: «Отправка повторяется»):
- `CardPresenter._send` при любой ошибке возвращает `None`; `show_run` возвращает 0; задача `quality.gate` завершается успешно. Варианты остаются в статусе «ждёт показа» без карточки — владелец их не увидит никогда.
- `Notifier.notify` глотает ошибки; `ops.alerts.send_alerts` после этого ставит отметку «отправлено». У алерта `job_dead` `repeat=None` — он теряется навсегда.

**Красные тесты:**
- `test_send_retries_on_retry_after`: первая отправка бросает `TelegramRetryAfter(retry_after=2)`, вторая успешна → сообщение доставлено, `sleep` вызван с 2.
- `test_send_retries_on_network_error_then_gives_up`: `TelegramNetworkError` 3 раза → исключение `DeliveryFailed`; паузы 1, 2, 4 с (подменный `sleep`).
- `test_send_does_not_retry_bad_request`: `TelegramBadRequest` → сразу `DeliveryFailed`, без повторов.
- `test_gate_job_retries_when_card_not_delivered`: Telegram падает → задача `quality.gate` получает статус `queued` (повтор), статусы вариантов не `shown`; со второй попытки карточка приходит один раз, ворота судью повторно не вызывают.
- `test_alert_not_marked_when_delivery_failed`: отправка падает → отметки `alert:job_dead:<id>` нет; следующий проход отправляет алерт.
- `test_show_callback_reports_delivery_failure`: кнопка «Показать» при сбое отвечает понятным текстом, а не кодом E500.

**Код:**
1. `bot/notify.py`: функция `send_with_retry(bot, chat_id, text, *, sleep=asyncio.sleep, attempts=3, **kwargs) -> Message`. `TelegramRetryAfter` → ждать `min(retry_after, 30)` и повторить (не считается попыткой больше 3 раз подряд); `TelegramNetworkError`, `TelegramServerError` → пауза 1/2/4 с; прочее — сразу. По исчерпании — `DeliveryFailed` (новый класс, наследник `xcopilot.errors.ExternalServiceError`).
2. `Notifier.notify` использует `send_with_retry`; поведение прежнее (ошибка получателя в лог, возвращается число доставленных). `notify_owner` оставить.
3. `ops/alerts.py::send_alerts`: протокол уведомителя — `notify(account_id, text) -> int`; отметку `set_mark` ставить только при `sent > 0`; иначе `log.warning("ops_alert_not_delivered")` и алерт не попадает в `sent`.
4. `review/presenter.py`: `_send` через `send_with_retry`; у `show_run` параметр `strict: bool = False`; при `strict` и пустом `sent_to` (а получатели были) — бросить `DeliveryFailed`. `show_for_drafts` вызывает со `strict=True`. Так задача `quality.gate` упадёт и повторится по правилам очереди (ворота идемпотентны: решение восстанавливается из `lint_report`).
5. `bot/routers/cards.py`, ветка `cb.SHOW`: ловить `DeliveryFailed` → `query.answer(texts.CARD_SEND_FAILED, show_alert=True)`.
6. Известное ограничение (записать в ADR 0025 одной строкой): при сбое посреди длинной карточки части текста могут прийти дважды.

---

## Задача E. Две полосы воркера — `fix/worker-lanes` (Opus, medium)

Закрывает AUD-007. Сначала короткий план главному чату (это архитектурное изменение), потом код. Файлы: `src/xcopilot/db/repos/jobs.py`, `src/xcopilot/jobs/queue.py`, `src/xcopilot/jobs/worker.py`, `src/xcopilot/app.py`, `docs/adr/` (новый ADR), тесты в `tests/integration/jobs/` и `tests/integration/bot/test_release_failures.py` (не менять, только чтобы оставались зелёными).

**Дефект.** Один воркер берёт задачи строго по одной. Вызов писателя — до 180 с, с повторами шлюза и починкой ответа — минуты. Всё это время стоят приём материалов, разбор и `ops.alerts`.

**Решение.** Два экземпляра `Worker` в одном процессе: «медленная полоса» — виды `writer.generate`, `writer.topics`, `quality.gate`, `brain.extract`, `learning.propose`, `reports.daily` (всё, что зовёт роль `writer`; уточни по коду, какие обработчики зовут `Role.WRITER`), «быстрая полоса» — всё остальное. Внутри полосы — по одной задаче, как сейчас.

**Красные тесты:**
- `test_claim_respects_kind_filter`: в очереди `writer.generate` и `ingest.process`; `claim(kinds=SLOW)` берёт только первую, `claim(exclude=SLOW)` — только вторую.
- `test_fast_lane_runs_while_slow_lane_busy`: обработчик `writer.generate` ждёт события; `ingest.process` в это время выполняется и завершается.
- `test_two_lanes_shutdown_releases_both_jobs`: `stop` во время двух задач → обе возвращены в очередь без засчитанной попытки.
- `test_recover_releases_jobs_of_both_lanes`: после «падения» задачи обеих полос возвращаются одним `recover()`.

**Код:**
1. `JobsRepo.claim(worker_id, limit, now, *, kinds: Sequence[str] | None = None, exclude: bool = False)` — добавить условие `Job.kind.in_(kinds)` / `notin_`. `JobQueue.claim` пробрасывает параметры.
2. `Worker.__init__`: `kinds`, `exclude_kinds`; `run_once` передаёт их в `claim`. `worker_id` вида `host:pid-slow` / `host:pid-fast` — проверь, что `host_prefix` (берётся через `rpartition(":")`) по-прежнему равен `host:`.
3. `app.py`: константа `SLOW_KINDS`, два воркера, поле `App.workers: list[Worker]` (старое `App.worker` оставить как быструю полосу — на него ссылаются тесты), `serve_app` запускает оба через существующий `guarded`.
4. Неизвестный вид задачи должен попадать в быструю полосу (она с `exclude`), чтобы пометиться `dead`.
5. Пул соединений (`db/engine.py`: 5 + 5) достаточен — не менять.
6. ADR: «две полосы воркера», с перечнем медленных видов и правилом, куда относить новые.

---

## Задача F. Мелкая гигиена — `fix/ops-hygiene` (Sonnet, medium)

Закрывает AUD-009, 012, 014, 015, 016, 021. Каждый пункт — свой коммит с тестом. Файлы названы в пунктах.

1. **Очистка старых задач (AUD-009).** Новая задача `ops.cleanup`, ежедневно 04:30 (добавить в `jobs/scheduler.py::DEFAULT_SCHEDULES`, `jobs/handlers.py::KNOWN_KINDS` и реестр). `OpsRepo.cleanup(account_id, older_than)`: удалить `jobs` со статусом `done`/`dead` и `finished_at` старше 30 дней; удалить `ops_marks` с ключом `alert:job_dead:%` старше 30 дней. `events` и журналы не трогать. Обработчик возвращает число удалённых строк. Тесты: старые удалены, свежие и `queued`/`running` целы, чужой аккаунт не тронут. Проверь, не закреплено ли число видов задач в тестах (`KNOWN_KINDS`) — обнови через `test-fix`, если да.
2. **Эмбеддинги вне цикла событий (AUD-012).** `brain/owner.py` (2 места), `brain/detectors.py`, `brain/extract.py`: `ctx.embedder.embed([...])` → `await asyncio.to_thread(ctx.embedder.embed, [...])`. Тест: подменный эмбеддер запоминает `threading.get_ident()` — он не равен идентификатору основного потока.
3. **«Техработы» при недоступной БД (AUD-014).** В `db/engine.py` — функция `is_db_unavailable(exc: BaseException) -> bool` (`sqlalchemy.exc.OperationalError`, `InterfaceError`, `ConnectionRefusedError`, `asyncpg` `CannotConnectNowError`, `TimeoutError` из пула). `bot/middlewares/errors.py`: если `is_db_unavailable(exc)` — ответ `texts.MAINTENANCE` («Идут техработы, попробуй через пару минут.»), код в лог `E503`. Тест: движок с недоступным адресом → пользователь получает текст техработ.
4. **Планировщик: честное описание (AUD-015).** Код не менять. В докстринге `jobs/scheduler.py` заменить «пропущенный запуск догоняется один раз» на «догоняется в пределах тех же местных суток; пропущенный день и пропущенная недельная сводка не догоняются». Строка в ADR 0022.
5. **Сверка публикаций и бюджет (AUD-016).** `review/jobs.py::handle_publications_match`: ловить `(XError, BudgetExceeded)`. Тест: X-клиент бросает `BudgetExceeded` → задача завершается, просроченные черновики становятся `unconfirmed`.
6. **Флуд от незнакомцев (AUD-021).** `bot/middlewares/flood.py`: предупреждение `texts.FLOOD` слать только если `user.id` есть в `settings.owner_tg_ids`; удалять запись из `_hits`, когда очередь опустела. Тест: 100 обновлений от чужого id → бот ничего не отправил, размер `_hits` для устаревших записей не растёт.

Не входит (бэклог, решает главный чат): AUD-010 (внешний сторож процесса), AUD-013 (вызовы модели внутри транзакции БД), повтор вопроса о неоднозначном совпадении.

---

## Задача H. Перенос на другое устройство — `feat/device-transfer` (Sonnet, medium)

Решение владельца 16.11: «пока работаем на Mac, но нужна возможность автоматически перенести бота на другое устройство с другой подпиской». Закрывает AUD-001 для режима «на устройстве владельца». Стартовать после слияния задачи C. Сначала проверь у главного чата, не делает ли похожее сессия `bot-control` (запуск/остановка бота) — если да, строй поверх её скриптов, а не рядом.

**Как бот запущен сейчас** (`docs/README.md`, запись 16.11): приложение на Mac через `uv run`, БД — контейнер Docker на `127.0.0.1:5433`, писатель — `claude -p` по подписке этого Mac. Службы автозапуска и скриптов переноса нет; порт 5433 в `compose.yaml` не описан (поднят вручную).

**Что значит «автоматически».** На старом устройстве — одна команда, на новом — одна команда плюс единственный ручной шаг `claude login` (вход в подписку нельзя сделать за человека). Подписка нигде в коде и БД не хранится: бот вызывает тот `claude`, который установлен и залогинен на устройстве. Поэтому «другая подписка» = другой вход на новом устройстве, данные от неё не зависят.

**Что переносится:** дамп БД (все знания, материалы, черновики, метрики, зашифрованные токены X), файл `.env` (в нём `SECRETS_KEY`, без него токены X не расшифровать, и токен бота), папка `data/`. Код берётся из git.

**Файлы:** новые `compose.host.yaml`, `scripts/setup.sh`, `scripts/transfer-export.sh`, `scripts/transfer-import.sh`, `scripts/service.sh`, шаблоны `scripts/templates/com.xcopilot.bot.plist` и `scripts/templates/xcopilot.service`; правки `Makefile`, `docs/runbook.md` (новый раздел «Перенос на другое устройство»), `README.md`, `.env.example`, `.gitignore` (`*.xcbundle`); тесты `tests/integration/db/test_transfer.py`, `tests/unit/ops/test_host_mode.py`.

**Красные тесты:**
- `test_transfer_export_import_roundtrip` (по образцу `test_backup_script_produces_restorable_dump`, БД в testcontainers через `PG_CONTAINER`): заполнить БД → `transfer-export.sh` с паролем из файла → получить один файл-пакет → в пустую БД `transfer-import.sh` → число строк в `sources`, `drafts`, `knowledge_items` совпало, `.env` восстановлен байт-в-байт, `data/` на месте.
- `test_transfer_bundle_is_encrypted`: в пакете нет открытого текста токена бота (поиск значения из тестового `.env`) и строки `PGDMP`.
- `test_transfer_import_refuses_wrong_passphrase`: неверный пароль → код 2, БД и `.env` не тронуты.
- `test_transfer_import_refuses_non_empty_db_without_flag`: в целевой БД есть аккаунты → код 2 без `--force`.
- `test_host_compose_publishes_db_only_on_loopback`: в `compose.host.yaml` один сервис `db`, порт привязан к `127.0.0.1`.
- `test_service_templates_render`: шаблоны launchd и systemd после подстановки путей содержат рабочую папку, команду запуска, автоперезапуск и путь к логам; в них нет секретов.
- `test_setup_check_reports_missing_tools`: `setup.sh --check` при `PATH` без `claude` выдаёт понятную строку «нет Claude CLI» и код 1 (ничего не устанавливает).

**Код и скрипты:**
1. `compose.host.yaml` — только `db` (как в `compose.yaml`), порт `127.0.0.1:${XC_DB_PORT:-5433}:5432`, том `pgdata`. В `.env.example` — блок «Режим на устройстве»: `DATABASE_URL=postgresql+asyncpg://xcopilot:xcopilot@127.0.0.1:5433/xcopilot`, `COMPOSE_FILE=compose.host.yaml`.
2. `scripts/setup.sh [--check]` — идемпотентная подготовка устройства (macOS и Linux). По порядку: проверка `docker`, `uv`, `claude`, `openssl` (чего нет — понятная строка, что поставить, и выход; сам ничего не скачивает); `.env` есть и права 600; `uv sync --frozen`; `docker compose up -d --wait db`; `python -m xcopilot.db.migrate`; проверка подписки — `python -m xcopilot.llm ping` для роли writer (один короткий вызов; при «не выполнен вход» — строка «выполни `claude login` и запусти setup ещё раз»); установка службы (`service.sh install`); итоговая сводка «✓/✗». Любая ошибка — ненулевой код, повторный запуск безопасен.
3. `scripts/service.sh install|start|stop|status|logs|uninstall` — служба автозапуска: на macOS `~/Library/LaunchAgents/com.xcopilot.bot.plist` (`RunAtLoad`, `KeepAlive`, рабочая папка — корень репозитория, команда `uv run python -m xcopilot.main`, переменные из `.env` подгружает обёртка `set -a; . ./.env`, логи — `~/Library/Logs/xcopilot/`), на Linux — пользовательский unit systemd `xcopilot.service` (`Restart=always`). В `PATH` службы должны попасть каталоги `uv` и `claude` (определи через `command -v` при установке).
4. `scripts/transfer-export.sh [файл]` — на старом устройстве: `service.sh stop` (иначе два устройства одновременно опрашивают Telegram с одним токеном и задачи остаются «в работе»); дамп через существующий `scripts/backup.sh <временный файл>`; архив `tar` из дампа, `.env`, `data/` и файла `manifest.json` (версия схемы `alembic_version`, коммит git, дата, имя устройства); шифрование `openssl enc -aes-256-cbc -pbkdf2` паролем из `XC_TRANSFER_PASSPHRASE_FILE` или с запросом в терминале; результат `xcopilot-<дата>.xcbundle`, права 600; временные файлы удаляются в `trap`. В конце — подсказка: «скопируй файл на новое устройство; бот здесь остановлен, чтобы запустить снова — `scripts/service.sh start`».
5. `scripts/transfer-import.sh <пакет> [--force]` — на новом устройстве (репозиторий уже склонирован): расшифровать во временную папку (проверка пароля до любых изменений, как в `restore.sh`); если `.env` уже есть — сохранить копию `.env.before-import`; положить `.env` (600) и `data/`; `docker compose up -d --wait db`; отказаться, если в БД уже есть рабочие аккаунты (без `--force`); `scripts/restore.sh` дампа; `python -m xcopilot.db.migrate` (код может быть новее дампа); затем `scripts/setup.sh`. Сравнить `manifest.json` с текущим коммитом и предупредить, если схема дампа новее кода (тогда остановиться: сначала `git pull`).
6. `Makefile`: цели `setup`, `start`, `stop`, `status`, `logs`, `export`, `import FILE=…`.
7. `docs/runbook.md`, раздел «Перенос на другое устройство» — ровно шаги владельца: (1) старое устройство: `make export` → файл; (2) новое: поставить Docker, uv, Claude Code; `git clone`; `claude login` (подписка этого устройства); (3) `make import FILE=…`; (4) `/health` в боте. Отдельно: что делать, если старое устройство недоступно (последний ежедневный бэкап + сохранённая копия `.env`), и предупреждение — не запускать бота на двух устройствах сразу.
8. Ограничения записать в runbook: Mac во сне — бот стоит (совет: `pmset`/«не засыпать от сети» или `caffeinate` в службе — выбери и опиши один вариант); Docker Desktop должен стартовать при входе в систему.

**Готово:** тесты зелёные; на этом Mac вручную проверено `scripts/setup.sh --check` (без установки службы и без остановки живого бота — их проверяет главный чат в согласованное с владельцем время).

---

## Задача I. Внешний сторож — `feat/heartbeat` (Sonnet, medium)

Решение владельца 16.11: «внешний сторож нужен». Закрывает AUD-010. Стартовать после слияния задачи E. Файлы: `src/xcopilot/config.py`, `src/xcopilot/app.py`, новый `src/xcopilot/ops/heartbeat.py`, `src/xcopilot/jobs/worker.py` и `jobs/scheduler.py` (только отметка времени), `src/xcopilot/ops/health.py`, `.env.example`, `docs/runbook.md`, новый ADR; тесты `tests/unit/ops/test_heartbeat.py`, `tests/integration/bot/`.

**Дефект.** Все алерты шлёт сам процесс. Если процесс умер, Mac уснул или пропал интернет — владелец ничего не узнает. Контейнерный healthcheck проверяет только БД.

**Решение.** «Пульс»: раз в минуту процесс шлёт GET на адрес внешней службы-сторожа (например, healthchecks.io, бесплатный тариф). Если пульса нет дольше заданного времени, служба сама пишет владельцу (в Telegram или на почту). Адрес — в `.env` (`HEARTBEAT_URL`, пусто = выключено).

**Красные тесты:**
- `test_heartbeat_disabled_without_url`: пустой `HEARTBEAT_URL` → ни одного запроса.
- `test_heartbeat_pings_when_loops_alive` (respx + `FakeClock`): воркер и планировщик отмечались недавно → GET на адрес.
- `test_heartbeat_reports_fail_when_scheduler_stalled`: планировщик не отмечался дольше 5 минут → запрос на `<адрес>/fail`, обычного пульса нет.
- `test_heartbeat_tolerates_long_job`: воркер занят одной задачей 10 минут → пульс идёт (занятость — не зависание); занят дольше 45 минут → `/fail`.
- `test_heartbeat_network_error_does_not_crash_loop`: сбой сети → запись в лог, цикл жив.
- `test_heartbeat_url_masked_in_logs`: значение `HEARTBEAT_URL` в логах заменено на `***`.
- `test_health_shows_heartbeat_state`: `/health` показывает «Сторож: включён, последний пульс …» или «выключен».
- `test_env_example_covers_all_settings` (существующий) остаётся зелёным — добавь переменную в `.env.example`.

**Код:**
1. `config.py`: `heartbeat_url: str = ""`, `heartbeat_interval: float = 60`; значение адреса добавить в `secret_values()` (адрес сторожа — секрет: зная его, можно слать ложный пульс).
2. `Worker` и `Scheduler`: поля `last_tick_at` (обновляются в начале каждой итерации цикла) и у воркера `busy_since` (время начала текущей задачи или `None`). Только запись времени, логика не меняется. После задачи E воркеров два — учитывать оба.
3. `ops/heartbeat.py`: `Heartbeat(http, clock, url, probes)`; `run(stop)` — цикл как у планировщика; `healthy()` — планировщик тикал < 5 мин назад; каждый воркер либо тикал < 5 мин назад, либо занят задачей < 45 мин. Здоров → GET `url`, нет → GET `url + "/fail"`. Таймаут 10 с, ошибки сети — в лог.
4. `app.py::serve_app`: четвёртый цикл через `guarded`, только если адрес задан. При штатной остановке ничего не слать (сторож сам заметит тишину — это нормально для переноса и обновления; опиши в runbook, как поставить проверку на паузу).
5. `docs/runbook.md`, раздел «Внешний сторож» — шаги владельца: завести проверку (период 5 минут, льгота 10 минут), подключить уведомление в Telegram или на почту, вставить адрес в `.env`, перезапустить, убедиться в `/health`. Предупреждение: сон Mac вызовет алерт — это ожидаемо.
6. ADR: почему внешний пульс, а не второй процесс на том же устройстве.

Создание учётной записи во внешней службе и вставку адреса делает владелец — исполнитель этого не делает.

---

## G. Решения владельца (16.11.2026) и что отложено

| # | Вопрос | Решение владельца | Что из этого следует |
|---|---|---|---|
| 1 | Как работает писатель | Пока на Mac по подписке; нужна возможность автоматически перенести на другое устройство с другой подпиской | задача H; Docker-образ с писателем и сервер — не сейчас |
| 2 | 30 тем и 30 настоящих постов | чуть позже | baseline и этап 15b ждут; AUD-002 остаётся открытым |
| 3 | Ключи X и живые вызовы | потом | кассеты остаются синтетическими; живая проверка X и vision — в 15b |
| 4 | Внешний сторож | нужен | задача I; учётную запись в службе заводит владелец (шаги — в runbook) |
| 5 | Сканер зависимостей | на усмотрение аудитора → да, еженедельный и не блокирующий | пункт C5 |

Когда придут п. 2–3 — отдельная задача главного чата (этап 15b): живой сквозной прогон, проверка флага `--max-turns` у текущей версии CLI (AUD-019: в `claude --help` версии 2.1.319 его нет), запись `tests/evals/baseline.json`, тег `v1.0.0`.

## Проверка после всех волн (главный чат)

1. Все ветки слиты, `uv run pytest` — не меньше 1097 + новые тесты, три прогона подряд зелёные.
2. `docker build` проходит; в контейнере `python -m xcopilot.db.migrate downgrade 0009`, затем `python -m xcopilot.db.migrate` — без ошибок (на учебной БД).
3. Вручную в боте: подключение X меняет хэндл; ссылка на свой пост принимается; генерация идёт, пока разбирается материал.
4. `docs/README.md`, `docs/overview.md` и память обновлены; находки аудита помечены закрытыми.
