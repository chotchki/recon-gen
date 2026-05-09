WITH base AS (
  SELECT
    *,
    e.source_account_name || ' (' || e.source_account_id || ')' AS source_display,
    e.target_account_name || ' (' || e.target_account_id || ')' AS target_display
  FROM spec_example_inv_money_trail_edges AS e
)
SELECT
  *
FROM base
WHERE
  (
    source_display = %(pInvANetworkAnchor)s
    OR target_display = %(pInvANetworkAnchor)s
  )
  AND hop_amount >= %(pInvANetworkMinAmount)s
