# Runbook xcopilot

Эксплуатация MVP на одном сервере (этап 15, ADR 0028). Команды — из корня репозитория на сервере. Везде ниже подразумевается:

```bash
export COMPOSE_FILE=compose.prod.yaml
```

Один процесс `app` (бот + воркер + планировщик) и `db` (PostgreSQL 18 + pgvector). Наружу не открыт ни один порт: бот сам ходит в Telegram (long polling).

## 1. Первый запуск

1. Сервер: Docker Engine + Compose v2, `rsync`, `openssl`. Отдельный пользователь без sudo для приложения.
2. Клонировать репозиторий (deploy-ключ только на чтение).
3. `.env` из шаблона и права:
   ```bash
   cp .env.example .env && chmod 600 .env
   ```
   Заполнить: `BOT_TOKEN`, `OWNER_TG_IDS`, `SECRETS_KEY` (Fernet), ключи моделей, `POSTGRES_PASSWORD` (`openssl rand -hex 24`) и **тот же пароль** в `DATABASE_URL`: `postgresql+asyncpg://xcopilot:<пароль>@db:5432/xcopilot`. Писатель: на сервере нет `claude login` — либо `ANTHROPIC_API_KEY` и `config/models.toml` на API, либо Claude CLI в образе (решение 15b).
4. Пароль шифрования бэкапов — отдельный файл **вне** репозитория и вне каталога бэкапов:
   ```bash
   openssl rand -base64 48 > ~/.xcopilot-backup.pass && chmod 600 ~/.xcopilot-backup.pass
   ```
   В `.env`: `BACKUP_PASSPHRASE_FILE=/home/<user>/.xcopilot-backup.pass`, `BACKUP_REMOTE=<user>@<другой-хост>:/srv/xcopilot-backups/`. Копию пароля — в менеджер паролей владельца: без него бэкап не восстановить.
5. Сборка, миграции, запуск:
   ```bash
   docker compose build
   docker compose up -d db
   docker compose run --rm --no-deps app python -m xcopilot.db.migrate
   docker compose up -d
   ```
6. Проверка: `scripts/smoke.sh` (код 0 — всё в порядке), затем `/health` в боте от владельца.
7. Cron бэкапа (раздел 5) и сразу один ручной прогон, чтобы `/health` показал «Бэкап: ✅».

## 2. Обновление

```bash
git fetch && git log --oneline HEAD..origin/main      # что придёт
scripts/backup.sh backups/pre-update-$(date +%F).dump  # разовый дамп (без отметки и ротации)
git pull --ff-only
docker compose build app
docker compose run --rm --no-deps app python -m xcopilot.db.migrate
docker compose up -d app
scripts/smoke.sh
```

`up -d app` шлёт SIGTERM: воркер доделывает текущую задачу до 20 с, иначе возвращает её в очередь без засчитанной попытки; после старта задачи, которые держал прошлый процесс, берутся сразу. Проверить в боте `/health`: очередь не растёт, «умерло за сутки» не увеличилось.

## 3. Откат версии

1. `git log --oneline` → нужный коммит или тег; `git checkout <тег>`.
2. Если в обновлении была миграция — откатить её **до** смены кода: `docker compose run --rm --no-deps app alembic downgrade <ревизия>` (номер — в `src/xcopilot/db/migrations/versions/`). Миграции с удалением данных обратно не откатываются: восстановить дамп `pre-update-…` (раздел 4).
3. `docker compose build app && docker compose up -d app && scripts/smoke.sh`.

## 4. Восстановление из бэкапа (учебное — раз в месяц)

Учебное восстановление делается в **отдельную** БД, рабочая не трогается:

```bash
docker compose exec -T db createdb -U xcopilot xc_restore_check
DATABASE_URL=postgresql+asyncpg://xcopilot:x@db:5432/xc_restore_check \
  BACKUP_PASSPHRASE_FILE=~/.xcopilot-backup.pass \
  scripts/restore.sh backups/xcopilot-<время>.dump.enc </dev/null
docker compose exec -T db psql -U xcopilot -d xc_restore_check -c \
  "SELECT (SELECT count(*) FROM sources) AS sources, (SELECT count(*) FROM drafts) AS drafts,
          (SELECT count(*) FROM knowledge_items) AS knowledge"
docker compose exec -T db dropdb -U xcopilot xc_restore_check
```

Числа сравнить с рабочей БД (те же запросы). Записать дату проверки.

Настоящее восстановление (рабочая БД потеряна):

1. `docker compose stop app`.
2. Взять последний дамп из `BACKUP_DIR` или с хоста `BACKUP_REMOTE` (`rsync <хост>:/srv/xcopilot-backups/<файл> backups/`).
3. `scripts/restore.sh backups/<файл>` — спросит `yes`; всё идёт одной транзакцией: при ошибке старые данные целы. Зашифрованный дамп без верного `BACKUP_PASSPHRASE_FILE` не восстанавливается (код 2, БД не трогается).
4. `docker compose run --rm --no-deps app python -m xcopilot.db.migrate` (если дамп старее кода).
5. `docker compose up -d app && scripts/smoke.sh`.

## 5. Бэкап

Cron на хосте (пользователь приложения), раз в сутки:

```cron
15 3 * * * cd /srv/xcopilot && set -a && . ./.env && set +a && COMPOSE_FILE=compose.prod.yaml scripts/backup.sh >> backups/backup.log 2>&1
```

Без аргумента `backup.sh` делает: `pg_dump -Fc` → шифрование (`openssl aes-256-cbc -pbkdf2`) → `backups/xcopilot-<UTC>.dump.enc` (права 600) → копия `rsync` на `BACKUP_REMOTE` → удаление дампов старше `BACKUP_KEEP_DAYS` → отметка `backup.ok` в БД. Сбой — отметка `backup.failed`. Алерт «Бэкап не прошёл / не проходил 26 ч» приходит владельцу в Telegram; `/health` показывает время последнего.

Бэкап не прошёл: смотреть `backups/backup.log`. Частые причины — `db` не запущена, нет места (`df -h`), недоступен `BACKUP_REMOTE` (ssh-ключ, `known_hosts`), пуст файл пароля.

## 6. Ротация ключей

Все ключи — только в `.env`. После любой правки `.env`: `docker compose up -d app` (перечитать окружение), `/health`.

- **BOT_TOKEN:** @BotFather → Revoke → новый в `.env` → перезапуск. Старый перестаёт работать сразу.
- **Ключи моделей (`ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`, `TYPESAFE_API_KEY`):** создать новый в кабинете провайдера, заменить, перезапустить, убедиться в `/health` (🟢), затем отозвать старый.
- **X (OAuth):** токены лежат в БД зашифрованными `SECRETS_KEY`. Отозвать доступ приложения в настройках X → владелец заново подключает X в боте (⚙️ → X).
- **SECRETS_KEY:** смена делает старые токены X нечитаемыми. Порядок: новый ключ в `.env` → перезапуск → владелец переподключает X.
- **POSTGRES_PASSWORD:** `docker compose exec db psql -U xcopilot -c "ALTER USER xcopilot PASSWORD '<новый>'"` → поменять `POSTGRES_PASSWORD` и `DATABASE_URL` в `.env` → `docker compose up -d app`.
- **Пароль бэкапов:** новый файл → `BACKUP_PASSPHRASE_FILE`. Старые дампы остаются со старым паролем — хранить его, пока дампы не ушли по ротации.

## 7. Алерты владельцу и что делать

Проверка каждые 15 минут (`ops.alerts`); один и тот же алерт — не чаще раза в час.

| Алерт | Что делать |
|---|---|
| ⚠️ Задача … не выполнена после 5 попыток (код E-xxxx) | `docker compose exec db psql -U xcopilot -c "SELECT kind, last_error, payload FROM jobs WHERE status = 'dead' AND id::text LIKE '%xxxx'"`; в логах `docker compose logs app | grep job_failed`. Исправить причину и поставить заново (`UPDATE jobs SET status = 'queued', attempts = 0, run_at = now() WHERE id = …`). |
| Расход … 80% лимита | `/cost`; поднять лимит в ⚙️ или ждать следующих суток. |
| Провайдер недоступен дольше 15 минут | Статус провайдера, ключ (раздел 6), баланс. Задачи повторятся сами. |
| Строки в партиции DEFAULT | Не хватает месячных партиций: `docker compose run --rm --no-deps app python -m xcopilot.db.migrate` (создаёт партиции и переносит строки). |
| Бэкап не прошёл / не проходил | Раздел 5. |
| Голос уплывает | `/style`, последние карточки; при необходимости откат паспорта. |
| Откачено больше 30% автоматических решений | Пороги «Мозга» слишком мягкие: `/brain` → настройки → выключить автоиспытания, сообщить разработчику. |
| Очередь разбора стоит | `/health`: провайдер `cheap`; `docker compose logs app | grep brain.analyze`. |

## 8. «Мозг»: откат базы знаний, блокировка автора, переразбор

- **Откат базы знаний на дату** (плохая пачка материалов): в боте `/brain undo` → «Вчера» / «7 дней назад» / «30 дней назад» (или `/brain undo 2026-11-20`) → предпросмотр (сколько знаний создано, изменено, доказательств) → «Откатить». Знания, созданные после даты, уходят в архив, изменённые возвращаются к прежним текстам и весам; материалы и разборы не удаляются. Отменить откат — кнопка «Отменить откат» в том же сообщении.
- **Блокировка автора** (спам, накрутки, вредные советы):
  ```bash
  docker compose exec app python -m xcopilot.ops.admin block-author <аккаунт> <автор>
  ```
  Доказательства автора отзываются (`revoked_reason = author_blocked`), веса знаний пересчитываются сразу, автор получает `trust = -1` — его новые материалы не учат базу. Знания, которые держались только на нём, ослабнут на ближайшем ночном проходе (03:30). Если пачка уже наделала вреда — сначала откат на дату (выше), потом блокировка.
- **Переразбор материалов** (сменилась таксономия или промпт разбора, разбор падал):
  ```bash
  docker compose exec app python -m xcopilot.ops.admin reanalyze <аккаунт>         # упавшие и ждущие
  docker compose exec app python -m xcopilot.ops.admin reanalyze <аккаунт> --all   # все
  ```
  После каждого разбора сам ставится `brain.detect` — доказательства пересчитаются. Старый разбор остаётся в истории (`is_current = false`). Расход — роль `cheap` на каждый материал: перед `--all` посмотреть `/cost`.
- **Своя история без кусков** (импорт до этапа 14): разово `ingest.process` по `sources WHERE origin = 'history_backfill' AND status = 'parsed'` (ADR 0027, п. 13).

## 9. Логи и состояние

- `docker compose logs -f app` — JSON-логи (structlog), ротация 10 МБ × 5 файлов.
- `/health` (только администратор): БД, провайдеры, очередь, последний бэкап, очередь разбора, снимок базы знаний.
- `docker compose ps` — оба сервиса `healthy`.

## 10. Перед v1.0.0 (этап 15b)

1. Ключи и токены в `.env`, первый запуск по разделу 1, `scripts/smoke.sh` зелёный.
2. Учебное восстановление (раздел 4) — дата в `docs/README.md`.
3. 30 настоящих тем и 30 постов владельца в `tests/evals/golden_*.yaml`, `provisional: false`.
4. 7 дней работы: ≥ 20 карточек, `zero_edit_rate` посчитан; ≥ 50 материалов разобрано; ≥ 5 знаний в работе, одно подтверждено на своём посте; `brain_probation_win_rate` посчитан.
5. `uv run python -m tests.evals.run_evals --account <handle> --record` — baseline (с абляцией) в `tests/evals/baseline.json`, коммит.
6. Тег `v1.0.0`.
