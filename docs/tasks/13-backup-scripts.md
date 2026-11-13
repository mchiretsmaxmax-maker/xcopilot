## Файлы
- scripts/backup.sh
- scripts/restore.sh

## Новые
- (нет)

## Контекст
- compose.yaml
- tests/integration/db/test_backup.py
- scripts/check_tdd.sh

## Задача
Два bash-скрипта (`#!/usr/bin/env bash`, `set -euo pipefail`, комментарии по-русски). pg_dump и pg_restore всегда запускаются ВНУТРИ контейнера БД (версия клиента = версия сервера 18), через unix-сокет (без `-h`, без пароля).

Общая часть (одинаковая в обоих скриптах, функцией не выносить в отдельный файл):
1. `DATABASE_URL` обязателен: `: "${DATABASE_URL:?DATABASE_URL не задан}"`.
2. Разбор URL вида `postgresql+asyncpg://user:pass@host:port/db?params` bash-регуляркой `re='^[a-z0-9+]+://([^:@/]+)(:[^@]*)?@[^/]+/([^?]+)'`; при несовпадении — `echo "ошибка: не разобран DATABASE_URL" >&2; exit 2`. `DB_USER="${BASH_REMATCH[1]}"`, `DB_NAME="${BASH_REMATCH[3]}"`. Пароль не использовать и нигде не печатать; сам URL не печатать.
3. Исполнитель команд: если задан `PG_CONTAINER` — `RUN=(docker exec -i "$PG_CONTAINER")`, иначе `RUN=(docker compose exec -T db)`.

`scripts/backup.sh [файл]`:
4. `OUT="${1:-backups/xcopilot-$(date -u +%Y%m%dT%H%M%SZ).dump}"`; `mkdir -p "$(dirname "$OUT")"`.
5. `"${RUN[@]}" pg_dump -Fc --no-owner --no-privileges -U "$DB_USER" -d "$DB_NAME" > "$OUT.part"`, затем `mv "$OUT.part" "$OUT"` (при ошибке pg_dump частичный файл удалить: `trap 'rm -f "$OUT.part"' ERR`).
6. В stderr: `backup: $OUT ($(wc -c < "$OUT" | tr -d ' ') байт)`.

`scripts/restore.sh файл`:
7. `IN="${1:?укажи файл дампа}"`; если `[[ ! -s "$IN" ]]` — `echo "ошибка: файл $IN пуст или не найден" >&2; exit 2`.
8. Если stdin — терминал (`[[ -t 0 ]]`), спросить `read -r -p "Восстановить в БД $DB_NAME? Текущие данные будут заменены. Введи yes: " answer` и при ответе не `yes` — `echo "отменено" >&2; exit 1`.
9. `"${RUN[@]}" pg_restore --no-owner --no-privileges --clean --if-exists --exit-on-error --single-transaction -U "$DB_USER" -d "$DB_NAME" < "$IN"`.
10. В stderr: `restore: $IN → $DB_NAME`.

Шапка каждого скрипта — 3–4 строки комментария: назначение, запуск, переменные `DATABASE_URL` и `PG_CONTAINER`. Файлы сейчас — заглушки с правами на исполнение; замени их содержимое целиком.
Должен пройти `test_backup_restore_roundtrip`.

## Коммит
feat: scripts/backup.sh и restore.sh (pg_dump -Fc внутри контейнера БД)
