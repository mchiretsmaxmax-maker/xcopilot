-- key: rewrite_loop_avg
-- title: Переписываний на черновик
-- description: Среднее число переписываний на один сгенерированный черновик.
-- unit: count · grain: day · version: 1
SELECT (e.ts AT TIME ZONE :tz)::date AS period_start,
       '{}'::jsonb AS dims,
       count(*) FILTER (WHERE e.type = 'draft.rewritten')::numeric
         / NULLIF(count(*) FILTER (WHERE e.type = 'draft.generated'), 0) AS value,
       count(*) FILTER (WHERE e.type = 'draft.generated') AS sample_size
FROM events e
WHERE e.account_id = :account_id
  AND e.ts >= :from AND e.ts < :to
  AND e.type IN ('draft.generated', 'draft.rewritten')
GROUP BY 1;
