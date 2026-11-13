#!/usr/bin/env bash
# Простой дашборд xcopilot в терминале.
#   scripts/status.sh          — один раз
#   scripts/status.sh --watch  — обновлять каждые 15 сек (выход: Ctrl+C)
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

B=$'\e[1m'; G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; D=$'\e[2m'; N=$'\e[0m'

show() {
  cd "$ROOT" || exit 1
  echo "${B}xcopilot — $(date '+%d.%m %H:%M')${N}"
  echo

  echo "${B}main${N}"
  git log -3 --format="  %h %s ${D}(%cr)${N}" main
  echo

  echo "${B}Ветки в работе${N}"
  local any=0
  while read -r path _ ref; do
    br="${ref#[}"; br="${br%]}"
    [ "$path" = "$ROOT" ] && continue
    any=1
    ahead=$(git rev-list --count "main..$br" 2>/dev/null || echo "?")
    last=$(git -C "$path" log -1 --format="%s ${D}(%cr)${N}" 2>/dev/null)
    dirty=$(git -C "$path" status --porcelain 2>/dev/null | wc -l | tr -d ' ')
    stage=$(echo "$br" | sed -n 's#^stage/\([0-9]*\)-.*#\1#p')
    tdd=""
    if [ -n "$stage" ]; then
      if git rev-parse -q --verify "refs/tags/stage-$stage-red" >/dev/null; then
        tdd="${G}тесты написаны${N}"
      else
        tdd="${Y}тесты ещё не написаны${N}"
      fi
    fi
    echo "  ${B}$br${N}  +$ahead коммитов к main  $tdd"
    echo "    последний: $last"
    [ "$dirty" != "0" ] && echo "    ${Y}несохранённых файлов: $dirty${N}"
    if [ -f "$path/.ds/RESULT.md" ]; then
      echo "    DeepSeek: $(head -1 "$path/.ds/RESULT.md")"
    fi
  done < <(git worktree list)
  [ $any = 0 ] && echo "  ${D}нет${N}"
  echo

  echo "${B}Очередь этапов${N} ${D}(docs/README.md)${N}"
  awk -F'|' '/^## /{q=($0 ~ /Очередь этапов/)} q && /^\| [0-9]/ {gsub(/^ +| +$/,"",$2); gsub(/^ +| +$/,"",$3); gsub(/^ +| +$/,"",$5);
      c=$5; if (c ~ /готов/) col="\033[32m"; else if (c ~ /работ|ждёт/) col="\033[33m"; else col="\033[2m";
      printf "  %-8s %-24s %s%s\033[0m\n", $2, $3, col, c}' docs/README.md
  echo

  echo "${B}Docker${N}"
  if docker info >/dev/null 2>&1; then
    out=$(docker ps --filter name=xcopilot --format '  {{.Names}}  {{.Status}}' 2>/dev/null)
    echo "${out:-  ${D}контейнеров xcopilot нет${N}}"
  else
    echo "  ${R}Docker не запущен${N}"
  fi
}

if [ "${1:-}" = "--watch" ]; then
  while true; do clear; show; echo; echo "${D}обновление каждые 15 сек, Ctrl+C — выход${N}"; sleep 15; done
else
  show
fi
