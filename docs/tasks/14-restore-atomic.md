## Файлы
- scripts/restore.sh

## Новые
- (нет)

## Контекст
- tests/integration/db/test_backup.py

## Задача
Проблема: `pg_restore --clean` поверх существующей БД падает на партициях:
`cannot drop inherited constraint "x_api_calls_p202701_pkey" of relation "x_api_calls_p202701"`.
Нужно: восстановление одной транзакцией через psql — сначала пересоздать схему `public`, затем SQL из дампа; при любой ошибке откат, старые данные целы.

1. Меняй только последний вызов `"${RUN[@]}" pg_restore ... < "$IN"`. Шапку, разбор URL, `RUN`, проверку файла, вопрос `yes` и итоговое сообщение в stderr оставь как есть.
2. Новый вызов (ровно так, одной командой; внутри контейнера `sh`, пользователь и БД передаются позиционными аргументами `$1` и `$2`):
```bash
"${RUN[@]}" sh -c '
  {
    echo "DROP SCHEMA IF EXISTS public CASCADE;"
    echo "CREATE SCHEMA public AUTHORIZATION pg_database_owner;"
    echo "GRANT USAGE ON SCHEMA public TO PUBLIC;"
    pg_restore --no-owner --no-privileges -f -
  } | psql -X -q -1 -v ON_ERROR_STOP=1 -U "$1" -d "$2" >/dev/null
' restore "$DB_USER" "$DB_NAME" < "$IN"
```
3. Над вызовом — комментарий из двух строк: почему не `--clean` (партиции наследуют ограничения) и что всё идёт одной транзакцией (`psql -1`), при ошибке откат.
4. Должны пройти все три теста `tests/integration/db/test_backup.py`.

## Коммит
fix: restore.sh — одна транзакция через psql вместо pg_restore --clean
