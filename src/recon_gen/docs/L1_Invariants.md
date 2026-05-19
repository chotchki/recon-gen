# L1 Invariants

The L1 SPEC declares a small set of SHOULD-constraints that any
healthy ledger feed must satisfy. The L1 library
(`common.l2.emit_schema`) materializes each constraint as a
prefixed PostgreSQL view; rows in any of these views ARE the
constraint violations. Healthy = empty.

This page is the authoritative reference for what each
`{{ l2_instance_name }}_*` view returns, the SHOULD-constraint motivation, and
what the matview surfaces against the canonical
demo seed.

## How the views are layered

```
base tables
  ├── {{ l2_instance_name }}_transactions
  └── {{ l2_instance_name }}_daily_balances
                  ↓
Current* matviews (M.1.5 — max-Entry-per-logical-key projection)
  ├── {{ l2_instance_name }}_current_transactions
  └── {{ l2_instance_name }}_current_daily_balances
                  ↓
Helper matviews (computed-balance derivation)
  ├── {{ l2_instance_name }}_computed_subledger_balance
  └── {{ l2_instance_name }}_computed_ledger_balance
                  ↓
L1 invariant matviews (the SHOULD-constraint surfaces)
  ├── {{ l2_instance_name }}_drift
  ├── {{ l2_instance_name }}_ledger_drift
  ├── {{ l2_instance_name }}_overdraft
  ├── {{ l2_instance_name }}_expected_eod_balance_breach
  ├── {{ l2_instance_name }}_limit_breach
  ├── {{ l2_instance_name }}_stuck_pending          (M.2b.8)
  └── {{ l2_instance_name }}_stuck_unbundled        (M.2b.9)
                  ↓
Dashboard-shape matviews (UI convenience)
  ├── {{ l2_instance_name }}_daily_statement_summary
  └── {{ l2_instance_name }}_todays_exceptions      (UNION over the 5 baseline L1s)
```

13 matviews total. Refresh contract: every batch insert into the
base tables MUST be followed by `refresh_matviews_sql(instance)`
to recompute every dependent matview in dependency order. The
refresh is deterministic — leaves first, helpers second, L1
invariants third, dashboard-shape last.

## The seven L1 SHOULD-constraints

### 1. `{{ l2_instance_name }}_drift` — Sub-ledger drift

> For every CurrentStoredBalance where `Account.Scope = Internal`
> and `¬IsParent(Account)`,
> `Drift(Account, BusinessDay)` SHOULD equal 0.

Each leaf-account day where the stored balance disagrees with the
cumulative net of every Posted Money record posted to that account
through the BusinessDay's end. The disagreement is the *drift*; a
non-zero value signals the feed diverged from the underlying
ledger.

**Columns:** `account_id`, `account_name`, `account_role`,
`account_parent_role`, `business_day_start`, `business_day_end`,
`stored_balance`, `computed_balance`, `drift`.

**What to do:** Diff the day's transactions for `account_id`
against the stored balance — the gap is missing or duplicated
postings on that account-day. Re-load the source feed for the
account-day and refresh matviews.

{% if vocab.fixture_name == "sasquatch_pr" %}
**the matview should surface:** `bigfoot-brews +$75` planted at
`days_ago=5` surfaces with `drift=75.00`.
{% endif %}

### 2. `{{ l2_instance_name }}_ledger_drift` — Parent-account roll-up drift

> For every CurrentStoredBalance where `Account.Scope = Internal`
> and `IsParent(Account)`,
> `LedgerDrift(Account, BusinessDay)` SHOULD equal 0.

Each parent-account day where the stored balance disagrees with
the sum of its child accounts' stored balances. Surfaces a child
posting that didn't roll up correctly to its parent.

**Columns:** same as `_drift` minus `account_parent_role`
(parents ARE the parents).

**What to do:** Sum the child accounts of `account_id` on
`business_day_start` and compare to the parent's stored balance.
The gap is a child posting that didn't propagate to the parent —
usually a missing FK in the feed. Fix the parent link upstream,
re-load, refresh.

### 3. `{{ l2_instance_name }}_overdraft` — Non-negative balance

> For every CurrentStoredBalance,
> `money` SHOULD be ≥ 0.

Each internal account day where the stored balance is negative.
External counterparties are excluded by construction (the
asymmetry is intentional: banks may legitimately overdraft *us*;
we MUST NOT overdraft *them*).

**Columns:** `account_id`, `account_name`, `account_role`,
`account_parent_role`, `business_day_start`, `business_day_end`,
`stored_balance`.

**What to do:** Trace `account_id`'s posting sequence on
`business_day_end` to find the over-debit — usually a missing
inbound credit or an over-issued debit. Reconcile against the
source system and post a correction.

{% if vocab.fixture_name == "sasquatch_pr" %}
**the matview should surface:** `sasquatch-sips -$1500` planted at
`days_ago=6` surfaces with `stored_balance=-1500.00`.
{% endif %}

### 4. `{{ l2_instance_name }}_expected_eod_balance_breach` — Expected EOD

> For every CurrentStoredBalance where `expected_eod_balance` is
> set, `money` SHOULD equal `expected_eod_balance`.

Each account day where the stored EOD balance differs from the
L2-declared expected EOD balance. Surfaces accounts that didn't
clean up to their expected zero / target by end-of-day.

**Columns:** `account_id`, `account_name`, `account_role`,
`business_day_start`, `business_day_end`, `stored_balance`,
`expected_eod_balance`, `variance`.

**What to do:** Compare `stored_balance` against
`expected_eod_balance` for the gap size — typically a delayed
posting that should have landed before EOD. Verify the
source-system posting time and re-time the posting if needed.

### 5. `{{ l2_instance_name }}_limit_breach` — Per-direction flow cap

> For every CurrentStoredBalance where `Limits` is set, for every
> `(Rail, limit, direction)` in `Limits`, for every child Account
> whose `Parent = this account`, when `direction = Outbound`
> `OutboundFlow(child, rail, businessDay)` SHOULD be ≤ `limit`;
> when `direction = Inbound` `InboundFlow(child, rail, businessDay)`
> SHOULD be ≤ `limit`.

Per-`(account, day, rail_name, direction)` cells where cumulative
flow exceeded the cap. Caps come from the L2 instance's
LimitSchedules and are inlined into the view as CASE branches at
schema-emit time. The matview emits one row per breach per
direction — the `direction` column distinguishes Outbound caps
(`amount_direction = 'Debit'` transactions) from Inbound caps
(`amount_direction = 'Credit'`, typical AML inbound-cap pattern).
Both directions can apply to the same `(parent, rail)` pair via
two LimitSchedules; the dashboard renders both on the Limit
Breach sheet, distinguished by the Direction column. (Z.B
2026-05-15: keyed on `rail_name` — previously `transfer_type`.
AB.1 2026-05-19: added `direction` column.)

**Columns:** `account_id`, `account_name`, `account_role`,
`account_parent_role`, `business_day`, `rail_name`, `direction`,
`flow_total`, `cap`.

**What to do:** Either the LimitSchedule cap is too low (raise it
after confirming the day's volume is legitimate) or an upstream
control failed. For Outbound breaches, audit the feed for
over-sent volume — downstream beneficiaries may be undercredited
until reconciled. For Inbound breaches, flag the source for
review per the AML / KYC policy that motivated the cap
(structuring, unexpected deposits, counterparty source
diligence).

{% if vocab.fixture_name == "sasquatch_pr" %}
**the matview should surface:** `big-meadow-dairy $22k wire`
planted at `days_ago=4` surfaces with `outbound_total > cap` for
`rail_name='CustomerOutboundWire'`.
{% endif %}

### 6. `{{ l2_instance_name }}_stuck_pending` — Per-rail pending aging (M.2b.8)

> For every Rail with `max_pending_age` set, every Transaction
> on that rail SHOULD transition `Pending → Posted` before
> `posting + max_pending_age`.

Transactions stuck in `status='Pending'` past their rail's
configured cap. Caps come from the per-Rail `max_pending_age`
field and are inlined as CASE branches keyed on `rail_name`. Rails
without an aging watch contribute no branch and are excluded.

**Columns:** `transaction_id`, `account_id`, `account_name`,
`account_role`, `account_parent_role`, `transfer_id`,
`rail_name`, `amount_money`, `amount_direction`,
`posting`, `max_pending_age_seconds`, `age_seconds`.

**What to do:** Either re-poke the source-system integration to
transition the transaction, or raise the rail's `max_pending_age`
if the cap is too aggressive for normal volume. Escalate to ops
when `age_seconds` is in days rather than hours.

{% if vocab.fixture_name == "sasquatch_pr" %}
**the matview should surface:** `bigfoot-brews ACH at days_ago=2`
(172800s) surfaces with `age_seconds > max_pending_age_seconds`
(86400s for the `CustomerInboundACH` rail's PT24H cap).
{% endif %}

### 7. `{{ l2_instance_name }}_stuck_unbundled` — Per-rail unbundled aging (M.2b.9)

> For every Rail with `max_unbundled_age` set, every Posted leg
> on that rail SHOULD be picked up by an AggregatingRail
> (`bundle_id` set) before `posting + max_unbundled_age`.

Posted transactions whose `bundle_id IS NULL` past their rail's
cap. Per validator R8, `max_unbundled_age` is only meaningful on
rails appearing in some AggregatingRail's `bundles_activity`.

**Columns:** same shape as `_stuck_pending` with
`max_unbundled_age_seconds` instead of `max_pending_age_seconds`.

**What to do:** Verify the AggregatingRail's `bundles_activity`
still names this rail — the bundler may be silently
mis-configured. For high `age_seconds`, run the bundle process
manually and investigate why the regular cycle missed it.

{% if vocab.fixture_name == "sasquatch_pr" %}
**the matview should surface:** `sasquatch-sips fee accrual at
days_ago=35` surfaces with `age_seconds > max_unbundled_age_seconds`
(2,678,400s for the `CustomerFeeAccrual` rail's P31D cap).
{% endif %}

### 8. `{{ l2_instance_name }}_chain_parent_disagreement` — Two-template chain Parent disagreement (AB.2.3)

> For every two-template chain (chain.children resolves to a
> TransferTemplate), every leg_rail firing of one child Transfer
> SHOULD agree on which parent firing it descends from
> (first-firing-wins per gap doc §3).

A child Transfer where leg_rail firings claim **different**
`parent_transfer_id` values surfaces here. The pattern usually means
an ETL bug: stale parent reference, cross-cycle contamination, or a
race where two parent firings both wrote into the same downstream
template Transfer.

The matview groups by `(transfer_id, template_name)` over
`<prefix>_current_transactions` and gates on
`COUNT(DISTINCT transfer_parent_id) > 1`. Filters keep rail-as-child
chains out (`template_name IS NOT NULL`) and exclude `Failed` legs
(metadata is unreliable on failures).

**Columns:** `transfer_id`, `child_template_name`, `business_day`
(MIN of conflicting leg postings), `distinct_parent_count` (the
cardinality, ≥2 = violation), `parent_transfer_id_min` /
`parent_transfer_id_max` (sample conflicting values for analyst drill
without re-running the GROUP BY).

**What to do:** Identify which leg_rails of the child template wrote
the conflicting `parent_transfer_id` values — usually the bug is
upstream of the matview (in the ETL adapter that assigns
`parent_transfer_id` from a stale reference). Compare the two parents
by drilling into their respective transfer_ids on the Transactions
sheet; one should be the correct first-firing parent and the other a
contamination from an unrelated cycle. Fix the ETL's parent-resolution
logic and re-run the affected child Transfer.

{% if vocab.fixture_name == "sasquatch_pr" %}
**the matview should surface:** a planted `ChainParentDisagreementPlant`
on `InternalTransferCycle` (chain child of `CustomerFeeAccrual`) at
days_ago=1 — two leg rows share one child_transfer_id but carry
different parent_transfer_ids ("tr-cpd-parent-a-0001" vs
"tr-cpd-parent-b-0001").
{% endif %}

### 9. `{{ l2_instance_name }}_xor_group_violation` — Multi-mode template variant XOR violation (AB.3.3)

> For every TransferTemplate that declares `leg_rail_xor_groups`,
> for every group in that template, exactly ONE member of the
> group SHOULD fire per Transfer (the variants compete; the
> runtime picks one per cycle).

A Transfer where a group has 0 firings (missed — the template
fired but no variant did, the cycle didn't close) or ≥2 firings
(overlap — the runtime double-posted competing variants) surfaces
here. The pattern usually means an ETL bug: a misrouted variant
trigger, a re-post that didn't suppress the original, or a race
where two variants both wrote the closure.

The matview's CTE inlines `(template_name, xor_group_index,
member_rail_name)` rows from the L2 yaml's `leg_rail_xor_groups`
declarations, LEFT JOINs them against
`<prefix>_current_transactions` per (Transfer-of-template,
group-of-template, member-rail), and gates on
`COUNT(tx.transfer_id) <> 1`. When no template declares
`leg_rail_xor_groups`, the matview short-circuits with
`WHERE 1=0` (parses cleanly, zero rows).

**Columns:** `transfer_id`, `template_name`, `xor_group_index`
(0-based), `firing_count` (0 = missed; ≥2 = overlap),
`fired_rails` (dialect-specific concat of the rail_names that
DID fire; empty string when `firing_count=0`), `business_day`
(MIN of the firing legs' postings, or NULL for missed firings).

**What to do:** Identify which template firing this is, then
look at the upstream variant trigger. For missed firings (count=0):
the template fired but no variant trigger ran — usually a routing
bug where the runtime resolved to no variant. For overlaps (count≥2):
two variant triggers both fired — usually a re-post that didn't
suppress the original, or a race condition between two variant
selection paths. Drill from the row to the Transactions sheet to
see every leg of the Transfer, including the non-variant legs
that DID fire (so you know which template firing the violation
belongs to).

{% if vocab.fixture_name == "sasquatch_pr" %}
**the matview should surface:** a planted `XorVariantMissedFiringPlant`
on `MerchantSettlementCycle` (group 0, settlement-timing) at
days_ago=2 — one witness leg fires but no member of the
`[SettlementAutoSettle, SettlementStandardSettle, SettlementSlowSettle]`
group did. And a planted `XorVariantOverlapPlant` on the same
template at days_ago=1 — both `SettlementAutoSettle` and
`SettlementStandardSettle` fired for the same Transfer (1 minute
apart for visual separability).
{% endif %}

### 10. `{{ l2_instance_name }}_fan_in_disagreement` — Fan-in chain parent-set mismatch (AB.4.7)

> For every chain child entry declaring `fan_in: true` (AB.6
> per-child shape), every child Transfer's contributing parent set
> SHOULD match the entry's `expected_parent_count` (when set), or
> have cardinality ≥2 (when unset).

A child Transfer where the contributing parent set deviates from
the chain's expected count surfaces here. The pattern usually
means an ETL bug: a daily contribution never landed (the batch is
short of contributors), or a stale / foreign parent reference
claimed batch membership it shouldn't have (cross-batch
contamination).

The matview's CTE inlines the L2 chain config rows
`(chain_parent_name, child_template_name, expected_parent_count)`,
joins them against the upstream `<prefix>_transfer_parents`
matview's per-(child, parent) DISTINCT projection, and emits a
row when:

- `expected_parent_count IS NULL AND parent_count < 2` →
  `disagreement_kind='orphan'` (variable-batch flow's fallback —
  the chain declared no upper bound so the matview can only flag
  the degenerate single-parent case);
- `expected_parent_count IS NOT NULL AND parent_count < expected` →
  `disagreement_kind='missing'`;
- `expected_parent_count IS NOT NULL AND parent_count > expected` →
  `disagreement_kind='extra'`.

When no chain child declares `fan_in: true`, the matview
short-circuits with `WHERE 1=0` (parses cleanly across all 3
dialects, zero rows).

**Columns:** `child_transfer_id`, `chain_parent_name`,
`child_template_name`, `parent_count` (actual contributing
parents), `expected_parent_count` (NULL when chain leaves it
unset), `disagreement_kind` ('orphan' / 'missing' / 'extra'),
`business_day` (earliest contributing leg's posting day).

**What to do:** Identify which batch this is via the `business_day`
+ `child_template_name`. For 'missing': the upstream parent rail
should have fired N times but one (or more) firings never landed —
look at the parent rail's firing log for the batch period and find
the missing date(s). For 'extra': a parent firing claimed membership
in this batch it shouldn't have — could be a stale `parent_transfer_id`
metadata value (ETL pulled from a wrong source) or a duplicate
re-post. For 'orphan': only one parent contributed — either the
chain shouldn't be `fan_in` (it's actually 1:1) OR the operator
should set `expected_parent_count` so the matview can flag the
missing siblings.

{% if vocab.fixture_name == "sasquatch_pr" %}
**the matview should surface:** 3 planted batches on
`MerchantWeeklyPayoutBatch` (chain child of
`MerchantDailySettleAggregator`, `expected_parent_count=5`):
healthy (5 parents, days_ago=5, no row), missing-parent (4 parents,
days_ago=4, `disagreement_kind='missing'`), extra-parent (6 parents,
days_ago=3, `disagreement_kind='extra'`).
{% endif %}

## Diagnostic surface — Supersession Audit

`{{ l2_instance_name }}_supersession_*` is **not** a SHOULD-constraint — it's a
diagnostic view that surfaces logical keys with multiple `entry`
versions (the audit trail for `TechnicalCorrection` /
`BundleAssignment` / `Inflight` rewrites). Reads from BASE tables
(not Current*) since Current* hides superseded entries by
construction. See M.2b.12 dashboard for the visualization.

**What to do:** Diagnostic only — supersession is expected for
normal corrections. Investigate when `entry_count` is unusually
high for the row's age, or when the rewrite reason is missing.
Diff the entries to see what actually changed across versions.

{% if vocab.fixture_name == "sasquatch_pr" %}
the matview should surface a planted TechnicalCorrection on
`bigfoot-brews` (2 entries on the same logical id at `days_ago=3`)
surfaces with `entry_count > 1`.
{% endif %}

## Refresh + extend contracts

- **Refresh:** `refresh_matviews_sql(instance)` returns a single
  SQL string with 26 statements (13 REFRESH + 13 ANALYZE) in
  dependency order. Caller splits on `;` and executes
  per-statement (psycopg2's `cursor.execute` can't run multiple
  statements separated by `;` reliably).
- **Per-instance hot-path indexes:** every L1 invariant matview
  ships indexes on the dashboard's filter dropdowns
  (`account_id`, `rail_name`, `business_day`, etc) so the deployed
  dashboard's per-visual SELECTs hit indexed lookups.
- **Adding a new SHOULD-constraint:** declare the underlying L1
  primitive in the SPEC, add the matview to
  `common.l2.schema._L1_INVARIANT_VIEWS_TEMPLATE`, register it in
  `_L1_INVARIANT_VIEWS_DROPS_TEMPLATE` + `refresh_matviews_sql`,
  and write a `_render_<name>_cases` helper if it needs L2 data
  inlined at schema-emit time (mirror of
  `_render_pending_age_cases`). Then surface it via a new dataset
  + sheet on the L1 dashboard.

## See also

- [Schema v6 — Data Feed Contract](Schema_v6.md) — the column
  contract for the two base tables.
- [L1 Reconciliation Dashboard](handbook/l1.md) — the analyst's
  view of these invariants.
- [Customization Handbook — L2-fed pattern](handbook/customization.md#the-l2-fed-pattern-m2b)
  — how to point the dashboard at your own L2 instance.
