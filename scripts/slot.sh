#!/usr/bin/env bash
# Постоянные рабочие папки («слоты») для веток: .worktrees/slot1..slot3.
# Путь слота не меняется, поэтому приложение спрашивает доверие к нему
# только один раз за всё время. Ветка в слоте меняется.
#
#   scripts/slot.sh <номер> <ветка>   — посадить новую ветку (от main) в слот
#   scripts/slot.sh list              — что сейчас в слотах
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ "${1:-}" = "list" ]; then
  for d in .worktrees/slot*; do
    [ -d "$d" ] || continue
    echo "$d  $(git -C "$d" branch --show-current || echo '(без ветки)')"
  done
  exit 0
fi

n="${1:?номер слота}"; br="${2:?имя ветки}"
dir=".worktrees/slot$n"

if [ ! -d "$dir" ]; then
  git worktree add -q -b "$br" "$dir" main
else
  if [ -n "$(git -C "$dir" status --porcelain)" ]; then
    echo "В $dir есть несохранённые изменения — слот занят" >&2; exit 1
  fi
  git -C "$dir" switch -q -c "$br" main
fi
cp -p .env "$dir/.env"
echo "$dir → $br"
