-- key: brain_pack_coverage
-- title: Доля генераций, где под задачу нашлось не меньше 8 рабочих знаний
-- unit: ratio · grain: day · dims: format · version: 1
SELECT (p.created_at AT TIME ZONE :tz)::date AS period_start,
       jsonb_build_object('format', p.task ->> 'format') AS dims,
       (count(*) FILTER (WHERE jsonb_array_length(
            jsonb_path_query_array(p.items, '$[*] ? (@.slot == "core")')) >= 8))::numeric
         / NULLIF(count(*), 0) AS value,
       count(*) AS sample_size
FROM knowledge_packs p
WHERE p.account_id = :account_id AND p.consumer = 'writer'
  AND p.created_at >= :from AND p.created_at < :to
GROUP BY 1, 2;
