# L1 Exceptions

> **What this sheet teaches.** The unified snapshot of every L1 ([account-integrity](../_glossary.md#l1-dashboard--account-integrity-invariants)) SHOULD-constraint violation across all ten invariant checks, scoped to the date window you're examining. This is your morning scan: one sheet that answers "what's broken across balance, chain flow, and aging" without hopping between five separate invariant sheets.

## What you're looking at

The sheet opens with a single KPI: *Open Exceptions* — the total count of violations in the selected date window. Below sits *Exceptions by Check Type* (a horizontal bar chart with log-scale Y axis) that groups the violations into ten categories so you can see which invariant is dominating. The detail table *Exception Detail* lists every single violation, sorted by dollar magnitude (largest first), so the highest-impact items surface at the top.

At the bottom is an *Institution Context* text box carrying your L2 instance's description — the unified-view landing page's anchor for "what this institution reconciles."

## How to read the numbers

The detail table draws from a live UNION ALL across ten L1 invariant matviews, each contributing its own `check_type` discriminator column. The union branches across three categories:

**Balance / numeric checks** (5 types; each row is one account-day violation):
- `drift` — leaf account's stored balance disagrees with cumulative net of postings; drawn from `<prefix>_drift` (see [Drift](drift.md))
- `ledger_drift` — parent account's stored balance does not equal the sum of its children's stored balances; from `<prefix>_ledger_drift`
- `overdraft` — stored balance is negative (`< 0`); from `<prefix>_overdraft`
- `limit_breach` — the net outbound or inbound flow on a rail exceeded the per-account cap for that direction; from `<prefix>_limit_breach` (see [Limit Breach](limit-breach.md))
- `expected_eod_balance_breach` — the stored balance disagrees with a declared expected EOD target

**Time-based aging checks** (2 types; each row is one leg, keyed by posting timestamp):
- `stuck_pending` — a Pending leg has exceeded its rail's `max_pending_age` cap
- `stuck_unbundled` — a Posted leg has exceeded its rail's `max_unbundled_age` cap and is not yet bundled

**Transfer-keyed chain / cardinality checks** (3 types; each row is one transfer's violation, not an account-day):
- `chain_parent_disagreement` — a child Transfer claims multiple parent Transfer IDs; the chain's parent linkage is ambiguous
- `xor_group_violation` — an XOR group's firing count is not exactly one (0 = missed, ≥2 = overlap)
- `fan_in_disagreement` — a fan-in child Transfer's parent count disagrees with the expected count

Every row carries `account_id`, `account_name`, `account_role`, and `business_day`. For money-based checks (balance and aging branches), `magnitude_amount` holds the dollar variance; for transfer-keyed checks, `magnitude_count` holds the cardinality disagreement (the count of unexpected parents, or firing/child count mismatch). Exactly one magnitude column is populated per row.

The *Open Exceptions* KPI counts all rows in the dataset within the selected date window and filter settings. The *Exceptions by Check Type* bar chart groups by `check_type` to show you the volume mix across the ten branches — useful for spotting which invariant is the current bottleneck.

## Common patterns

### Single check type dominating

The bar chart shows one or two check types (e.g., `stuck_unbundled`) as massive bars while the others barely register. This is the **steady-state shape** — one aging rail is perpetually slow on bundling, or one specific invariant has a known endemic issue. Filter the detail table by that `check_type` using the filter controls, then sort by `magnitude_amount` desc to find the worst offenders. Cross to the invariant's dedicated sheet (e.g., *Unbundled Aging* for `stuck_unbundled`) to drill into which rails / accounts are the hotbed.

### Transfer-keyed rows alongside account-day rows

The detail table mixes money rows (with `magnitude_amount` ≥ $0.01) and cardinality rows (with `magnitude_count` ≥ 1). The transfer-keyed checks (`chain_parent_disagreement`, `xor_group_violation`, `fan_in_disagreement`) have NULL on `account_id` and a count in `magnitude_count`. Account-day checks always have `account_id` and a dollar amount. This is expected — the sheet's design surfaces all ten invariants on one canvas, which means two different key-shapes. When triaging, focus on money checks first (they map to accounts / roles you know), then loop in the chain-analysis team for the transfer-keyed exceptions.

### Balance spike after a known outage

The bar chart shows a sudden wave in `drift` or `ledger_drift`, all on the same `business_day`, across many accounts of one `account_role`. This is a **batch feed failure** — a nightly balance load either re-ran with stale input or was skipped entirely. Drill into *Drift* sheet with the same account role filter to see which child accounts are involved; cross to *Daily Statement* for one account to confirm the posting timeline. If the postings landed but the balance never updated, escalate to the balance-emission team.

### Cardinality checks pointing at a single parent or template

Several rows with `check_type` in `('chain_parent_disagreement', 'xor_group_violation', 'fan_in_disagreement')` all show the same `rail_name` (or template name). This is a **transfer-chain template issue** — the L2 declaration for that template's leg sequence or parent linkage doesn't match what's actually posting. Right-click a row → *View Daily Statement for this account-day* (for account-day rows) or drill directly to *Transactions* to see the competing parent claims or fired rail set. Loop in the L2 chain-definition maintainer with the template name and a sample transfer ID.

## What "no rows" means

A clean L1 Exceptions sheet means *all ten* SHOULD-constraints held true on every account and every transfer within your date window. This is the steady-state target, not an edge case — exceptions exist to catch violations, not to trend metrics. If you see zero rows:

- **Confirm the matviews are fresh.** Cross to *App Info* and check the *Matview Status* table. If `l1_exceptions` shows `last_refresh_at` older than the most recent ETL load AND new postings landed since, the data may be clean *as of the last refresh* but stale. All L1 matviews refresh on every ETL load; ad-hoc dashboard hits don't trigger one.
- **Check the date filter.** A very narrow window (e.g., a single hour on a quiet weekend) can legitimately show zero exceptions. Widen to the trailing 7 days; if you still get zero, the system is clean across that span.
- **Don't assume all-clean.** Each of the ten invariants has a dedicated sheet; a clean Exceptions view does NOT rule out edge cases in one branch. If you're investigating a specific account or rail, cross to its invariant sheet and apply a tighter filter.

If *App Info* shows `last_refresh_at` as NULL across the board or the `l1_exceptions` row count as zero but you posted data recently, the ETL pipeline encountered an error. That's an ops alert, not a "system is clean" signal.

## Cross-sheet drills

- **Exception Detail table row (left-click on `account_id`) → Drift sheet** (filtering to that account). For account-day rows only. Use this when you want to see a single problematic account's full drift posture across the selected date range.
- **Exception Detail table row (right-click → View Daily Statement for this account-day) → Daily Statement** (filtered to that account-day). Lands you on the per-account-per-day narrative of every posting and balance event. Works only for account-day rows (balance, aging checks); transfer-keyed rows (chain/cardinality) show no account context.

## Related handbook pages

- [Drift](drift.md) — deep-dive into stored vs. computed balance disagreement; the most common balance-check violation.
- [Overdraft](overdraft.md) — the non-negative balance constraint; what it means when an internal account goes negative.
- [Limit Breach](limit-breach.md) — per-direction flow caps; transfers exceeding outbound or inbound limits per rail.
- [Pending Aging](pending-aging.md) — stuck Pending legs; transactions that didn't clear within their rail's age cap.
- [Unbundled Aging](unbundled-aging.md) — stuck Posted legs waiting for the aggregator; the highest-volume aging check.
- [Daily Statement](daily-statement.md) — per-account-per-day walk; the destination of the detail-row drill.

---

*First time here? See the [Vocabulary](../_glossary.md) for `L1`, `matview`, `account_role`, `rail`, `chain`, `template`, and the other project-specific terms.*
