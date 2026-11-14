-- key: brain_materials_day
-- title: Сколько материалов разобрано за день
-- unit: count · grain: day · dims: stance · version: 1
SELECT (a.created_at AT TIME ZONE :tz)::date AS period_start,
       jsonb_build_object('stance', s.stance) AS dims,
       count(*)::numeric AS value,
       count(*) AS sample_size
FROM material_analyses a
JOIN sources s ON s.id = a.source_id
WHERE a.account_id = :account_id AND a.is_current
  AND a.created_at >= :from AND a.created_at < :to
GROUP BY 1, 2;
