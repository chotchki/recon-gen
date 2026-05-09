WITH base AS (
  SELECT
    *,
    CONCAT(e.source_account_name, ' (', e.source_account_id, ')') AS source_display,
    CONCAT(e.target_account_name, ' (', e.target_account_id, ')') AS target_display
  FROM spec_example_inv_money_trail_edges e
)
SELECT
  *
FROM base
WHERE
  (
    source_display = <<$pInvANetworkAnchor>> OR target_display = <<$pInvANetworkAnchor>>
  )
  AND hop_amount >= <<$pInvANetworkMinAmount>>
