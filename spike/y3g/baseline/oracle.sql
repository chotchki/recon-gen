SELECT qs_inner."ROOT_TRANSFER_ID" AS "root_transfer_id", qs_inner."TRANSFER_ID" AS "transfer_id", qs_inner."DEPTH" AS "depth", qs_inner."SOURCE_ACCOUNT_ID" AS "source_account_id", qs_inner."SOURCE_ACCOUNT_NAME" AS "source_account_name", qs_inner."SOURCE_ACCOUNT_TYPE" AS "source_account_type", qs_inner."TARGET_ACCOUNT_ID" AS "target_account_id", qs_inner."TARGET_ACCOUNT_NAME" AS "target_account_name", qs_inner."TARGET_ACCOUNT_TYPE" AS "target_account_type", qs_inner."HOP_AMOUNT" AS "hop_amount", qs_inner."POSTED_AT" AS "posted_at", qs_inner."TRANSFER_TYPE" AS "transfer_type", qs_inner."SOURCE_DISPLAY" AS "source_display", qs_inner."TARGET_DISPLAY" AS "target_display" FROM (
WITH base AS (
SELECT
    e.*,
    source_account_name || ' (' || source_account_id || ')' AS source_display,
    target_account_name || ' (' || target_account_id || ')' AS target_display
FROM spec_example_inv_money_trail_edges e
)
SELECT * FROM base
WHERE 1=1
  AND (
    source_display = <<$pInvANetworkAnchor>>
    OR target_display = <<$pInvANetworkAnchor>>
  )
  AND hop_amount >= <<$pInvANetworkMinAmount>>
) qs_inner