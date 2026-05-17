# AA.A.1 — Dropdown control audit

**Date:** 2026-05-15. **Phase:** AA.A (dropdown multi → single-select flip).

## Why this audit exists

X.2.t.2 forced every dataset parameter dropdown into the **sentinel-guard pattern** (`'__sentinel__' IN (<<$pX>>) OR col IN (<<$pX>>)`) so that an unbounded-value-universe multi-select could survive AWS's 32-element `DataSetParameter.DefaultValues.StaticValues` cap. That pattern's footgun: there's no "select none" affordance in the QS multi-select widget and no "pick exactly one and clear the rest" gesture — operators have to deselect every other value individually. Operator workflow on these dashboards is **drill-to-one** 99% of the time; the multi-select shape is paying for affordances no one uses.

This phase flips the default to **single-select**, which collapses the sentinel-guard SQL to `('__sentinel__' = <<$pX>>) OR (col = <<$pX>>)` (1-element default, not a 32-cap concern) AND restores a one-click "pick this value" gesture. The flip is per-dropdown; some genuinely multi-valued workflows ("show me Pending AND Failed for status workflow analysis") stay multi-select as "compare-N keepers".

## Classification key

- **FLIP** → currently multi, will become single-select. Default action.
- **KEEPER** → stays multi-select. Operator workflow genuinely benefits from holding N values simultaneously.
- **DISCUSS** → unclear from code alone; my best read is one column, but user should ratify the call before AA.A.3.
- **(already single)** → no action; row included for completeness.

## L1 dashboard (8 sheets, 23 dropdowns)

| Sheet | Param | Column | Current | Sentinel? | Classification | Reasoning |
|---|---|---|---|---|---|---|
| Drift | `pL1DriftAccount` | `account_id` | multi | yes | **FLIP** | Account drilldown — one-at-a-time |
| Drift | `pL1DriftRole` | `account_role` | multi | no | **FLIP** | Role facet — scope to one category (CPA view) |
| Drift Timelines | `pL1DriftTlRole` | `account_role` | multi | no | **FLIP** | Same as Drift Role |
| Overdraft | `pL1OverdraftAccount` | `account_id` | multi | yes | **FLIP** | Account drilldown |
| Overdraft | `pL1OverdraftRole` | `account_role` | multi | no | **FLIP** | Role facet |
| Limit Breach | `pL1LimitBreachAccount` | `account_id` | multi | yes | **FLIP** | Account drilldown |
| Limit Breach | `pL1LimitBreachType` | `transfer_type` | multi | no | **FLIP** | Inspecting one limit shape at a time |
| Pending Aging | `pL1PendingAccount` | `account_id` | multi | yes | **FLIP** | Account drilldown |
| Pending Aging | `pL1PendingType` | `transfer_type` | multi | no | **FLIP** | One transfer type at a time |
| Pending Aging | `pL1PendingRail` | `rail_name` | multi | no | **FLIP** | One rail at a time |
| Unbundled Aging | `pL1UnbundledAccount` | `account_id` | multi | yes | **FLIP** | Account drilldown |
| Unbundled Aging | `pL1UnbundledType` | `transfer_type` | multi | no | **FLIP** | One type at a time |
| Unbundled Aging | `pL1UnbundledRail` | `rail_name` | multi | no | **FLIP** | One rail at a time |
| Supersession Audit | `pL1SupersedeReason` | `supersedes_reason` | multi | no | **FLIP** | Inspect one reason at a time |
| Today's Exceptions | `pL1TodaysExcCheckType` | `check_type` | multi | no | **DISCUSS** | Today's Exceptions is the cross-invariant landing sheet. "Show drift AND overdraft together" is a plausible compare-N workflow. Default: FLIP, but flag for ratification. |
| Today's Exceptions | `pL1TodaysExcAccount` | `account_id` | multi | yes | **FLIP** | Account drilldown |
| Today's Exceptions | `pL1TodaysExcType` | `transfer_type` | multi | no | **FLIP** | One type at a time |
| Transactions | `pL1TxAccount` | `account_id` | multi | yes | **FLIP** | Account drilldown |
| Transactions | `pL1TxTransferId` | `transfer_id` | multi | yes | **FLIP** | Drilling to one transfer |
| Transactions | `pL1TxStatus` | `status` | multi | yes | **DISCUSS** | Status-workflow analysis ("pending AND failed for stuck-flow triage") is a plausible compare-N. Default: FLIP, but flag. |
| Transactions | `pL1TxOrigin` | `origin` | multi | yes | **FLIP** | One origin at a time |
| Transactions | `pL1TxType` | `transfer_type` | multi | no | **FLIP** | One type at a time |
| Daily Statement | `pL1DsAccount` | `account_id` | single | — | (already single) | No action |

**L1 totals:** 21 flip, 2 discuss (`pL1TodaysExcCheckType`, `pL1TxStatus`), 1 already single. Source: `src/quicksight_gen/apps/l1_dashboard/app.py` lines 1779–2024.

## L2 Flow Tracing (4 sheets, 11 dropdowns)

| Sheet | Param | Column | Current | Sentinel? | Classification | Reasoning |
|---|---|---|---|---|---|---|
| Rails | `pL2ftRail` | `rail_name` | multi | no | **FLIP** | Operator picks one rail to investigate |
| Rails | `pL2ftStatus` | `status` | multi | no | **DISCUSS** | Status-workflow compare-N candidate. Default: FLIP, but flag. |
| Rails | `pL2ftBundle` | `bundle_status` | multi | no | **DISCUSS** | Bundle-status workflow compare-N candidate. Default: FLIP, but flag. |
| Rails | `pL2ftMetaKey` | `metadata_key` | single | — | (already single) | No action |
| Chains | `pL2ftChainsChain` | `parent_chain_name` | multi | no | **FLIP** | One chain at a time |
| Chains | `pL2ftChainsCompletion` | `completion_status` | multi | no | **DISCUSS** | Completion-workflow compare-N candidate. Default: FLIP, but flag. |
| Chains | `pL2ftChainsMetaKey` | `metadata_key` | single | — | (already single) | No action |
| Transfer Templates | `pL2ftTtTemplate` | `template_name` | multi | no | **FLIP** | One template at a time |
| Transfer Templates | `pL2ftTtCompletion` | `completion_status` | multi | no | **DISCUSS** | Same as Chains Completion |
| Transfer Templates | `pL2ftTtMetaKey` | `metadata_key` | single | — | (already single) | No action |
| L2 Exceptions | *(no dropdowns)* | — | — | — | — | KPI + bar + table only |

**L2FT totals:** 4 flip, 4 discuss (all status / completion / bundle-status workflow), 3 already single. Source: `src/quicksight_gen/apps/l2_flow_tracing/app.py` lines 634–1008.

## Investigation (5 sheets, 2 dropdowns)

| Sheet | Param | Column | Current | Sentinel? | Classification | Reasoning |
|---|---|---|---|---|---|---|
| Money Trail | `pInvMoneyTrailRoot` | `root_transfer_id` | single | — | (already single) | No action |
| Account Network | `pInvANetworkAnchor` | `account_id` | single | — | (already single) | No action |

**Inv totals:** 0 flip, 0 discuss, 2 already single. Source: `src/quicksight_gen/apps/investigation/app.py` lines 723–1002. The walk-the-flow / anchor-dropdown sheets were already single-select by design.

## DISCUSS rows — user ratified (2026-05-15)

All six DISCUSS rows resolved to **FLIP**. Both status workflows (`pL1TxStatus`, `pL2ftStatus`) and all four completion/bundle workflows (`pL2ftChainsCompletion`, `pL2ftTtCompletion`, `pL1TodaysExcCheckType`, `pL2ftBundle`) flip to single-select per the drill-to-one default. Total scope of AA.A.3: **31 dropdowns flipped** (25 default-FLIP + 6 ratified-DISCUSS), 5 unchanged (already single).

## Sequencing implication for AA.A.3

The mechanical change per flipped dropdown:

1. `multiselect=True` → `single_select=True` on `ParameterDropDownControl`
2. Dataset SQL `IN (<<$pX>>)` → `= <<$pX>>` (for fixed-enum columns) OR `('__sentinel__' = <<$pX>>) OR (col = <<$pX>>)` (for show-all-default columns)
3. Parameter declaration: scalar `StringParameter` instead of `StringParameterList`
4. `DataSetParameter.DefaultValues.StaticValues` → 1-element list (the sentinel for show-all, or the single default value)

The `app2_param_eq` helper already exists for single-value bind translation. Need to add `app2_param_eq_with_all_sentinel` for the show-all-default case. AA.A.2 covers this.
