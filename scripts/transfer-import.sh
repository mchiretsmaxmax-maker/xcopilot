#!/usr/bin/env bash
# Развернуть пакет переноса на новом устройстве (репозиторий уже склонирован).
#   scripts/transfer-import.sh <пакет.xcbundle> [--force]
# Пароль: XC_TRANSFER_PASSPHRASE_FILE или запрос. Коды: 2 — отказ (пароль, непустая БД,
# схема новее кода). --force — заменить данные в непустой БД.
# XC_SKIP_SETUP=1 — не запускать setup.sh; XC_ENV_FILE, XC_DATA_DIR — куда класть .env и data/.
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
BUNDLE="${1:?укажи пакет .xcbundle}"
FORCE=0; [ "${2:-}" = "--force" ] && FORCE=1
ENV_FILE="${XC_ENV_FILE:-$ROOT/.env}"
DATA_DIR="${XC_DATA_DIR:-$ROOT/data}"
[ -s "$BUNDLE" ] || { echo "ошибка: нет файла $BUNDLE" >&2; exit 2; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
PASSFILE="${XC_TRANSFER_PASSPHRASE_FILE:-}"
if [ -z "$PASSFILE" ]; then
  [ -t 0 ] || { echo "ошибка: задай XC_TRANSFER_PASSPHRASE_FILE или запусти в терминале" >&2; exit 2; }
  read -r -s -p "Пароль пакета: " pw; echo
  printf '%s\n' "$pw" >"$TMP/pass"; PASSFILE="$TMP/pass"
fi

# пароль проверяется до любых изменений
if ! openssl enc -d -aes-256-cbc -pbkdf2 -pass "file:$PASSFILE" -in "$BUNDLE" -out "$TMP/bundle.tar" 2>/dev/null \
  || ! tar -tf "$TMP/bundle.tar" >/dev/null 2>&1; then
  echo "ошибка: не расшифровать $BUNDLE — неверный пароль?" >&2; exit 2
fi
mkdir "$TMP/b"; tar -C "$TMP/b" -xf "$TMP/bundle.tar"

if [ -z "${DATABASE_URL:-}" ]; then
  set -a; . "$TMP/b/env"; set +a
  DATABASE_URL="$(printf '%s' "${DATABASE_URL:-postgresql+asyncpg://xcopilot:xcopilot@db:5432/xcopilot}" \
    | sed -E "s#@[^/@]*/#@127.0.0.1:${RUN_LOCAL_DB_PORT:-5433}/#")"
  export DATABASE_URL
fi
re='^[a-z0-9+]+://([^:@/]+)(:[^@]*)?@[^/]+/([^?]+)'
[[ "$DATABASE_URL" =~ $re ]] || { echo "ошибка: не разобран DATABASE_URL" >&2; exit 2; }
DB_USER="${BASH_REMATCH[1]}"; DB_NAME="${BASH_REMATCH[3]}"

if [ -z "${PG_CONTAINER:-}" ]; then
  docker compose -f compose.yaml -f compose.local.yaml up -d --wait db
  RUN=(docker compose exec -T db)
else
  RUN=(docker exec -i "$PG_CONTAINER")
fi
psql_() { "${RUN[@]}" psql -X -tA -U "$DB_USER" -d "$DB_NAME" -c "$1"; }

# схема дампа новее кода?
SCHEMA="$(sed -n 's/.*"schema": "\([^"]*\)".*/\1/p' "$TMP/b/manifest.json")"
if [ -n "$SCHEMA" ] && ! grep -rqE "^revision.*[\"']$SCHEMA[\"']" "$ROOT/src/xcopilot/db/migrations/versions"; then
  echo "ошибка: схема дампа ($SCHEMA) новее кода — сначала git pull" >&2; exit 2
fi

N="$(psql_ "SELECT count(*) FROM accounts WHERE kind <> 'global'" 2>/dev/null || echo 0)"
if [ "${N:-0}" != "0" ] && [ "$FORCE" -ne 1 ]; then
  echo "ошибка: в БД уже есть рабочие аккаунты ($N); данные будут заменены — повтори с --force" >&2; exit 2
fi

[ -f "$ENV_FILE" ] && cp -p "$ENV_FILE" "$ENV_FILE.before-import"
mkdir -p "$(dirname "$ENV_FILE")"
cp "$TMP/b/env" "$ENV_FILE"; chmod 600 "$ENV_FILE"
mkdir -p "$DATA_DIR"; cp -R "$TMP/b/data/." "$DATA_DIR/"

BACKUP_PASSPHRASE_FILE="" "$ROOT/scripts/restore.sh" "$TMP/b/db.dump" </dev/null
uv run python -m xcopilot.db.migrate
echo "перенос выполнен"
if [ -z "${XC_SKIP_SETUP:-}" ]; then "$ROOT/scripts/setup.sh"; fi
