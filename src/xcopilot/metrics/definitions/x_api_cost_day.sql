-- key: x_api_cost_day
-- title: Расход на X API за день
-- description: Сумма cost_micro_usd вызовов X API по эндпоинтам. Микродоллары.
-- unit: micro_usd · grain: day · version: 1
-- dims: endpoint
SELECT (c.ts AT TIME ZONE :tz)::date AS period_start,
       jsonb_build_object('endpoint', c.endpoint) AS dims,
       sum(c.cost_micro_usd)::numeric AS value,
       count(*) AS sample_size
FROM x_api_calls c
WHERE c.account_id = :account_id AND c.ts >= :from AND c.ts < :to
GROUP BY 1, c.endpoint;
