-- key: cost_per_published_post
-- title: Цена одного опубликованного поста
-- description: (Расход на модели + расход на X API) / число публикаций за день; без публикаций — NULL. Микродоллары.
-- unit: micro_usd · grain: day · version: 1
WITH llm AS (
  SELECT (ts AT TIME ZONE :tz)::date AS day, sum(cost_micro_usd) AS cost
  FROM llm_calls
  WHERE account_id = :account_id AND ts >= :from AND ts < :to
  GROUP BY 1
), xapi AS (
  SELECT (ts AT TIME ZONE :tz)::date AS day, sum(cost_micro_usd) AS cost
  FROM x_api_calls
  WHERE account_id = :account_id AND ts >= :from AND ts < :to
  GROUP BY 1
), pub AS (
  SELECT (ts AT TIME ZONE :tz)::date AS day, count(*) AS n
  FROM events
  WHERE account_id = :account_id AND ts >= :from AND ts < :to
    AND type = 'publication.confirmed'
  GROUP BY 1
), days AS (
  SELECT day FROM llm UNION SELECT day FROM xapi UNION SELECT day FROM pub
)
SELECT days.day AS period_start,
       '{}'::jsonb AS dims,
       (COALESCE(llm.cost, 0) + COALESCE(xapi.cost, 0))::numeric
         / NULLIF(COALESCE(pub.n, 0), 0) AS value,
       COALESCE(pub.n, 0) AS sample_size
FROM days
LEFT JOIN llm USING (day)
LEFT JOIN xapi USING (day)
LEFT JOIN pub USING (day);
