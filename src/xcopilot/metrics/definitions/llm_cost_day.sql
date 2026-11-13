-- key: llm_cost_day
-- title: Расход на модели за день
-- description: Сумма cost_micro_usd вызовов моделей по ролям (writer, judge, cheap, embed). Микродоллары.
-- unit: micro_usd · grain: day · version: 1
-- dims: role
SELECT (c.ts AT TIME ZONE :tz)::date AS period_start,
       jsonb_build_object('role', c.role) AS dims,
       sum(c.cost_micro_usd)::numeric AS value,
       count(*) AS sample_size
FROM llm_calls c
WHERE c.account_id = :account_id AND c.ts >= :from AND c.ts < :to
GROUP BY 1, c.role;
