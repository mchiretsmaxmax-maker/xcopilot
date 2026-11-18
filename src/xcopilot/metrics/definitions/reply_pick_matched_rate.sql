-- key: reply_pick_matched_rate
-- title: Доля показов «💬 Ответы», где ответ владельца нашёлся и совпал с вариантом
-- description: Показы (запуски kind=reply старше суток) с решением автосверки (reviews.comment = harvest_match) / все такие показы. Ниже 50% через 2 недели после R4 — включать R5 (кнопка «↩️ Ответить» с отметкой выбора).
-- unit: ratio · grain: day · version: 1
SELECT (r.created_at AT TIME ZONE :tz)::date AS period_start,
       '{}'::jsonb AS dims,
       count(*) FILTER (WHERE EXISTS (
           SELECT 1 FROM drafts d JOIN reviews v ON v.draft_id = d.id
           WHERE d.run_id = r.id AND v.comment = 'harvest_match'))::numeric
         / NULLIF(count(*), 0) AS value,
       count(*) AS sample_size
FROM generation_runs r
WHERE r.account_id = :account_id AND r.kind = 'reply'
  AND r.created_at >= :from AND r.created_at < :to
  AND r.created_at < now() - interval '1 day'
GROUP BY 1;
