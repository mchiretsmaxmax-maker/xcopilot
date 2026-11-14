-- key: brain_thesis_precision_own
-- title: Как часто применённое знание срабатывает на своих постах
-- unit: ratio · grain: week · version: 1
SELECT date_trunc('week', e.created_at AT TIME ZONE :tz)::date AS period_start,
       '{}'::jsonb AS dims,
       (count(*) FILTER (WHERE e.polarity = 1))::numeric
         / NULLIF(count(*) FILTER (WHERE e.polarity <> 0), 0) AS value,
       count(*) FILTER (WHERE e.polarity <> 0) AS sample_size
FROM knowledge_evidence e
WHERE e.account_id = :account_id
  AND e.basis = 'own_outcome' AND e.revoked_at IS NULL
  AND e.created_at >= :from AND e.created_at < :to
GROUP BY 1;
