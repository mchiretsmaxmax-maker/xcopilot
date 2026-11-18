#!/usr/bin/env bash
# Управление ботом одной командой (для ярлыков на рабочем столе).
#   scripts/ctl.sh start   — Docker Desktop → база → служба бота, ждёт старта
#   scripts/ctl.sh stop    — останавливает бота (база остаётся)
#   scripts/ctl.sh status  — работает ли бот, последние строки лога
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1
export PATH="$HOME/.local/bin:$HOME/.docker/bin:/usr/local/bin:/opt/homebrew/bin:$PATH"
COMPOSE=(docker compose -f compose.yaml -f compose.local.yaml)

say() { printf '%s\n' "$*"; }

bot_pid() { pgrep -f "python -m xcopilot.main" | head -1; }

wait_docker() {
  if docker info >/dev/null 2>&1; then return 0; fi
  say "Запускаю Docker Desktop…"
  open -a Docker
  for _ in $(seq 1 60); do
    docker info >/dev/null 2>&1 && return 0
    sleep 3
  done
  say "❌ Docker не запустился за 3 минуты. Открой Docker Desktop вручную и повтори."
  return 1
}

start() {
  wait_docker || return 1
  say "Поднимаю базу…"
  "${COMPOSE[@]}" up -d db >/dev/null 2>&1 || { say "❌ База не поднялась: ${COMPOSE[*]} up -d db"; return 1; }
  for _ in $(seq 1 30); do
    "${COMPOSE[@]}" exec -T db pg_isready -q >/dev/null 2>&1 && break
    sleep 2
  done
  say "Запускаю бота…"
  "$ROOT/scripts/service.sh" stop >/dev/null 2>&1
  sleep 8
  local from
  from=$(( $(wc -l < "$ROOT/logs/bot.log" 2>/dev/null || echo 0) + 1 ))
  "$ROOT/scripts/service.sh" start >/dev/null 2>&1
  for _ in $(seq 1 45); do
    if [ -n "$(bot_pid)" ] && tail -n +"$from" "$ROOT/logs/bot.log" 2>/dev/null | grep -q '"scheduler_started"'; then
      say "✅ Бот работает (pid $(bot_pid)). Можно закрыть окно."
      return 0
    fi
    sleep 2
  done
  say "⚠️ Бот не подтвердил старт за 90 с. Последние строки лога:"
  tail -n 15 "$ROOT/logs/bot.log"
  return 1
}

stop() {
  "$ROOT/scripts/service.sh" stop >/dev/null 2>&1
  sleep 3
  if [ -z "$(bot_pid)" ]; then
    say "⏹ Бот остановлен."
  else
    say "⚠️ Процесс бота ещё жив (pid $(bot_pid)) — повтори через пару секунд."
  fi
  say "Не забудь поставить проверку на паузу в healthchecks.io, иначе придёт оповещение."
}

status() {
  if docker info >/dev/null 2>&1; then say "Docker: ✅"; else say "Docker: ❌ не запущен"; fi
  if [ -n "$(bot_pid)" ]; then say "Бот: ✅ работает (pid $(bot_pid))"; else say "Бот: ❌ не запущен"; fi
  say ""
  say "Последние строки лога:"
  tail -n 8 "$ROOT/logs/bot.log" 2>/dev/null | cut -c1-160
}

case "${1:-status}" in
  start) start ;;
  stop) stop ;;
  status) status ;;
  *) say "usage: $0 start|stop|status"; exit 2 ;;
esac
