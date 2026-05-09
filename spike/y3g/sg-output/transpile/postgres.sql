WITH base AS (
  SELECT
    e.*,
    source_account_name || ' (' || source_account_id || ')' AS source_display,
    target_account_name || ' (' || target_account_id || ')' AS target_display
  FROM spec_example_inv_money_trail_edges AS e
)
SELECT
  *
FROM base
WHERE
  1 = 1
  AND (
    source_display = %(pInvANetworkAnchor)s
    OR target_display = %(pInvANetworkAnchor)s
  )
  AND hop_amount >= %(pInvANetworkMinAmount)s
