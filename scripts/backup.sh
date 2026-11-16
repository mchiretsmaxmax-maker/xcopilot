#!/usr/bin/env bash
# Бэкап БД xcopilot: pg_dump -Fc → шифрование → копия на другой хост → отметка в БД.
# Запуск: scripts/backup.sh [файл]   (cron на хосте раз в сутки — docs/runbook.md, «Бэкап»)
# Переменные:
#   DATABASE_URL            обязательна (пользователь и имя БД берутся из неё)
#   PG_CONTAINER            id контейнера PostgreSQL; иначе `docker compose exec db`
#                           (на сервере: COMPOSE_FILE=compose.prod.yaml)
#   BACKUP_DIR              папка дампов (по умолчанию backups)
#   BACKUP_PASSPHRASE_FILE  файл с паролем: дамп шифруется openssl aes-256-cbc + pbkdf2 (.enc)
#   BACKUP_REMOTE           куда скопировать дамп rsync'ом: user@host:/path/ или локальная папка
#   BACKUP_KEEP_DAYS        сколько дней хранить дампы в BACKUP_DIR (по умолчанию 14)
# Без аргумента — ежедневный бэкап: имя по времени в BACKUP_DIR, ротация, и отметка
# ops_marks(backup.ok / backup.failed) у аккаунта global — по ней работают алерт «бэкап не
# прошёл» и /health. С аргументом — разовый дамп в указанный файл (например перед обновлением):
# без ротации и без отметки.
set -euo pipefail
umask 077

: "${DATABASE_URL:?DATABASE_URL не задан}"

re='^[a-z0-9+]+://([^:@/]+)(:[^@]*)?@[^/]+/([^?]+)'
if [[ ! "$DATABASE_URL" =~ $re ]]; then
  echo "ошибка: не разобран DATABASE_URL" >&2
  exit 2
fi
DB_USER="${BASH_REMATCH[1]}"
DB_NAME="${BASH_REMATCH[3]}"

if [[ -n "${PG_CONTAINER:-}" ]]; then
  RUN=(docker exec -i "$PG_CONTAINER")
else
  RUN=(docker compose exec -T db)
fi

PASSFILE="${BACKUP_PASSPHRASE_FILE:-}"
if [[ -n "$PASSFILE" && ! -s "$PASSFILE" ]]; then
  echo "ошибка: BACKUP_PASSPHRASE_FILE=$PASSFILE пуст или не найден" >&2
  exit 2
fi

DIR="${BACKUP_DIR:-backups}"
SUFFIX=".dump"
[[ -n "$PASSFILE" ]] && SUFFIX=".dump.enc"
OUT="${1:-$DIR/xcopilot-$(date -u +%Y%m%dT%H%M%SZ)$SUFFIX}"
mkdir -p "$(dirname "$OUT")"

DAILY=0
[[ $# -eq 0 ]] && DAILY=1

mark() { # mark <ключ> <json> — отметка у аккаунта global; без неё бэкап всё равно годен
  [[ "$DAILY" == 1 ]] || return 0
  "${RUN[@]}" psql -X -q -v ON_ERROR_STOP=1 -U "$DB_USER" -d "$DB_NAME" -c \
    "INSERT INTO ops_marks (account_id, key, at, payload)
     SELECT id, '$1', now(), '$2'::jsonb FROM accounts WHERE kind = 'global'
     ON CONFLICT (account_id, key) DO UPDATE SET at = EXCLUDED.at, payload = EXCLUDED.payload" \
    >/dev/null 2>&1 || echo "предупреждение: отметка $1 не записана" >&2
}

on_error() {
  rm -f "$OUT.part"
  mark backup.failed "{\"file\": \"$(basename "$OUT")\"}"
  echo "backup: ОШИБКА, дамп не создан" >&2
}
trap on_error ERR

if [[ -n "$PASSFILE" ]]; then
  "${RUN[@]}" pg_dump -Fc --no-owner --no-privileges -U "$DB_USER" -d "$DB_NAME" \
    | openssl enc -aes-256-cbc -pbkdf2 -salt -pass "file:$PASSFILE" > "$OUT.part"
else
  "${RUN[@]}" pg_dump -Fc --no-owner --no-privileges -U "$DB_USER" -d "$DB_NAME" > "$OUT.part"
fi
mv "$OUT.part" "$OUT"
BYTES="$(wc -c < "$OUT" | tr -d ' ')"

if [[ -n "${BACKUP_REMOTE:-}" ]]; then
  rsync -a "$OUT" "$BACKUP_REMOTE"
fi

if [[ "$DAILY" == 1 ]]; then # ротация только своих дампов в BACKUP_DIR
  find "$DIR" -maxdepth 1 -type f -name 'xcopilot-*.dump*' -mtime "+${BACKUP_KEEP_DAYS:-14}" \
    -delete
fi

trap - ERR
mark backup.ok "{\"file\": \"$(basename "$OUT")\", \"bytes\": $BYTES, \"remote\": $([[ -n "${BACKUP_REMOTE:-}" ]] && echo true || echo false)}"
echo "backup: $OUT ($BYTES байт)" >&2
