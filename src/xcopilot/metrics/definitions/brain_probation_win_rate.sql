-- key: brain_probation_win_rate
-- title: Как часто владелец выбирает вариант с новым приёмом (случайный уровень при трёх вариантах — 0.33)
-- unit: ratio · grain: week · version: 1
WITH decided AS (
  SELECT d.run_id,
         bool_or(d.probation_item_id IS NOT NULL) AS run_has_probation,
         bool_or(d.probation_item_id IS NOT NULL AND r.action IN ('approve', 'edit')) AS probation_chosen,
         min(r.created_at) FILTER (WHERE r.action IN ('approve', 'edit')) AS decided_at
  FROM drafts d
  LEFT JOIN reviews r ON r.draft_id = d.id
  WHERE d.account_id = :account_id
  GROUP BY d.run_id
)
SELECT date_trunc('week', decided_at AT TIME ZONE :tz)::date AS period_start,
       '{}'::jsonb AS dims,
       (count(*) FILTER (WHERE probation_chosen))::numeric / NULLIF(count(*), 0) AS value,
       count(*) AS sample_size
FROM decided
WHERE run_has_probation AND decided_at >= :from AND decided_at < :to
GROUP BY 1;
