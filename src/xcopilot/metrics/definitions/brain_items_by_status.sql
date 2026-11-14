-- key: brain_items_by_status
-- title: Сколько знаний в каждом статусе на конец дня
-- unit: count · grain: day · dims: status, layer · version: 1
SELECT s.snapshot_date AS period_start,
       jsonb_build_object('status', s.status, 'layer', s.layer) AS dims,
       count(*)::numeric AS value,
       count(*) AS sample_size
FROM knowledge_snapshots s
WHERE s.account_id = :account_id
  AND s.snapshot_date >= (:from)::date AND s.snapshot_date < (:to)::date
GROUP BY 1, 2;
