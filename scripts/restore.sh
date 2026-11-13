#!/usr/bin/env bash
# Восстановление БД xcopilot из custom-дампа pg_dump.
# Запуск: scripts/restore.sh файл
# Переменные: DATABASE_URL (обязательна), PG_CONTAINER (опционально).
set -euo pipefail

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

IN="${1:?укажи файл дампа}"
if [[ ! -s "$IN" ]]; then
  echo "ошибка: файл $IN пуст или не найден" >&2
  exit 2
fi

if [[ -t 0 ]]; then
  read -r -p "Восстановить в БД $DB_NAME? Текущие данные будут заменены. Введи yes: " answer
  if [[ "$answer" != "yes" ]]; then
    echo "отменено" >&2
    exit 1
  fi
fi

# --clean не используется: партиции наследуют ограничения, и pg_restore падает на их удалении.
# Всё идёт одной транзакцией (psql -1), при ошибке откат, старые данные целы.
"${RUN[@]}" sh -c '
  {
    echo "DROP SCHEMA IF EXISTS public CASCADE;"
    echo "CREATE SCHEMA public AUTHORIZATION pg_database_owner;"
    echo "GRANT USAGE ON SCHEMA public TO PUBLIC;"
    pg_restore --no-owner --no-privileges -f -
  } | psql -X -q -1 -v ON_ERROR_STOP=1 -U "$1" -d "$2" >/dev/null
' restore "$DB_USER" "$DB_NAME" < "$IN"

echo "restore: $IN → $DB_NAME" >&2
