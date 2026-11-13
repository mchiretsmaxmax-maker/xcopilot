-- key: impressions_24h_median
-- title: Показы на 24ч, медиана
-- description: Медианное число показов на снимке 24 часа, по дню выхода поста.
-- unit: count · grain: day · version: 1
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
ORDER BY 1;
