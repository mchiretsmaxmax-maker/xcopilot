-- key: engagement_rate_24h
-- title: Вовлечённость на отметке 24 часа
-- description: (Лайки + ответы + репосты + цитаты + закладки) / показы на отметке 24ч (первый снимок не раньше суток), по дню выхода поста.
-- unit: ratio · grain: day · version: 1
SELECT ((c.ts - c.age_minutes * interval '1 minute') AT TIME ZONE :tz)::date AS period_start,
       '{}'::jsonb AS dims,
       sum(COALESCE(c.likes, 0) + COALESCE(c.replies, 0) + COALESCE(c.reposts, 0)
           + COALESCE(c.quotes, 0) + COALESCE(c.bookmarks, 0))::numeric
         / NULLIF(sum(c.impressions), 0) AS value,
       count(*) AS sample_size
FROM v_post_checkpoints c
WHERE c.account_id = :account_id
  AND c.checkpoint = '24h'
  AND c.impressions IS NOT NULL
  AND c.ts - c.age_minutes * interval '1 minute' >= :from
  AND c.ts - c.age_minutes * interval '1 minute' < :to
GROUP BY 1;
