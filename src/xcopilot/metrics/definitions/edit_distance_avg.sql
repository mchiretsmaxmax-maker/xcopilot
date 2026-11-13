-- key: edit_distance_avg
-- title: Средняя дистанция правок
-- description: Средняя дистанция правок по событиям review.edited.
-- unit: ratio · grain: day · version: 1
SELECT (e.ts AT TIME ZONE :tz)::date AS period_start,
       '{}'::jsonb AS dims,
       avg((e.props ->> 'edit_distance')::numeric) AS value,
       count(*) AS sample_size
FROM events e
WHERE e.account_id = :account_id
  AND e.ts >= :from AND e.ts < :to
  AND e.type = 'review.edited'
GROUP BY 1;
