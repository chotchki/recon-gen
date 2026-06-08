# CN.4 Phase B — fan-out findings log

52 agents (26 drafts + 26 adversarial fact-checks); 3M tokens; ~7 min wall.
**Stats:** 26 drafts | 9 approved | 17 flagged for revision.

## Approved pages (no revision needed)

- `docs/handbook/l1/drift-timelines.md`
**Severity totals:** 9 critical, 18 major, 9 minor.
- `docs/handbook/l1/daily-statement.md`
- `docs/handbook/investigation/fanout.md`
- `docs/handbook/investigation/anomalies.md`
- `docs/handbook/investigation/money-trail.md`
- `docs/handbook/investigation/account-network.md`
- `docs/handbook/executives/getting-started.md`
- `docs/handbook/executives/account-coverage.md`
- `docs/handbook/executives/transaction-volume.md`

## Flagged pages — findings by severity

Critical = factually wrong, operator would be misled.
Major = inaccurate detail or vocabulary discipline violation.
Minor = phrasing / cross-reference / completeness.

### `getting-started.md`
- **[major]** Source creates 12 sheets: 1 Getting Started discovery + 11 others (Drift, Drift Timelines, Overdraft, Limit Breach, Pending Aging, Unbundled Aging, Supersession Audit, L1 Exceptions, Daily Statement, Transactions, App Info). Draft claims nine but there are ten additional tabs.
- **[minor]** Not all tabs query one invariant directly. Drift Timelines is time-series derived; Daily Statement is per-account narrative derived; Transactions is raw ledger; L1 Exceptions UNIONs all invariants. Claim is inaccurate.
- **[minor]** Files daily-statement.md and app-info.md do not exist in /Users/chotchki/workspace/quicksight/docs/handbook/l1/. Only drift.md exists. These references cannot resolve.

### `overdraft.md`
- **[critical]** The `source` column does not exist in the dashboard dataset. While the source column is available in the underlying effective_balances matview and selected in the overdraft matview (schema.py line 2654), it was not included in the dataset SQL (datasets.py lines 881-888) that builds the dashboard view.
- **[major]** While business_day_end exists in the dataset contract, it is not displayed in the actual sheet table. The table in app.py (lines 988-995) includes only: account_id, account_name, account_role, account_parent_role, business_day_start (as date), and stored_balance.
- **[major]** This entire section assumes the source column is available and visible to users. Since the source column is not exposed in the dashboard dataset, users cannot see which rows are 'carried' vs 'emitted'. This diagnostic guidance is actionable only if the column is visible.

### `limit-breach.md`
- **[critical]** The matview schema definition shows that limit caps are no longer embedded inline at schema-emit time. Per Phase AW.4 (2026-05-23) in schema.py, caps are now read from the config table at runtime via LEFT JOIN to <prefix>_v_config_limit_schedules. The old CASE-branch inlining approach was removed.

### `pending-aging.md`
- **[major]** The Stuck Pending Detail table does not contain an `account_role` column. The actual table columns are: account_id, account_name, transfer_id, rail_name, amount_money, amount_direction, posting, stuck_pending_aging_bucket, max_pending_age_seconds, age_seconds. The account_role is available in the dataset but not rendered as a visible column in the detail table.

### `unbundled-aging.md`
- **[minor]** The draft omits the 'Transfer Type' filter that is actually present on the Unbundled Aging sheet alongside Account and Rail filters (per app.py lines 2198-2202 which defines all three dropdown filters).
- **[minor]** The implementation (app.py line 1436) uses `ds['transaction_id'].count()` which counts rows, not explicitly distinct transaction_ids. The word 'distinct' is not supported by the actual code implementation.

### `supersession-audit.md`
- **[major]** Made-up matview names. No matviews named `l1-supersession-transactions` or `l1-supersession-daily-balances` exist. The Supersession Audit datasets read directly from the BASE tables `<prefix>_transactions` and `<prefix>_daily_balances`, not from separate matviews. These base tables ARE listed in the l1_matview_specs (line 68-69 of datasets.py).
- **[minor]** Vocabulary violation / incorrect cross-reference. The Supersession Audit sheet audits rewrites (Inflight, BundleAssignment, TechnicalCorrection) and is not related to carry-forward / sparse-cadence patterns. This appears to be copy-paste error or incorrect documentation linking.

### `exceptions.md`
- **[critical]** The draft lists 12 distinct check types but claims 'ten invariant checks'. The _L1_CHECK_TYPE_VALUES enum in datasets.py contains only 8 items. The matview projects 12 distinct check_type values (missing 4 transfer-keyed checks from the enum).
- **[major]** App.py sheet description (line 378-383) documents '5 balance/numeric checks' not 6. balance_cadence_gap (CL.6) is treated separately, not part of the original 5-check balance group.
- **[major]** _L1_CHECK_TYPE_VALUES enum (datasets.py lines 134-143) does not include these 4 check types. The enum only has 8 values. These checks exist in matview UNION but are missing from the dropdown filter enum.

### `transactions.md`
- **[major]** The filter dropdown is titled 'Transfer Type', not 'Rail Type'. While the parameter filters the rail_name column, the UI label is 'Transfer Type' (app.py line 2271).
- **[major]** The list omits the transfer_completion column, which is included in TRANSACTIONS_CONTRACT (datasets.py line 550) and dataset SQL (line 1257).
- **[minor]** Confusing notation since Posting Ledger IS the Transactions sheet. The description misrepresents drill direction: drills go FROM other sheets INTO Transactions, not within it.

### `getting-started.md`
- **[major]** Transaction status values in Rails sheet are incorrect. The code shows transaction_status_values() returns ('Pending', 'Posted', 'Other'), not ('Pending', 'Posted', 'Failed'). The 'Other' value is a catch-all category for all non-Pending/Posted statuses.
- **[major]** Chains sheet completion status values are incorrect. The code in datasets.py shows _CHAIN_COMPLETION_STATUS_VALUES = ('Completed', 'Incomplete') only. The 'No Required Children' value was removed per X.1.j — validator rule C5 rejects all-optional chains at L2 load, so this state never occurs.
- **[major]** This repeats the Chains completion status error. The 'No Required Children' option does not exist per the source code.

### `rails.md`
- **[critical]** Metadata Value is a free-text input field, not a dropdown. Users type the literal metadata value; it's single-valued, not multi-valued. Code uses `sheet.add_parameter_text_field`, not dropdown.

### `chains.md`
- **[major]** The 'No Required Children' value never exists. SQL in build_chain_instances_dataset (datasets.py:1136-1140) only produces 'Completed' or 'Incomplete'. Code comment (datasets.py:1026-1028) states: 'The pre-X.1.j third branch No Required Children is gone — validator rule C5 rejects all-optional/no-XOR chains at L2 load'.
- **[minor]** The actual drill action name in app.py line 1250 is 'View in Chains (filter parent_chain_name to entity_a)', not just 'View in Chains'. The parenthetical is part of the user-visible drill menu label.

### `transfer-templates.md`
- **[minor]** Source code shows edge legend uses 'Template → <child rail>' language, not 'matched chain children' terminology. Also, orphans are NOT rendered as dashed links; only the node names have '(orphan)' suffix.

### `l2-exceptions.md`
- **[major]** Chain parents can be either Rails or TransferTemplates per the L2 schema (Chain.parent is an Identifier resolving to either). Describing it as 'parent rail' is factually inaccurate when parent could be a template.
- **[major]** L2 is used on first appearance ('All six L2 checks') without glossary-link expansion. Per vocabulary contract, L2 must be glossary-linked on first use.
- **[major]** L1 is used without glossary-link expansion on what appears to be its first substantive use. Per vocabulary contract, L1 must be glossary-linked on first use.

### `getting-started.md`
- **[critical]** Source code's text box (app.py:239-242) limits drilling to three sheets, not four. Also, no cross-app drills are implemented yet; drills are marked K.4.7 (future phase). Account Network has only same-sheet walk-the-flow drills.
- **[major]** Term 'chain' used without inline glossary link. Handbook template requires first-use expansion for project vocabulary. Footer glossary link is insufficient; terms must be linked at first mention.

### `program-health.md`
- **[critical]** Made-up drill destination — the source code defines no drill action on the KPI tile. The app.py explicitly states (line 470-475) that the cross-app drill is deferred: 'the design's CrossAppDrill primitive landed in the CF.X-infra commit but no consumer wires it yet — first-use is deferred to keep CF.2 MVP scope tight'. Only a TextBox link below the KPI provides navigation text.
- **[critical]** Made-up column name — the Matview Status dataset has column 'latest_date' (MAX of the data's date column), not 'last_refresh_at'. The column represents the most recent data date, not refresh timestamp. This semantic mismatch makes the handbook's guidance incorrect.

### `money-moved.md`
- **[critical]** The referenced Account Reconciliation app does not exist in the codebase. The codebase contains l1_dashboard, l2_flow_tracing, executives, and investigation apps only. This drill target is fabricated.
- **[critical]** No drill actions are implemented in the Money Moved sheet as of the current code (L.6.6). Per the code comments (line 16-17 in app.py), cross-app drills into AR Transactions are deferred to L.6.7 (a future commit). These drill destinations do not exist in the current source.
- **[minor]** The phrasing 'count of distinct transfer_id values' is technically inaccurate. The actual SQL is `COUNT(*)` applied to the `per_transfer` CTE which already groups by `(transfer_id, rail_name)`. Distinctness is implicit in the GROUP BY, not from COUNT(DISTINCT ...). For readers unfamiliar with SQL, this phrasing may imply a COUNT(DISTINCT) function that isn't actually used.

### `app-info.md`
- **[major]** L1 is used without first-use expansion and glossary link. Per template section 'Vocabulary discipline' (lines 94-110), L1 MUST expand inline and link to glossary on first use.
