-- key: zero_edit_rate
-- title: Доля черновиков, одобренных без правок
-- description: Главная метрика: бот пишет «как я». Одобрено без правок / все решения владельца.
-- unit: ratio · grain: day · version: 1
SELECT (e.ts AT TIME ZONE :tz)::date AS period_start,
       '{}'::jsonb AS dims,
       count(*) FILTER (WHERE e.type = 'review.approved')::numeric
         / NULLIF(count(*), 0) AS value,
       count(*) AS sample_size
FROM events e
WHERE e.account_id = :account_id
  AND e.ts >= :from AND e.ts < :to
  AND e.type IN ('review.approved', 'review.edited', 'review.rejected')
GROUP BY 1;
