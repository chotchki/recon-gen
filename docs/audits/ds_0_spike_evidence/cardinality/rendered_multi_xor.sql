CREATE TABLE spec_example_multi_xor_violation AS
WITH multi_xor_chains AS (
  -- Non-fan_in children of chains whose non-fan_in sibling
  -- count is ≥ 2 (the multi-XOR qualifier). Singleton + fan_in
  -- chains are handled by AB.2.3 + AB.4.7 respectively.
  SELECT parent_name AS chain_parent_name,
         child_name
  FROM spec_example_v_config_chain_children
  WHERE fan_in = 0
    AND parent_name IN (
      SELECT parent_name
      FROM spec_example_v_config_chain_children
      WHERE fan_in = 0
      GROUP BY parent_name
      HAVING COUNT(*) >= 2
    )
),
parent_names AS (
  SELECT DISTINCT chain_parent_name AS name FROM multi_xor_chains
),
parent_firings AS (
  -- Every transfer that fires under a multi-XOR chain parent.
  -- Chain.parent can be a rail OR a template name (UNION both).
  -- DISTINCT collapses multi-leg template firings to one row.
  SELECT DISTINCT tx.transfer_id AS parent_transfer_id,
         tx.template_name AS chain_parent_name,
         DATE_TRUNC('day', tx.posting) AS business_day
  FROM spec_example_current_transactions tx
  WHERE tx.template_name IN (SELECT name FROM parent_names)
    AND tx.status <> 'Failed'
  UNION
  SELECT DISTINCT tx.transfer_id, tx.rail_name,
         DATE_TRUNC('day', tx.posting)
  FROM spec_example_current_transactions tx
  WHERE tx.rail_name IN (SELECT name FROM parent_names)
    AND tx.status <> 'Failed'
),
fired_children_distinct AS (
  -- For each parent firing, which declared XOR siblings
  -- fired? LEFT JOIN preserves the missed (count=0) case
  -- (the DISTINCT collapses multi-leg child firings to one
  -- name per (parent, child)).
  SELECT DISTINCT
    pf.parent_transfer_id,
    pf.chain_parent_name,
    pf.business_day,
    CASE WHEN ch.rail_name IS NOT NULL
           AND EXISTS (SELECT 1 FROM multi_xor_chains m
                       WHERE m.chain_parent_name = pf.chain_parent_name
                         AND m.child_name = ch.rail_name)
         THEN ch.rail_name
         WHEN ch.template_name IS NOT NULL
           AND EXISTS (SELECT 1 FROM multi_xor_chains m
                       WHERE m.chain_parent_name = pf.chain_parent_name
                         AND m.child_name = ch.template_name)
         THEN ch.template_name
    END AS matched_child_name
  FROM parent_firings pf
  LEFT JOIN spec_example_current_transactions ch
    ON ch.transfer_parent_id = pf.parent_transfer_id
   AND ch.status <> 'Failed'
)
SELECT
  fcd.parent_transfer_id,
  fcd.chain_parent_name AS parent_rail_or_template_name,
  COUNT(fcd.matched_child_name) AS child_count,
  COALESCE(STRING_AGG(fcd.matched_child_name, ',' ORDER BY fcd.matched_child_name), '') AS fired_children,
  CASE WHEN COUNT(fcd.matched_child_name) = 0 THEN 'missed'
       ELSE 'overlap' END AS disagreement_kind,
  MIN(fcd.business_day) AS business_day
FROM fired_children_distinct fcd
GROUP BY fcd.parent_transfer_id, fcd.chain_parent_name
HAVING COUNT(fcd.matched_child_name) <> 1;