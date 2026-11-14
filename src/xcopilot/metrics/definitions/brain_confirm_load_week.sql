-- key: brain_confirm_load_week
-- title: Сколько решений по знаниям владелец принял за неделю (сторожевая метрика нагрузки)
-- unit: count · grain: week · dims: decision · version: 1
SELECT date_trunc('week', e.ts AT TIME ZONE :tz)::date AS period_start,
       jsonb_build_object('decision', e.props ->> 'decision') AS dims,
       count(*)::numeric AS value,
       count(*) AS sample_size
FROM events e
WHERE e.account_id = :account_id AND e.type = 'knowledge.owner_decision'
  AND e.ts >= :from AND e.ts < :to
GROUP BY 1, 2;
