#!/usr/bin/env bash
# Локальный запуск бота на этом компьютере с автоперезапуском (fix/bot-control).
#
#   scripts/run_local.sh            — крутить бота в этом окне (остановка: Ctrl+C)
#   scripts/run_local.sh --daemon   — то же в фоне (nohup), pid в logs/run_local.pid
#   scripts/run_local.sh --stop     — остановить фоновый запуск корректно (SIGTERM)
#   scripts/run_local.sh --status   — работает ли фоновый запуск
#
# Что делает: подгружает .env, ставит DATABASE_URL на 127.0.0.1:5433 (БД из Docker с
# compose.local.yaml), применяет миграции и запускает `uv run python -m xcopilot.main`.
# Код выхода 3 («Перезапустить бота» в меню) и любое падение — запуск заново с паузой;
# частые падения подряд — длинная пауза. Обычная остановка (Ctrl+C, SIGTERM, код 0) — выход.
# Логи: logs/bot.log. Настройки: RUN_LOCAL_PAUSE (сек, обе паузы), RUN_LOCAL_DB_PORT (5433).
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1
LOG_DIR="$ROOT/logs"
LOG="$LOG_DIR/bot.log"
PID_FILE="$LOG_DIR/run_local.pid"
mkdir -p "$LOG_DIR"

PAUSE_RESTART="${RUN_LOCAL_PAUSE:-3}"   # после «Перезапустить» (код 3)
PAUSE_CRASH="${RUN_LOCAL_PAUSE:-10}"    # после падения
COOLDOWN="${RUN_LOCAL_COOLDOWN:-60}"    # если упал CRASH_LIMIT раз за CRASH_WINDOW секунд
CRASH_LIMIT=5
CRASH_WINDOW=120
RESTART_CODE=3

say() { printf '[run_local %s] %s\n' "$(date '+%d.%m %H:%M:%S')" "$*"; }
# в окне: на экран и в лог; в фоне (--daemon) stdout уже в логе
note() { say "$@"; if [ -z "${RUN_LOCAL_QUIET:-}" ]; then say "$@" >>"$LOG"; fi; }

alive() { [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null; }
daemon_pid() { [ -f "$PID_FILE" ] && cat "$PID_FILE" 2>/dev/null; }

case "${1:-}" in
  --daemon)
    if "${XC_SERVICE_SH:-$ROOT/scripts/service.sh}" running >/dev/null 2>&1; then
      say "служба автозапуска уже запущена — второй экземпляр не стартую (два опроса Telegram); остановить: scripts/service.sh stop"
      exit 0
    fi
    pid="$(daemon_pid)"
    if alive "$pid"; then say "уже работает в фоне (pid $pid); остановить: scripts/run_local.sh --stop"; exit 0; fi
    RUN_LOCAL_QUIET=1 nohup "${BASH_SOURCE[0]}" >>"$LOG" 2>&1 &
    echo $! >"$PID_FILE"
    say "запущен в фоне (pid $!); логи: logs/bot.log; остановить: scripts/run_local.sh --stop"
    exit 0
    ;;
  --stop)
    pid="$(daemon_pid)"
    if ! alive "$pid"; then say "в фоне не запущен"; rm -f "$PID_FILE"; exit 0; fi
    kill -TERM "$pid"
    for _ in $(seq 1 60); do alive "$pid" || break; sleep 0.5; done
    if alive "$pid"; then say "pid $pid ещё останавливается (ждёт завершения задач); проверь logs/bot.log"; exit 1; fi
    rm -f "$PID_FILE"
    say "остановлен"
    exit 0
    ;;
  --status)
    pid="$(daemon_pid)"
    if alive "$pid"; then say "работает в фоне (pid $pid)"; else say "в фоне не запущен"; fi
    exit 0
    ;;
  "") ;;
  *) echo "использование: $0 [--daemon|--stop|--status]" >&2; exit 2 ;;
esac

if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/.env"
  set +a
fi
# БД в Docker, порт опубликован на хост (compose.local.yaml): в .env хост `db` — только для compose.
DB_URL="${DATABASE_URL:-postgresql+asyncpg://xcopilot:xcopilot@db:5432/xcopilot}"
export DATABASE_URL
DATABASE_URL="$(printf '%s' "$DB_URL" | sed -E "s#@[^/@]*/#@127.0.0.1:${RUN_LOCAL_DB_PORT:-5433}/#")"

STOP=0
CHILD=""
on_signal() {
  STOP=1
  if alive "$CHILD"; then kill -TERM "$CHILD" 2>/dev/null; fi
}
trap on_signal INT TERM
[ -n "${RUN_LOCAL_QUIET:-}" ] && echo $$ >"$PID_FILE"

crashes=()   # времена последних падений (секунды)
note "старт; БД ${DATABASE_URL##*@}; лог: logs/bot.log"

run_logged() {   # команда с выводом в лог (и на экран, если не --daemon)
  if [ -n "${RUN_LOCAL_QUIET:-}" ]; then "$@" >>"$LOG" 2>&1; else "$@" > >(tee -a "$LOG") 2>&1; fi
}

while true; do
  if run_logged uv run python -m xcopilot.db.migrate; then
    # сам процесс бота (не подоболочка): сигнал остановки доходит прямо до него
    if [ -n "${RUN_LOCAL_QUIET:-}" ]; then
      uv run python -m xcopilot.main >>"$LOG" 2>&1 &
    else
      uv run python -m xcopilot.main > >(tee -a "$LOG") 2>&1 &
    fi
    CHILD=$!
    wait "$CHILD"; code=$?
    while alive "$CHILD"; do wait "$CHILD"; code=$?; done   # сигнал прерывает wait, процесс ещё завершается
    CHILD=""
  else
    code=$?
    note "миграции не прошли (код $code): БД запущена? docker compose -f compose.yaml -f compose.local.yaml up -d db"
  fi

  if [ "$STOP" -eq 1 ]; then note "остановлен (код $code)"; exit 0; fi
  case "$code" in
    0) note "бот остановился штатно (код 0) — выход"; exit 0 ;;
    "$RESTART_CODE")
      note "перезапуск по просьбе владельца (код 3) — через ${PAUSE_RESTART} с"
      sleep "$PAUSE_RESTART" ;;
    *)
      now=$(date +%s)
      fresh=()
      for t in ${crashes[@]+"${crashes[@]}"}; do [ $((now - t)) -le "$CRASH_WINDOW" ] && fresh+=("$t"); done
      crashes=(${fresh[@]+"${fresh[@]}"} "$now")
      if [ "${#crashes[@]}" -ge "$CRASH_LIMIT" ]; then
        note "падает слишком часто (код $code, ${#crashes[@]} раз за ${CRASH_WINDOW} с) — пауза ${COOLDOWN} с"
        sleep "$COOLDOWN"; crashes=()
      else
        note "бот упал (код $code) — запуск заново через ${PAUSE_CRASH} с"
        sleep "$PAUSE_CRASH"
      fi ;;
  esac
  [ "$STOP" -eq 1 ] && exit 0
done
