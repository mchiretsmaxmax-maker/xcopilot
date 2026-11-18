-- key: reply_nope_rate
-- title: Доля показов «💬 Ответы», отмеченных «👎 Не то»
-- description: Запуски kind=reply, где варианты отвергнуты кнопкой «Не то» (reviews.comment = reply_nope) / все запуски режима.
-- unit: ratio · grain: day · version: 1
SELECT (r.created_at AT TIME ZONE :tz)::date AS period_start,
       '{}'::jsonb AS dims,
       count(*) FILTER (WHERE EXISTS (
           SELECT 1 FROM drafts d JOIN reviews v ON v.draft_id = d.id
           WHERE d.run_id = r.id AND v.comment = 'reply_nope'))::numeric
         / NULLIF(count(*), 0) AS value,
       count(*) AS sample_size
FROM generation_runs r
WHERE r.account_id = :account_id AND r.kind = 'reply'
  AND r.created_at >= :from AND r.created_at < :to
GROUP BY 1;
