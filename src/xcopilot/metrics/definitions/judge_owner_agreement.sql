-- key: judge_owner_agreement
-- title: Согласие судьи с владельцем
-- description: Доля вердиктов судьи, совпавших с решением владельца (pass ↔ одобрено или отредактировано, fail ↔ отклонено).
-- unit: ratio · grain: day · version: 1
WITH decided AS (
  SELECT DISTINCT ON (e.entity_id)
         e.entity_id, e.type, (e.ts AT TIME ZONE :tz)::date AS day
  FROM events e
  WHERE e.account_id = :account_id
    AND e.ts >= :from AND e.ts < :to
    AND e.type IN ('review.approved', 'review.edited', 'review.rejected')
    AND e.entity_type = 'draft' AND e.entity_id IS NOT NULL
  ORDER BY e.entity_id, e.ts DESC
), judged AS (
  SELECT DISTINCT ON (j.entity_id) j.entity_id, j.props ->> 'verdict' AS verdict
  FROM events j
  WHERE j.account_id = :account_id
    AND j.type = 'draft.judged' AND j.entity_type = 'draft'
    AND j.entity_id IN (SELECT entity_id FROM decided)
  ORDER BY j.entity_id, j.ts DESC
)
SELECT d.day AS period_start,
       '{}'::jsonb AS dims,
       count(*) FILTER (
         WHERE (j.verdict = 'pass') = (d.type IN ('review.approved', 'review.edited'))
       )::numeric / NULLIF(count(*), 0) AS value,
       count(*) AS sample_size
FROM decided d
JOIN judged j USING (entity_id)
GROUP BY d.day;
