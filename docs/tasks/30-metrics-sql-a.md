## Файлы
- (нет)

## Новые
- src/xcopilot/metrics/definitions/edit_distance_avg.sql
- src/xcopilot/metrics/definitions/rewrite_loop_avg.sql
- src/xcopilot/metrics/definitions/lint_fail_rate.sql

## Контекст
- src/xcopilot/metrics/definitions/zero_edit_rate.sql
- src/xcopilot/metrics/definitions/reject_rate_by_reason.sql
- src/xcopilot/metrics/catalog.py
- tests/integration/metrics/test_formulas.py

## Задача
Создай три SQL-файла метрик. Формат файла-метрики — как у образцов из контекста: шапка из строк `-- ключ: значение` (key = имя файла без .sql; title; description; на одной строке `-- unit: … · grain: … · version: 1`; при измерениях `-- dims: имя`), затем ОДИН запрос SELECT/WITH с колонками period_start (date), dims (jsonb), value (numeric или NULL), sample_size (int), в конце `;`. Параметры запроса: `:account_id`, `:tz` (пояс аккаунта, text), `:from` и `:to` (timestamptz, полуинтервал [from, to)), `:day_from` и `:day_to` (date, `:day_to` не включён). День события — `(ts AT TIME ZONE :tz)::date`. Параметр `:from` рядом с `::` не ставить (пиши `CAST(:from AS timestamptz)`). Всегда фильтруй `account_id = :account_id`. Не меняй никакие другие файлы. ВАЖНО: часть тестов этапа красная по независимым от тебя причинам (других метрик и docs.py пока нет) — не пытайся их чинить; следи за ruff/mypy и за тем, чтобы твои файлы парсились (`load_definitions`) и тесты на твои метрики из контекста проходили.

1. `edit_distance_avg` (unit: ratio, grain: day, title «Средняя дистанция правок»): по событиям `type = 'review.edited'`, value = `avg((props ->> 'edit_distance')::numeric)`, sample_size = count(*). Группировка по дню.
2. `rewrite_loop_avg` (unit: count, grain: day, title «Переписываний на черновик»): по событиям типов `draft.generated` и `draft.rewritten`; value = число `draft.rewritten` / NULLIF(число `draft.generated`, 0) (используй count(*) FILTER, приведи числитель к numeric); sample_size = число `draft.generated`.
3. `lint_fail_rate` (unit: ratio, grain: day, title «Срабатывания правил линтера», dims: rule): CTE gen — число `draft.generated` по дням; CTE fails — число событий `draft.lint_failed` по дням и по `props ->> 'rule'`. Результат: по строке на (день, rule): dims = `jsonb_build_object('rule', rule)`, value = fails / NULLIF(gen, 0) (numeric), sample_size = COALESCE(gen, 0). fails LEFT JOIN gen по дню.

Тесты `test_edit_distance_avg_on_fixture`, `test_rewrite_loop_avg_on_fixture`, `test_lint_fail_rate_on_fixture` должны пройти.

## Коммит
feat(metrics): edit_distance_avg, rewrite_loop_avg, lint_fail_rate
