## Файлы
- (нет)

## Новые
- src/xcopilot/metrics/definitions/posts_published_day.sql
- src/xcopilot/metrics/definitions/followers_delta_day.sql
- src/xcopilot/metrics/definitions/lessons_accepted_week.sql

## Контекст
- src/xcopilot/metrics/definitions/zero_edit_rate.sql
- src/xcopilot/metrics/catalog.py
- tests/integration/metrics/test_formulas.py

## Задача
Создай три SQL-файла метрик. Формат файла-метрики — как у образцов из контекста: шапка из строк `-- ключ: значение` (key = имя файла без .sql; title; description; на одной строке `-- unit: … · grain: … · version: 1`; при измерениях `-- dims: имя`), затем ОДИН запрос SELECT/WITH с колонками period_start (date), dims (jsonb), value (numeric или NULL), sample_size (int), в конце `;`. Параметры запроса: `:account_id`, `:tz` (пояс аккаунта, text), `:from` и `:to` (timestamptz, полуинтервал [from, to)), `:day_from` и `:day_to` (date, `:day_to` не включён). День события — `(ts AT TIME ZONE :tz)::date`. Параметр `:from` рядом с `::` не ставить (пиши `CAST(:from AS timestamptz)`). Всегда фильтруй `account_id = :account_id`. Не меняй никакие другие файлы. ВАЖНО: часть тестов этапа красная по независимым от тебя причинам (других метрик и docs.py пока нет) — не пытайся их чинить; следи за ruff/mypy и за тем, чтобы твои файлы парсились (`load_definitions`) и тесты на твои метрики из контекста проходили.

1. `posts_published_day` (unit: count, grain: day, title «Опубликовано постов»): по событиям `type = 'publication.confirmed'`, value = count(*)::numeric, sample_size = count(*), группировка по дню.
2. `followers_delta_day` (unit: count, grain: day, title «Прирост подписчиков за день»): из таблицы `account_metric_snapshots` (колонки account_id, date, followers). Для каждой строки s с `s.date >= :day_from AND s.date < :day_to` соединить с предыдущим днём p (`p.account_id = s.account_id AND p.date = s.date - 1`); period_start = s.date; value = (s.followers - p.followers)::numeric; sample_size = 1; обе followers не NULL. Это единственная метрика, где вместо ts используются даты.
3. `lessons_accepted_week` (unit: count, grain: week, title «Принято уроков за неделю»): по событиям `type = 'lesson.accepted'`; period_start = `date_trunc('week', e.ts AT TIME ZONE :tz)::date` (понедельник), value = count(*)::numeric, sample_size = count(*), группировка по неделе. Границы `:from`/`:to` — целые недели пн–вс, их подставляет движок.

Тесты `test_posts_published_day_on_fixture`, `test_followers_delta_day_on_fixture`, `test_lessons_accepted_week_on_fixture` должны пройти.

## Коммит
feat(metrics): posts_published_day, followers_delta_day, lessons_accepted_week
