-- key: followers_delta_day
-- title: Прирост подписчиков за день
-- description: Разница числа подписчиков между текущим и предыдущим днём.
-- unit: count · grain: day · version: 1
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
  AND p.followers IS NOT NULL;
