-- key: brain_auto_revert_rate
-- title: Доля автоматических повышений знаний, которые владелец откатил
-- unit: ratio · grain: week · version: 1
SELECT date_trunc('week', t.ts AT TIME ZONE :tz)::date AS period_start,
       '{}'::jsonb AS dims,
       (count(*) FILTER (WHERE t.reverted_at IS NOT NULL))::numeric / NULLIF(count(*), 0) AS value,
       count(*) AS sample_size
FROM knowledge_transitions t
WHERE t.account_id = :account_id AND t.actor = 'system'
  AND t.to_status IN ('probation', 'active')
  AND t.ts >= :from AND t.ts < :to
GROUP BY 1;
