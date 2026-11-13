-- key: time_to_decision_p50
-- title: Время до решения, медиана
-- description: Медианное время от показа карточки до первого решения владельца.
-- unit: minutes · grain: day · version: 1
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
ORDER BY 1;
