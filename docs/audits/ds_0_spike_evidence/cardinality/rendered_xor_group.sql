CREATE TABLE spec_example_xor_group_violation AS
WITH xor_groups(template_name, xor_group_index, member_rail_name) AS (
  VALUES
    ('SettlementTimingCycle', 0, 'SettlementAuto'),
    ('SettlementTimingCycle', 0, 'SettlementStandard')
),
template_transfers AS (
  -- Every Transfer instance of a template that has at least
  -- one XOR group. We GROUP BY (transfer_id, template_name)
  -- via the cartesian below to get one row per (Transfer, group).
  SELECT DISTINCT tx.transfer_id, tx.template_name,
         MIN(DATE_TRUNC('day', tx.posting))
           OVER (PARTITION BY tx.transfer_id, tx.template_name) AS business_day
  FROM spec_example_current_transactions tx
  WHERE tx.status <> 'Failed'
    AND tx.template_name IN (SELECT DISTINCT template_name FROM xor_groups)
),
expected AS (
  -- Cartesian: every (Transfer-of-T, group-of-T) pair we need
  -- to check.
  SELECT tt.transfer_id, tt.template_name, g.xor_group_index,
         MIN(tt.business_day) AS business_day
  FROM template_transfers tt
  JOIN xor_groups g ON g.template_name = tt.template_name
  GROUP BY tt.transfer_id, tt.template_name, g.xor_group_index
)
SELECT
  e.transfer_id,
  e.template_name,
  e.xor_group_index,
  COUNT(tx.transfer_id) AS firing_count,
  COALESCE(STRING_AGG(tx.rail_name, ',' ORDER BY tx.rail_name), '') AS fired_rails,
  e.business_day
FROM expected e
JOIN xor_groups g
  ON g.template_name = e.template_name
  AND g.xor_group_index = e.xor_group_index
LEFT JOIN spec_example_current_transactions tx
  ON tx.transfer_id = e.transfer_id
  AND tx.template_name = e.template_name
  AND tx.rail_name = g.member_rail_name
  AND tx.status <> 'Failed'
GROUP BY e.transfer_id, e.template_name, e.xor_group_index, e.business_day
HAVING COUNT(tx.transfer_id) <> 1;