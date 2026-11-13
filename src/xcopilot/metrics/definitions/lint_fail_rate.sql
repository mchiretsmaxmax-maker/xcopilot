-- key: lint_fail_rate
-- title: Срабатывания правил линтера
-- description: Доля срабатываний каждого правила линтера на сгенерированный черновик.
-- unit: ratio · grain: day · version: 1
-- dims: rule
WITH gen AS (
  SELECT (e.ts AT TIME ZONE :tz)::date AS day,
         count(*) AS n
  FROM events e
  WHERE e.account_id = :account_id
    AND e.ts >= :from AND e.ts < :to
    AND e.type = 'draft.generated'
  GROUP BY 1
), fails AS (
  SELECT (e.ts AT TIME ZONE :tz)::date AS day,
         e.props ->> 'rule' AS rule,
         count(*) AS cnt
  FROM events e
  WHERE e.account_id = :account_id
    AND e.ts >= :from AND e.ts < :to
    AND e.type = 'draft.lint_failed'
  GROUP BY 1, e.props ->> 'rule'
)
SELECT f.day AS period_start,
       jsonb_build_object('rule', f.rule) AS dims,
       f.cnt::numeric / NULLIF(g.n, 0) AS value,
       COALESCE(g.n, 0) AS sample_size
FROM fails f
LEFT JOIN gen g USING (day);
