#!/usr/bin/env bash
# Подготовка устройства к запуску бота (macOS и Linux). Идемпотентно, ничего не скачивает.
#   scripts/setup.sh           — подготовить всё и установить службу автозапуска
#   scripts/setup.sh --check   — только проверить (ничего не менять), код 1 если чего-то нет
# Единственный ручной шаг — `codex login` (подписка этого устройства).
# XC_ENV_FILE — путь к .env (по умолчанию <корень>/.env).
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${XC_ENV_FILE:-$ROOT/.env}"
CHECK=0
case "${1:-}" in --check) CHECK=1 ;; "") ;; *) echo "использование: $0 [--check]" >&2; exit 2 ;; esac

FAILED=0
ok()  { echo "✓ $*"; }
bad() { echo "✗ $*"; FAILED=1; }

have() { # have <команда> <имя в сообщении> <что поставить>
  if command -v "$1" >/dev/null 2>&1; then ok "$2"; else bad "нет $2: $3"; fi
}
have docker "Docker" "поставь Docker Desktop (macOS) или docker engine + compose (Linux)"
have uv "uv" "https://docs.astral.sh/uv/"
have openssl "openssl" "поставь openssl"
# --check допускает полный путь через окружение. Обычный запуск читает его из .env.
if [ "$CHECK" -eq 1 ] || [ ! -f "$ENV_FILE" ]; then
  have "${CODEX_CLI_PATH:-codex}" "Codex CLI" "установи Codex CLI и выполни codex login"
fi
if [ "$FAILED" -ne 0 ]; then echo "Инструменты не готовы — поставь недостающее и запусти ещё раз."; exit 1; fi

if [ -f "$ENV_FILE" ]; then
  mode="$(stat -c %a "$ENV_FILE" 2>/dev/null || stat -f %Lp "$ENV_FILE")"
  if [ "$CHECK" -eq 1 ]; then
    case "$mode" in 600|400) ok ".env есть, права $mode" ;; *) bad ".env: права $mode, нужны 600 (chmod 600 .env)" ;; esac
  else
    chmod 600 "$ENV_FILE"; ok ".env есть, права 600"
  fi
else
  bad "нет .env: скопируй .env.example → .env и заполни (или перенеси пакетом: make import FILE=…)"
fi
[ "$FAILED" -eq 0 ] || exit 1

COMPOSE=(docker compose -f compose.yaml -f compose.local.yaml)
if [ "$CHECK" -eq 1 ]; then
  if (cd "$ROOT" && "${COMPOSE[@]}" ps --status running db 2>/dev/null | grep -q db); then ok "БД запущена"; else bad "БД не запущена (scripts/setup.sh поднимет)"; fi
  [ "$FAILED" -eq 0 ] && echo "Проверка пройдена." || echo "Есть замечания (см. ✗)."
  exit "$FAILED"
fi

cd "$ROOT" || exit 1
set -a; . "$ENV_FILE"; set +a
have "${CODEX_CLI_PATH:-codex}" "Codex CLI" "установи Codex CLI и выполни codex login"
[ "$FAILED" -eq 0 ] || exit 1
step() { "$@" || { bad "не удалось: $*"; exit 1; }; }
step uv sync --frozen && ok "зависимости (uv sync)"
step "${COMPOSE[@]}" up -d --wait db && ok "БД поднята (127.0.0.1:${RUN_LOCAL_DB_PORT:-5433})"
DATABASE_URL="$(printf '%s' "${DATABASE_URL:-postgresql+asyncpg://xcopilot:xcopilot@db:5432/xcopilot}" \
  | sed -E "s#@[^/@]*/#@127.0.0.1:${RUN_LOCAL_DB_PORT:-5433}/#")"
export DATABASE_URL
step uv run python -m xcopilot.db.migrate && ok "миграции применены"

step uv run python -m xcopilot.llm ping writer && ok "Codex CLI работает через ChatGPT"

step "$ROOT/scripts/service.sh" install && ok "служба автозапуска установлена"
echo "Готово. Проверь в боте: /health"
