-- key: zero_edit_rate_by_variant
-- title: Доля одобрений без правок: обычные варианты против вариантов с испытуемым знанием
-- unit: ratio · grain: day · dims: variant · version: 1
SELECT (e.ts AT TIME ZONE :tz)::date AS period_start,
       jsonb_build_object('variant', CASE WHEN d.probation_item_id IS NULL THEN 'regular' ELSE 'probation' END) AS dims,
       (count(*) FILTER (WHERE e.type = 'review.approved'))::numeric / NULLIF(count(*), 0) AS value,
       count(*) AS sample_size
FROM events e
JOIN drafts d ON d.id = e.entity_id
WHERE e.account_id = :account_id AND e.entity_type = 'draft'
  AND e.type IN ('review.approved', 'review.edited', 'review.rejected')
  AND e.ts >= :from AND e.ts < :to
GROUP BY 1, 2;
