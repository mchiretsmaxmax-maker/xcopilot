-- key: posts_published_day
-- title: Опубликовано постов
-- description: Количество подтверждённых публикаций за день.
-- unit: count · grain: day · version: 1
SELECT (e.ts AT TIME ZONE :tz)::date AS period_start,
       '{}'::jsonb AS dims,
       count(*)::numeric AS value,
       count(*) AS sample_size
FROM events e
WHERE e.account_id = :account_id
  AND e.ts >= :from AND e.ts < :to
  AND e.type = 'publication.confirmed'
GROUP BY 1;
