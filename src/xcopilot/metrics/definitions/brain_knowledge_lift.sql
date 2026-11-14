-- key: brain_knowledge_lift
-- title: Насколько посты с применёнными знаниями выше остальных своих постов по перцентилю вовлечённости
-- unit: ratio · grain: week · version: 1
WITH own_posts AS (
  SELECT s.posted_at, o.pct_engagement,
         EXISTS (SELECT 1
                 FROM publications p
                 JOIN knowledge_applications a ON a.draft_id = p.draft_id AND a.applied
                 WHERE p.account_id = s.account_id AND p.x_post_id = s.external_id) AS with_knowledge
  FROM sources s
  JOIN material_outcomes o ON o.source_id = s.id AND o.is_current
  WHERE s.account_id = :account_id AND s.stance = 'own' AND s.removed_at IS NULL
    AND o.pct_engagement IS NOT NULL
    AND s.posted_at >= :from AND s.posted_at < :to
)
SELECT date_trunc('week', posted_at AT TIME ZONE :tz)::date AS period_start,
       '{}'::jsonb AS dims,
       avg(pct_engagement) FILTER (WHERE with_knowledge)
         - avg(pct_engagement) FILTER (WHERE NOT with_knowledge) AS value,
       LEAST(count(*) FILTER (WHERE with_knowledge), count(*) FILTER (WHERE NOT with_knowledge)) AS sample_size
FROM own_posts
GROUP BY 1;
