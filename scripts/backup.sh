#!/usr/bin/env bash
# Бэкап БД xcopilot в custom-формат pg_dump.
# Запуск: scripts/backup.sh [файл]
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

OUT="${1:-backups/xcopilot-$(date -u +%Y%m%dT%H%M%SZ).dump}"
mkdir -p "$(dirname "$OUT")"

trap 'rm -f "$OUT.part"' ERR
"${RUN[@]}" pg_dump -Fc --no-owner --no-privileges -U "$DB_USER" -d "$DB_NAME" > "$OUT.part"
mv "$OUT.part" "$OUT"

echo "backup: $OUT ($(wc -c < "$OUT" | tr -d ' ') байт)" >&2
