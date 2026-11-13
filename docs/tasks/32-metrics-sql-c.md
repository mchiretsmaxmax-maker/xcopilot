## Файлы
- (нет)

## Новые
- src/xcopilot/metrics/definitions/time_to_decision_p50.sql
- src/xcopilot/metrics/definitions/impressions_24h_median.sql

## Контекст
- src/xcopilot/metrics/definitions/zero_edit_rate.sql
- src/xcopilot/metrics/definitions/engagement_rate_24h.sql
- src/xcopilot/metrics/definitions/judge_owner_agreement.sql
- src/xcopilot/metrics/catalog.py
- tests/integration/metrics/test_formulas.py

## Задача
Создай два SQL-файла метрик. Формат файла-метрики — как у образцов из контекста: шапка из строк `-- ключ: значение` (key = имя файла без .sql; title; description; на одной строке `-- unit: … · grain: … · version: 1`; при измерениях `-- dims: имя`), затем ОДИН запрос SELECT/WITH с колонками period_start (date), dims (jsonb), value (numeric или NULL), sample_size (int), в конце `;`. Параметры запроса: `:account_id`, `:tz` (пояс аккаунта, text), `:from` и `:to` (timestamptz, полуинтервал [from, to)), `:day_from` и `:day_to` (date, `:day_to` не включён). День события — `(ts AT TIME ZONE :tz)::date`. Параметр `:from` рядом с `::` не ставить (пиши `CAST(:from AS timestamptz)`). Всегда фильтруй `account_id = :account_id`. Не меняй никакие другие файлы. ВАЖНО: часть тестов этапа красная по независимым от тебя причинам (других метрик и docs.py пока нет) — не пытайся их чинить; следи за ruff/mypy и за тем, чтобы твои файлы парсились (`load_definitions`) и тесты на твои метрики из контекста проходили.

1. `time_to_decision_p50` (unit: minutes, grain: day, title «Время до решения, медиана»): CTE shown — для каждого `entity_id` минимальный `ts` события `card.shown` (`entity_id IS NOT NULL`, ts от `CAST(:from AS timestamptz) - interval '7 days'` до :to); CTE decided — для каждого `entity_id` ПЕРВОЕ событие (DISTINCT ON … ORDER BY entity_id, ts) из типов `review.approved`, `review.edited`, `review.rejected` в окне [:from, :to). Соединить по entity_id, оставить decided_ts >= shown_ts. period_start = день decided_ts по :tz; value = `(percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM decided_ts - shown_ts) / 60))::numeric` (минуты); sample_size = count(*).
2. `impressions_24h_median` (unit: count, grain: day, title «Показы на 24ч, медиана»): из представления `v_post_checkpoints` (колонки: account_id, x_post_id, checkpoint, ts, age_minutes, impressions, likes, …) со строками `checkpoint = '24h'` и `impressions IS NOT NULL`. Время выхода поста = `c.ts - c.age_minutes * interval '1 minute'`; оно должно лежать в [:from, :to). period_start = день выхода по :tz; value = `percentile_cont(0.5) WITHIN GROUP (ORDER BY c.impressions)` приведённый к numeric; sample_size = count(*). Образец — engagement_rate_24h.sql из контекста.

Тесты `test_time_to_decision_p50_on_fixture`, `test_impressions_24h_median_on_fixture` должны пройти.

## Коммит
feat(metrics): time_to_decision_p50, impressions_24h_median
