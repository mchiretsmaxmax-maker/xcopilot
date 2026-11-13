-- key: lift_vs_baseline
-- title: Посты с ботом против обычных
-- description: Средняя вовлечённость на 24ч постов, выпущенных через бота, делённая на медиану вовлечённости обычных постов за предыдущие 30 дней.
-- unit: ratio · grain: day · version: 1
WITH cp AS (
  SELECT (c.ts - c.age_minutes * interval '1 minute') AS posted_at,
         (COALESCE(c.likes, 0) + COALESCE(c.replies, 0) + COALESCE(c.reposts, 0)
          + COALESCE(c.quotes, 0) + COALESCE(c.bookmarks, 0))::float8
           / NULLIF(c.impressions, 0) AS er,
         EXISTS (
           SELECT 1 FROM publications p
           WHERE p.account_id = c.account_id AND p.x_post_id = c.x_post_id
         ) AS by_bot
  FROM v_post_checkpoints c
  WHERE c.account_id = :account_id
    AND c.checkpoint = '24h'
    AND c.ts - c.age_minutes * interval '1 minute' >= CAST(:from AS timestamptz) - interval '31 days'
    AND c.ts - c.age_minutes * interval '1 minute' < :to
), posts AS (
  SELECT (posted_at AT TIME ZONE :tz)::date AS day, er, by_bot FROM cp WHERE er IS NOT NULL
), bot AS (
  SELECT day, avg(er) AS er, count(*) AS n
  FROM posts
  WHERE by_bot AND day >= :day_from AND day < :day_to
  GROUP BY day
)
SELECT bot.day AS period_start,
       '{}'::jsonb AS dims,
       (bot.er / NULLIF(base.med, 0))::numeric AS value,
       bot.n AS sample_size
FROM bot
CROSS JOIN LATERAL (
  SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY p.er) AS med
  FROM posts p
  WHERE NOT p.by_bot AND p.day >= bot.day - 30 AND p.day < bot.day
) base;
