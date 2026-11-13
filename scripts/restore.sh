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

"${RUN[@]}" pg_restore --no-owner --no-privileges --clean --if-exists --exit-on-error --single-transaction -U "$DB_USER" -d "$DB_NAME" < "$IN"

echo "restore: $IN → $DB_NAME" >&2
