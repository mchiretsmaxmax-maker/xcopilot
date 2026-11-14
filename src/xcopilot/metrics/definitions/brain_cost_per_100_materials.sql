-- key: brain_cost_per_100_materials
-- title: Расход моделей на обучение в пересчёте на 100 разобранных материалов
-- unit: micro_usd · grain: week · version: 1
WITH cost AS (
  SELECT date_trunc('week', c.ts AT TIME ZONE :tz)::date AS wk, sum(c.cost_micro_usd) AS micro
  FROM llm_calls c
  WHERE c.account_id = :account_id AND c.purpose LIKE 'brain.%'
    AND c.ts >= :from AND c.ts < :to
  GROUP BY 1
), mats AS (
  SELECT date_trunc('week', a.created_at AT TIME ZONE :tz)::date AS wk, count(*) AS n
  FROM material_analyses a
  WHERE a.account_id = :account_id AND a.is_current
    AND a.created_at >= :from AND a.created_at < :to
  GROUP BY 1
)
SELECT mats.wk AS period_start,
       '{}'::jsonb AS dims,
       round(coalesce(cost.micro, 0)::numeric / mats.n * 100) AS value,
       mats.n::integer AS sample_size
FROM mats
LEFT JOIN cost USING (wk);
