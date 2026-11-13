-- key: reject_rate_by_reason
-- title: Отказы по причинам
-- description: За что отклоняют: доля отказов с данным reason_code среди всех решений дня.
-- unit: ratio · grain: day · version: 1
-- dims: reason_code
WITH decisions AS (
  SELECT (e.ts AT TIME ZONE :tz)::date AS day, e.type, e.props
  FROM events e
  WHERE e.account_id = :account_id
    AND e.ts >= :from AND e.ts < :to
    AND e.type IN ('review.approved', 'review.edited', 'review.rejected')
), totals AS (
  SELECT day, count(*) AS n FROM decisions GROUP BY day
)
SELECT r.day AS period_start,
       jsonb_build_object('reason_code', r.reason) AS dims,
       r.cnt::numeric / NULLIF(t.n, 0) AS value,
       t.n AS sample_size
FROM (
  SELECT day, props ->> 'reason_code' AS reason, count(*) AS cnt
  FROM decisions WHERE type = 'review.rejected' GROUP BY day, props ->> 'reason_code'
) r
JOIN totals t USING (day);
