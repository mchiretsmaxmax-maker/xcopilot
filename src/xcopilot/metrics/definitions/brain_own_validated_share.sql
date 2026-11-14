-- key: brain_own_validated_share
-- title: Доля рабочих знаний о ремесле, проверенных на своих постах (2 и более исхода)
-- unit: ratio · grain: day · version: 1
SELECT s.snapshot_date AS period_start,
       '{}'::jsonb AS dims,
       (count(*) FILTER (WHERE s.own_support + s.own_contra >= 2))::numeric / NULLIF(count(*), 0) AS value,
       count(*) AS sample_size
FROM knowledge_snapshots s
WHERE s.account_id = :account_id
  AND s.status IN ('active', 'probation') AND s.layer <> 'voice'
  AND s.snapshot_date >= (:from)::date AND s.snapshot_date < (:to)::date
GROUP BY 1;
