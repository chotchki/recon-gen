WITH base AS (
  SELECT
    e.*,
    source_account_name || ' (' || source_account_id || ')' AS source_display,
    target_account_name || ' (' || target_account_id || ')' AS target_display
  FROM spec_example_inv_money_trail_edges e
)
SELECT
  *
FROM base
WHERE
  1 = 1
  AND (
    source_display = <<$pInvANetworkAnchor>> OR target_display = <<$pInvANetworkAnchor>>
  )
  AND hop_amount >= <<$pInvANetworkMinAmount>>
