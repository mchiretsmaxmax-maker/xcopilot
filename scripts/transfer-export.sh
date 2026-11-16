#!/usr/bin/env bash
# Пакет переноса на другое устройство: дамп БД + .env + data/ → один зашифрованный файл.
#   scripts/transfer-export.sh [файл.xcbundle]
# Пароль: XC_TRANSFER_PASSPHRASE_FILE или запрос в терминале. Бот на этом устройстве
# останавливается (XC_SKIP_SERVICE=1 — не трогать службу). XC_ENV_FILE, XC_DATA_DIR — пути.
# Нужны DATABASE_URL (и PG_CONTAINER, если БД не через compose) — как для backup.sh.
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
ENV_FILE="${XC_ENV_FILE:-$ROOT/.env}"
DATA_DIR="${XC_DATA_DIR:-$ROOT/data}"
OUT="${1:-xcopilot-$(date -u +%Y%m%d).xcbundle}"

[ -f "$ENV_FILE" ] || { echo "ошибка: нет $ENV_FILE" >&2; exit 2; }
if [ -z "${DATABASE_URL:-}" ]; then
  set -a; . "$ENV_FILE"; set +a
  DATABASE_URL="$(printf '%s' "${DATABASE_URL:-}" | sed -E "s#@[^/@]*/#@127.0.0.1:${RUN_LOCAL_DB_PORT:-5433}/#")"
  export DATABASE_URL
fi

PASSFILE="${XC_TRANSFER_PASSPHRASE_FILE:-}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
if [ -z "$PASSFILE" ]; then
  [ -t 0 ] || { echo "ошибка: задай XC_TRANSFER_PASSPHRASE_FILE или запусти в терминале" >&2; exit 2; }
  read -r -s -p "Пароль пакета: " pw; echo
  read -r -s -p "Ещё раз: " pw2; echo
  [ "$pw" = "$pw2" ] || { echo "пароли не совпали" >&2; exit 2; }
  printf '%s\n' "$pw" >"$TMP/pass"; PASSFILE="$TMP/pass"
fi
[ -s "$PASSFILE" ] || { echo "ошибка: файл пароля пуст" >&2; exit 2; }

if [ -z "${XC_SKIP_SERVICE:-}" ]; then
  # два устройства с одним токеном Telegram мешают друг другу — сначала остановка
  "$ROOT/scripts/service.sh" stop || true
fi

mkdir -p "$TMP/b"
"$ROOT/scripts/backup.sh" "$TMP/b/db.dump"
cp "$ENV_FILE" "$TMP/b/env"
if [ -d "$DATA_DIR" ]; then cp -R "$DATA_DIR" "$TMP/b/data"; else mkdir "$TMP/b/data"; fi

re='^[a-z0-9+]+://([^:@/]+)(:[^@]*)?@[^/]+/([^?]+)'
[[ "$DATABASE_URL" =~ $re ]]
if [ -n "${PG_CONTAINER:-}" ]; then RUN=(docker exec -i "$PG_CONTAINER"); else RUN=(docker compose exec -T db); fi
SCHEMA="$("${RUN[@]}" psql -X -tA -U "${BASH_REMATCH[1]}" -d "${BASH_REMATCH[3]}" -c 'SELECT version_num FROM alembic_version' 2>/dev/null | tr -d '[:space:]' || true)"
printf '{"schema": "%s", "commit": "%s", "date": "%s", "host": "%s"}\n' \
  "$SCHEMA" "$(git rev-parse --short HEAD 2>/dev/null || echo unknown)" \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(hostname)" >"$TMP/b/manifest.json"

tar -C "$TMP/b" -cf "$TMP/bundle.tar" manifest.json db.dump env data
openssl enc -aes-256-cbc -pbkdf2 -salt -pass "file:$PASSFILE" -in "$TMP/bundle.tar" -out "$OUT.part"
mv "$OUT.part" "$OUT"
chmod 600 "$OUT"
echo "пакет: $OUT"
echo "Скопируй файл на новое устройство. Бот здесь остановлен; запустить снова — scripts/service.sh start"
