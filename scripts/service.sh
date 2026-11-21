#!/usr/bin/env bash
# Служба автозапуска бота: launchd (macOS) или systemd --user (Linux).
#   scripts/service.sh install|start|stop|status|logs|uninstall
# Служба запускает scripts/run_local.sh (без --daemon): он грузит .env, мигрирует,
# перезапускает по коду 3. XC_SERVICE_DRY=1 — только показать, что будет сделано.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OS="$(uname -s)"
LOG_DIR="${XC_LOG_DIR:-$HOME/Library/Logs/xcopilot}"
[ "$OS" = Linux ] && LOG_DIR="${XC_LOG_DIR:-$HOME/.local/state/xcopilot}"
PLIST="$HOME/Library/LaunchAgents/com.xcopilot.bot.plist"
UNIT="$HOME/.config/systemd/user/xcopilot.service"
UID_N="$(id -u)"

render() { # render <шаблон> <файл>
  local dirs="" tool d
  for tool in uv codex docker; do
    if p="$(command -v "$tool" 2>/dev/null)"; then d="$(dirname "$p")"; case ":$dirs:" in *":$d:"*) ;; *) dirs="$dirs$d:" ;; esac; fi
  done
  dirs="${dirs}/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
  mkdir -p "$LOG_DIR" "$(dirname "$2")"
  sed -e "s#{{WORKDIR}}#$ROOT#g" -e "s#{{PATH_DIRS}}#$dirs#g" -e "s#{{LOG_DIR}}#$LOG_DIR#g" "$1" >"$2"
}

is_running() {
  if [ "$OS" = Darwin ]; then
    launchctl print "gui/$UID_N/com.xcopilot.bot" 2>/dev/null | grep -qE "state = running"
  else
    systemctl --user is-active --quiet xcopilot.service 2>/dev/null
  fi
}
stop_daemon() { # фоновый run_local.sh --daemon и служба не должны работать вместе (один токен Telegram)
  "$ROOT/scripts/run_local.sh" --status 2>/dev/null | grep -q "работает в фоне" || return 0
  echo "останавливаю фоновый run_local.sh --daemon (иначе два опроса Telegram)"
  "$ROOT/scripts/run_local.sh" --stop
}

cmd="${1:-}"
case "$cmd" in install|start) stop_daemon ;; esac
case "$OS:$cmd" in
  *:running) is_running ;;
  Darwin:install) render "$ROOT/scripts/templates/com.xcopilot.bot.plist" "$PLIST"
    launchctl bootout "gui/$UID_N/com.xcopilot.bot" 2>/dev/null || true
    launchctl bootstrap "gui/$UID_N" "$PLIST"; echo "служба установлена и запущена: $PLIST" ;;
  Darwin:start) launchctl bootstrap "gui/$UID_N" "$PLIST" 2>/dev/null || launchctl kickstart "gui/$UID_N/com.xcopilot.bot" ;;
  Darwin:stop) launchctl bootout "gui/$UID_N/com.xcopilot.bot" 2>/dev/null || true
    "$ROOT/scripts/run_local.sh" --stop || true ;;
  Darwin:status) launchctl print "gui/$UID_N/com.xcopilot.bot" 2>/dev/null | grep -E "state|pid" || echo "служба не загружена" ;;
  Darwin:uninstall) launchctl bootout "gui/$UID_N/com.xcopilot.bot" 2>/dev/null || true; rm -f "$PLIST"; echo "служба удалена" ;;
  Linux:install) render "$ROOT/scripts/templates/xcopilot.service" "$UNIT"
    systemctl --user daemon-reload; systemctl --user enable --now xcopilot.service
    echo "служба установлена; чтобы работала без входа в систему: loginctl enable-linger $USER" ;;
  Linux:start) systemctl --user start xcopilot.service ;;
  Linux:stop) systemctl --user stop xcopilot.service || true; "$ROOT/scripts/run_local.sh" --stop || true ;;
  Linux:status) systemctl --user status xcopilot.service --no-pager || true ;;
  Linux:uninstall) systemctl --user disable --now xcopilot.service 2>/dev/null || true; rm -f "$UNIT"
    systemctl --user daemon-reload; echo "служба удалена" ;;
  *:logs) tail -n 100 -f "$ROOT/logs/bot.log" ;;
  *) echo "использование: $0 install|start|stop|status|logs|uninstall" >&2; exit 2 ;;
esac
