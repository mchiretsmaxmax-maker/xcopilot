-- key: lessons_accepted_week
-- title: Принято уроков за неделю
-- description: Количество принятых уроков за неделю.
-- unit: count · grain: week · version: 1
SELECT date_trunc('week', e.ts AT TIME ZONE :tz)::date AS period_start,
       '{}'::jsonb AS dims,
       count(*)::numeric AS value,
       count(*) AS sample_size
FROM events e
WHERE e.account_id = :account_id
  AND e.ts >= :from AND e.ts < :to
  AND e.type = 'lesson.accepted'
GROUP BY 1;
