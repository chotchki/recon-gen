# Program Health

> **What this sheet teaches.** A single-tile count of all open L1 ([account-integrity](../_glossary.md#l1-dashboard--account-integrity-invariants)) invariant violations in the selected date window, banded green / amber / red to flag whether you need to act now.

## What you're looking at

A single KPI tile dominates the sheet: *Open L1 Invariant Violations*, showing the count of all presently-open L1 violations. The number is wrapped in a threshold-banded indicator — **green** when the count is zero, **amber** on any violation (≥1) and **red** at ≥20 violations (the systemic mark). Below the tile sits a text box directing you to the L1 Dashboard for per-row drill and triage.

Both the count and the traffic-light banding respond to the dashboard's date-range filter (the *Date From* / *Date To* picker near the top). The 30-day window is the standard board-cadence review scope — wide enough to catch emerging patterns but narrow enough to reflect current health.

## How to read the numbers

The KPI feeds from the `<prefix>_l1_exceptions` [matview](../_glossary.md#matview--materialized-view), a UNION of all L1 invariant violation types: drift, ledger drift, overdraft, limit breach, expected end-of-day balance breach, balance cadence gap, stuck pending, stuck unbundled and the L2FT [chain](../_glossary.md#chain)-coherence checks.

The SQL counts only genuine violations — zero-magnitude rows (noise or resolved states) are filtered out. The date picker pushes down to the matview's `business_day` column, so the count obeys your window at query time.

The threshold banding is fixed: amber triggers at 1 (any violation), red triggers at 20 (systemic scope). These thresholds can't be customized per-institution in the current release.

## Common patterns

### Green (zero violations)

All internal [accounts](../_glossary.md#account) agree with their postings and all [transfers](../_glossary.md#transfer) chains fire their declared legs. This is the steady-state target. If you see green, your L1 foundations are solid — the board can scan other operational metrics without alarm. **Confirm freshness** on the App Info sheet's *Matview Status* table — each `latest_date` should be current (advanced by the last ETL load); a zero count from stale data is not a clean signal.

### Amber (1–19 violations)

At least one invariant has fired. The violation count is low enough that it's probably one broken account or one stuck transfer chain, not a feed-wide incident. **Drill into the L1 Dashboard** (link in the text box below the KPI) to see which `check_type` is active, which account or [rail](../_glossary.md#rail) and the specific violation's magnitude. Filter the L1 sheet by date and `check_type` to narrow scope — a single-account drift is a different remediation than a bank-wide limit-breach wave.

### Red (≥20 violations)

Systemic alarm. Either a major feed failed (e.g., the daily-balance import stopped, or a batch posting process re-ran with stale data) or the institution's L1 processes accumulated a backlog. **Act immediately**: cross to the L1 Dashboard, sort by `check_type` and `account_role` to understand the scope — if the same role dominates, it's feed-specific; if violations are scattered, the issue is structural. Loop in the relevant upstream system teams (the balance file owner, the ACH processor, etc.) with the scoped finding.

## What "no rows" means

Program Health never shows "no rows" — it always emits exactly one row, the count. But if you see a **zero count** on a green tile:

- **Confirm the matview is fresh.** Cross to *App Info* and read the *Matview Status* table — the Executives sheet monitors the `<prefix>_transactions` and `<prefix>_daily_balances` base tables Program Health derives from. If a base table's `latest_date` has advanced but the count hasn't moved, the rollup may be clean as of the last refresh but data-stale (the `<prefix>_l1_exceptions` matview's own `latest_date` shows on the L1 Dashboard's App Info). The institution refreshes matviews on every ETL load; ad-hoc dashboard hits do not trigger one.
- **Check the date filter.** A very narrow date window (e.g., a single day with no postings) shows zero correctly. Widen the window to the trailing 7 or 30 days; if violations still don't appear, the system is genuinely clean within that scope.
- **Don't assume all-clear.** Green Program Health means L1 violations are absent. L1 is the ledger layer; if *Account Coverage* shows low participation or *Transaction Volume* shows zero flows, those are operational issues that sit outside L1 scope — check those sheets too.

If *App Info* shows `latest_date` as null or the row count as zero across the board (not just this sheet's count), the ETL pipeline didn't run. That is an infrastructure alert, not a "clean" signal.

## Related handbook pages

- [L1 Dashboard — Drift](../l1/drift.md) — the per-account balance-agreement invariant; start here when drift is your dominant violation type.
- [Glossary](../_glossary.md#executives--program-health-scope) — definitions of `L1`, `matview`, `account_role`, `rail`, `chain` and the other project-specific terms.

---

*First time here? See the [Vocabulary](../_glossary.md) for `L1`, `matview`, `account_role`, `rail`, `chain` and the other project-specific terms.*
