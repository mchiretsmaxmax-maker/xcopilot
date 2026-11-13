# Каталог метрик

> Генерируется командой `make db-docs` из `src/xcopilot/metrics/definitions/*.sql` (`xcopilot.metrics.docs`). Не редактировать вручную.

Метрик: 17.

## Как читать

Единицы: `ratio` — доля 0..1; `count` — штуки; `micro_usd` — миллионные доли доллара (BIGINT-деньги); `minutes` — минуты. Зерно: `day` — день по поясу аккаунта, `week` — неделя пн–вс. «Сегодня» считается на лету.

## Сводка

| Ключ | Что показывает | Единица | Зерно | Измерения |
|---|---|---|---|---|
| `approval_rate` | Доля черновиков, дошедших до публикации. (Одобрено + отредактировано) / все решения владельца. | `ratio` | `day` | — |
| `cost_per_published_post` | Цена одного опубликованного поста. (Расход на модели + расход на X API) / число публикаций за день; без публикаций — NULL. Микродоллары. | `micro_usd` | `day` | — |
| `edit_distance_avg` | Средняя дистанция правок. Средняя дистанция правок по событиям review.edited. | `ratio` | `day` | — |
| `engagement_rate_24h` | Вовлечённость на отметке 24 часа. (Лайки + ответы + репосты + цитаты + закладки) / показы на отметке 24ч (первый снимок не раньше суток), по дню выхода поста. | `ratio` | `day` | — |
| `followers_delta_day` | Прирост подписчиков за день. Разница числа подписчиков между текущим и предыдущим днём. | `count` | `day` | — |
| `impressions_24h_median` | Показы на 24ч, медиана. Медианное число показов на снимке 24 часа, по дню выхода поста. | `count` | `day` | — |
| `judge_owner_agreement` | Согласие судьи с владельцем. Доля вердиктов судьи, совпавших с решением владельца (pass ↔ одобрено или отредактировано, fail ↔ отклонено). | `ratio` | `day` | — |
| `lessons_accepted_week` | Принято уроков за неделю. Количество принятых уроков за неделю. | `count` | `week` | — |
| `lift_vs_baseline` | Посты с ботом против обычных. Средняя вовлечённость на 24ч постов, выпущенных через бота, делённая на медиану вовлечённости обычных постов за предыдущие 30 дней. | `ratio` | `day` | — |
| `lint_fail_rate` | Срабатывания правил линтера. Доля срабатываний каждого правила линтера на сгенерированный черновик. | `ratio` | `day` | `rule` |
| `llm_cost_day` | Расход на модели за день. Сумма cost_micro_usd вызовов моделей по ролям (writer, judge, cheap, embed). Микродоллары. | `micro_usd` | `day` | `role` |
| `posts_published_day` | Опубликовано постов. Количество подтверждённых публикаций за день. | `count` | `day` | — |
| `reject_rate_by_reason` | Отказы по причинам. За что отклоняют: доля отказов с данным reason_code среди всех решений дня. | `ratio` | `day` | `reason_code` |
| `rewrite_loop_avg` | Переписываний на черновик. Среднее число переписываний на один сгенерированный черновик. | `count` | `day` | — |
| `time_to_decision_p50` | Время до решения, медиана. Медианное время от показа карточки до первого решения владельца. | `minutes` | `day` | — |
| `x_api_cost_day` | Расход на X API за день. Сумма cost_micro_usd вызовов X API по эндпоинтам. Микродоллары. | `micro_usd` | `day` | `endpoint` |
| `zero_edit_rate` | Доля черновиков, одобренных без правок. Главная метрика: бот пишет «как я». Одобрено без правок / все решения владельца. | `ratio` | `day` | — |

## `approval_rate`

Доля черновиков, дошедших до публикации

(Одобрено + отредактировано) / все решения владельца.

Единица: `ratio` · зерно: `day` · версия: 1

Формула:

```sql
SELECT (e.ts AT TIME ZONE :tz)::date AS period_start,
       '{}'::jsonb AS dims,
       count(*) FILTER (WHERE e.type IN ('review.approved', 'review.edited'))::numeric
         / NULLIF(count(*), 0) AS value,
       count(*) AS sample_size
FROM events e
WHERE e.account_id = :account_id
  AND e.ts >= :from AND e.ts < :to
  AND e.type IN ('review.approved', 'review.edited', 'review.rejected')
GROUP BY 1
```

## `cost_per_published_post`

Цена одного опубликованного поста

(Расход на модели + расход на X API) / число публикаций за день; без публикаций — NULL. Микродоллары.

Единица: `micro_usd` · зерно: `day` · версия: 1

Формула:

```sql
WITH llm AS (
  SELECT (ts AT TIME ZONE :tz)::date AS day, sum(cost_micro_usd) AS cost
  FROM llm_calls
  WHERE account_id = :account_id AND ts >= :from AND ts < :to
  GROUP BY 1
), xapi AS (
  SELECT (ts AT TIME ZONE :tz)::date AS day, sum(cost_micro_usd) AS cost
  FROM x_api_calls
  WHERE account_id = :account_id AND ts >= :from AND ts < :to
  GROUP BY 1
), pub AS (
  SELECT (ts AT TIME ZONE :tz)::date AS day, count(*) AS n
  FROM events
  WHERE account_id = :account_id AND ts >= :from AND ts < :to
    AND type = 'publication.confirmed'
  GROUP BY 1
), days AS (
  SELECT day FROM llm UNION SELECT day FROM xapi UNION SELECT day FROM pub
)
SELECT days.day AS period_start,
       '{}'::jsonb AS dims,
       (COALESCE(llm.cost, 0) + COALESCE(xapi.cost, 0))::numeric
         / NULLIF(COALESCE(pub.n, 0), 0) AS value,
       COALESCE(pub.n, 0) AS sample_size
FROM days
LEFT JOIN llm USING (day)
LEFT JOIN xapi USING (day)
LEFT JOIN pub USING (day)
```

## `edit_distance_avg`

Средняя дистанция правок

Средняя дистанция правок по событиям review.edited.

Единица: `ratio` · зерно: `day` · версия: 1

Формула:

```sql
SELECT (e.ts AT TIME ZONE :tz)::date AS period_start,
       '{}'::jsonb AS dims,
       avg((e.props ->> 'edit_distance')::numeric) AS value,
       count(*) AS sample_size
FROM events e
WHERE e.account_id = :account_id
  AND e.ts >= :from AND e.ts < :to
  AND e.type = 'review.edited'
GROUP BY 1
```

## `engagement_rate_24h`

Вовлечённость на отметке 24 часа

(Лайки + ответы + репосты + цитаты + закладки) / показы на отметке 24ч (первый снимок не раньше суток), по дню выхода поста.

Единица: `ratio` · зерно: `day` · версия: 1

Формула:

```sql
SELECT ((c.ts - c.age_minutes * interval '1 minute') AT TIME ZONE :tz)::date AS period_start,
       '{}'::jsonb AS dims,
       sum(COALESCE(c.likes, 0) + COALESCE(c.replies, 0) + COALESCE(c.reposts, 0)
           + COALESCE(c.quotes, 0) + COALESCE(c.bookmarks, 0))::numeric
         / NULLIF(sum(c.impressions), 0) AS value,
       count(*) AS sample_size
FROM v_post_checkpoints c
WHERE c.account_id = :account_id
  AND c.checkpoint = '24h'
  AND c.impressions IS NOT NULL
  AND c.ts - c.age_minutes * interval '1 minute' >= :from
  AND c.ts - c.age_minutes * interval '1 minute' < :to
GROUP BY 1
```

## `followers_delta_day`

Прирост подписчиков за день

Разница числа подписчиков между текущим и предыдущим днём.

Единица: `count` · зерно: `day` · версия: 1

Формула:

```sql
SELECT s.date AS period_start,
       '{}'::jsonb AS dims,
       (s.followers - p.followers)::numeric AS value,
       1 AS sample_size
FROM account_metric_snapshots s
LEFT JOIN account_metric_snapshots p
  ON p.account_id = s.account_id
 AND p.date = s.date - 1
WHERE s.account_id = :account_id
  AND s.date >= :day_from AND s.date < :day_to
  AND s.followers IS NOT NULL
  AND p.followers IS NOT NULL
```

## `impressions_24h_median`

Показы на 24ч, медиана

Медианное число показов на снимке 24 часа, по дню выхода поста.

Единица: `count` · зерно: `day` · версия: 1

Формула:

```sql
SELECT ((c.ts - c.age_minutes * interval '1 minute') AT TIME ZONE :tz)::date AS period_start,
       '{}'::jsonb AS dims,
       CAST(percentile_cont(0.5) WITHIN GROUP (ORDER BY c.impressions) AS numeric) AS value,
       count(*) AS sample_size
FROM v_post_checkpoints c
WHERE c.account_id = :account_id
  AND c.checkpoint = '24h'
  AND c.impressions IS NOT NULL
  AND c.ts - c.age_minutes * interval '1 minute' >= CAST(:from AS timestamptz)
  AND c.ts - c.age_minutes * interval '1 minute' < CAST(:to AS timestamptz)
GROUP BY 1
ORDER BY 1
```

## `judge_owner_agreement`

Согласие судьи с владельцем

Доля вердиктов судьи, совпавших с решением владельца (pass ↔ одобрено или отредактировано, fail ↔ отклонено).

Единица: `ratio` · зерно: `day` · версия: 1

Формула:

```sql
WITH decided AS (
  SELECT DISTINCT ON (e.entity_id)
         e.entity_id, e.type, (e.ts AT TIME ZONE :tz)::date AS day
  FROM events e
  WHERE e.account_id = :account_id
    AND e.ts >= :from AND e.ts < :to
    AND e.type IN ('review.approved', 'review.edited', 'review.rejected')
    AND e.entity_type = 'draft' AND e.entity_id IS NOT NULL
  ORDER BY e.entity_id, e.ts DESC
), judged AS (
  SELECT DISTINCT ON (j.entity_id) j.entity_id, j.props ->> 'verdict' AS verdict
  FROM events j
  WHERE j.account_id = :account_id
    AND j.type = 'draft.judged' AND j.entity_type = 'draft'
    AND j.entity_id IN (SELECT entity_id FROM decided)
  ORDER BY j.entity_id, j.ts DESC
)
SELECT d.day AS period_start,
       '{}'::jsonb AS dims,
       count(*) FILTER (
         WHERE (j.verdict = 'pass') = (d.type IN ('review.approved', 'review.edited'))
       )::numeric / NULLIF(count(*), 0) AS value,
       count(*) AS sample_size
FROM decided d
JOIN judged j USING (entity_id)
GROUP BY d.day
```

## `lessons_accepted_week`

Принято уроков за неделю

Количество принятых уроков за неделю.

Единица: `count` · зерно: `week` · версия: 1

Формула:

```sql
SELECT date_trunc('week', e.ts AT TIME ZONE :tz)::date AS period_start,
       '{}'::jsonb AS dims,
       count(*)::numeric AS value,
       count(*) AS sample_size
FROM events e
WHERE e.account_id = :account_id
  AND e.ts >= :from AND e.ts < :to
  AND e.type = 'lesson.accepted'
GROUP BY 1
```

## `lift_vs_baseline`

Посты с ботом против обычных

Средняя вовлечённость на 24ч постов, выпущенных через бота, делённая на медиану вовлечённости обычных постов за предыдущие 30 дней.

Единица: `ratio` · зерно: `day` · версия: 1

Формула:

```sql
WITH cp AS (
  SELECT (c.ts - c.age_minutes * interval '1 minute') AS posted_at,
         (COALESCE(c.likes, 0) + COALESCE(c.replies, 0) + COALESCE(c.reposts, 0)
          + COALESCE(c.quotes, 0) + COALESCE(c.bookmarks, 0))::float8
           / NULLIF(c.impressions, 0) AS er,
         EXISTS (
           SELECT 1 FROM publications p
           WHERE p.account_id = c.account_id AND p.x_post_id = c.x_post_id
         ) AS by_bot
  FROM v_post_checkpoints c
  WHERE c.account_id = :account_id
    AND c.checkpoint = '24h'
    AND c.ts - c.age_minutes * interval '1 minute' >= CAST(:from AS timestamptz) - interval '31 days'
    AND c.ts - c.age_minutes * interval '1 minute' < :to
), posts AS (
  SELECT (posted_at AT TIME ZONE :tz)::date AS day, er, by_bot FROM cp WHERE er IS NOT NULL
), bot AS (
  SELECT day, avg(er) AS er, count(*) AS n
  FROM posts
  WHERE by_bot AND day >= :day_from AND day < :day_to
  GROUP BY day
)
SELECT bot.day AS period_start,
       '{}'::jsonb AS dims,
       (bot.er / NULLIF(base.med, 0))::numeric AS value,
       bot.n AS sample_size
FROM bot
CROSS JOIN LATERAL (
  SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY p.er) AS med
  FROM posts p
  WHERE NOT p.by_bot AND p.day >= bot.day - 30 AND p.day < bot.day
) base
```

## `lint_fail_rate`

Срабатывания правил линтера

Доля срабатываний каждого правила линтера на сгенерированный черновик.

Единица: `ratio` · зерно: `day` · версия: 1 · измерения: `rule`

Формула:

```sql
WITH gen AS (
  SELECT (e.ts AT TIME ZONE :tz)::date AS day,
         count(*) AS n
  FROM events e
  WHERE e.account_id = :account_id
    AND e.ts >= :from AND e.ts < :to
    AND e.type = 'draft.generated'
  GROUP BY 1
), fails AS (
  SELECT (e.ts AT TIME ZONE :tz)::date AS day,
         e.props ->> 'rule' AS rule,
         count(*) AS cnt
  FROM events e
  WHERE e.account_id = :account_id
    AND e.ts >= :from AND e.ts < :to
    AND e.type = 'draft.lint_failed'
  GROUP BY 1, e.props ->> 'rule'
)
SELECT f.day AS period_start,
       jsonb_build_object('rule', f.rule) AS dims,
       f.cnt::numeric / NULLIF(g.n, 0) AS value,
       COALESCE(g.n, 0) AS sample_size
FROM fails f
LEFT JOIN gen g USING (day)
```

## `llm_cost_day`

Расход на модели за день

Сумма cost_micro_usd вызовов моделей по ролям (writer, judge, cheap, embed). Микродоллары.

Единица: `micro_usd` · зерно: `day` · версия: 1 · измерения: `role`

Формула:

```sql
SELECT (c.ts AT TIME ZONE :tz)::date AS period_start,
       jsonb_build_object('role', c.role) AS dims,
       sum(c.cost_micro_usd)::numeric AS value,
       count(*) AS sample_size
FROM llm_calls c
WHERE c.account_id = :account_id AND c.ts >= :from AND c.ts < :to
GROUP BY 1, c.role
```

## `posts_published_day`

Опубликовано постов

Количество подтверждённых публикаций за день.

Единица: `count` · зерно: `day` · версия: 1

Формула:

```sql
SELECT (e.ts AT TIME ZONE :tz)::date AS period_start,
       '{}'::jsonb AS dims,
       count(*)::numeric AS value,
       count(*) AS sample_size
FROM events e
WHERE e.account_id = :account_id
  AND e.ts >= :from AND e.ts < :to
  AND e.type = 'publication.confirmed'
GROUP BY 1
```

## `reject_rate_by_reason`

Отказы по причинам

За что отклоняют: доля отказов с данным reason_code среди всех решений дня.

Единица: `ratio` · зерно: `day` · версия: 1 · измерения: `reason_code`

Формула:

```sql
WITH decisions AS (
  SELECT (e.ts AT TIME ZONE :tz)::date AS day, e.type, e.props
  FROM events e
  WHERE e.account_id = :account_id
    AND e.ts >= :from AND e.ts < :to
    AND e.type IN ('review.approved', 'review.edited', 'review.rejected')
), totals AS (
  SELECT day, count(*) AS n FROM decisions GROUP BY day
)
SELECT r.day AS period_start,
       jsonb_build_object('reason_code', r.reason) AS dims,
       r.cnt::numeric / NULLIF(t.n, 0) AS value,
       t.n AS sample_size
FROM (
  SELECT day, props ->> 'reason_code' AS reason, count(*) AS cnt
  FROM decisions WHERE type = 'review.rejected' GROUP BY day, props ->> 'reason_code'
) r
JOIN totals t USING (day)
```

## `rewrite_loop_avg`

Переписываний на черновик

Среднее число переписываний на один сгенерированный черновик.

Единица: `count` · зерно: `day` · версия: 1

Формула:

```sql
SELECT (e.ts AT TIME ZONE :tz)::date AS period_start,
       '{}'::jsonb AS dims,
       count(*) FILTER (WHERE e.type = 'draft.rewritten')::numeric
         / NULLIF(count(*) FILTER (WHERE e.type = 'draft.generated'), 0) AS value,
       count(*) FILTER (WHERE e.type = 'draft.generated') AS sample_size
FROM events e
WHERE e.account_id = :account_id
  AND e.ts >= :from AND e.ts < :to
  AND e.type IN ('draft.generated', 'draft.rewritten')
GROUP BY 1
```

## `time_to_decision_p50`

Время до решения, медиана

Медианное время от показа карточки до первого решения владельца.

Единица: `minutes` · зерно: `day` · версия: 1

Формула:

```sql
WITH shown AS (
  SELECT entity_id, MIN(ts) AS shown_ts
  FROM events
  WHERE account_id = :account_id
    AND type = 'card.shown'
    AND entity_id IS NOT NULL
    AND ts >= CAST(:from AS timestamptz) - interval '7 days'
    AND ts < CAST(:to AS timestamptz)
  GROUP BY entity_id
), decided AS (
  SELECT DISTINCT ON (entity_id) entity_id, ts AS decided_ts, type
  FROM events
  WHERE account_id = :account_id
    AND type IN ('review.approved', 'review.edited', 'review.rejected')
    AND entity_id IS NOT NULL
    AND ts >= CAST(:from AS timestamptz)
    AND ts < CAST(:to AS timestamptz)
  ORDER BY entity_id, ts
)
SELECT (d.decided_ts AT TIME ZONE :tz)::date AS period_start,
       '{}'::jsonb AS dims,
       CAST(percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM d.decided_ts - s.shown_ts) / 60) AS numeric) AS value,
       count(*) AS sample_size
FROM decided d
JOIN shown s ON s.entity_id = d.entity_id
WHERE d.decided_ts >= s.shown_ts
GROUP BY 1
ORDER BY 1
```

## `x_api_cost_day`

Расход на X API за день

Сумма cost_micro_usd вызовов X API по эндпоинтам. Микродоллары.

Единица: `micro_usd` · зерно: `day` · версия: 1 · измерения: `endpoint`

Формула:

```sql
SELECT (c.ts AT TIME ZONE :tz)::date AS period_start,
       jsonb_build_object('endpoint', c.endpoint) AS dims,
       sum(c.cost_micro_usd)::numeric AS value,
       count(*) AS sample_size
FROM x_api_calls c
WHERE c.account_id = :account_id AND c.ts >= :from AND c.ts < :to
GROUP BY 1, c.endpoint
```

## `zero_edit_rate`

Доля черновиков, одобренных без правок

Главная метрика: бот пишет «как я». Одобрено без правок / все решения владельца.

Единица: `ratio` · зерно: `day` · версия: 1

Формула:

```sql
SELECT (e.ts AT TIME ZONE :tz)::date AS period_start,
       '{}'::jsonb AS dims,
       count(*) FILTER (WHERE e.type = 'review.approved')::numeric
         / NULLIF(count(*), 0) AS value,
       count(*) AS sample_size
FROM events e
WHERE e.account_id = :account_id
  AND e.ts >= :from AND e.ts < :to
  AND e.type IN ('review.approved', 'review.edited', 'review.rejected')
GROUP BY 1
```
