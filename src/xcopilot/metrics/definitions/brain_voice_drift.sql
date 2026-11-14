-- key: brain_voice_drift
-- title: Косинусное расстояние между постами с ботом и своими постами до бота (сторожевая метрика: рост = голос уплывает)
-- unit: distance · grain: week · version: 1
WITH base AS (
  SELECT avg(e.vec) AS centroid
  FROM sources s
  JOIN documents d ON d.source_id = s.id
  JOIN chunks ch   ON ch.document_id = d.id
  JOIN embeddings e ON e.chunk_id = ch.id
  WHERE s.account_id = :account_id AND s.stance = 'own'
    AND s.origin = 'history_backfill' AND s.removed_at IS NULL
), bot AS (
  SELECT date_trunc('week', p.published_at AT TIME ZONE :tz)::date AS wk,
         avg(e.vec) AS centroid,
         count(DISTINCT p.id) AS n
  FROM publications p
  JOIN sources s   ON s.account_id = p.account_id AND s.external_id = p.x_post_id AND s.stance = 'own'
  JOIN documents d ON d.source_id = s.id
  JOIN chunks ch   ON ch.document_id = d.id
  JOIN embeddings e ON e.chunk_id = ch.id
  WHERE p.account_id = :account_id
    AND p.published_at >= :from AND p.published_at < :to
  GROUP BY 1
)
SELECT bot.wk AS period_start,
       '{}'::jsonb AS dims,
       (bot.centroid <=> base.centroid)::numeric AS value,
       bot.n::integer AS sample_size
FROM bot
CROSS JOIN base
WHERE base.centroid IS NOT NULL;
