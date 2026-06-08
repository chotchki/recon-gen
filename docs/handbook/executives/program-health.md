# Program Health

> **What this sheet teaches.** Your ledger integrity status at a glance — a single-tile indicator that counts all open L1 ([account-integrity](../_glossary.md#l1-dashboard--account-integrity-invariants)) invariant violations in the selected date window, with traffic-light thresholds (green / amber / red) to signal whether immediate action is required.

## What you're looking at

A single KPI tile dominates the sheet: *Open L1 Invariant Violations*, showing the count of all presently-open L1 violations. The number is wrapped in a threshold-banded indicator — **green** when the count is zero, **amber** on any violation (≥1), and **red** when the system has ≥20 violations (the systemic mark). Below the tile sits a text box directing you to the L1 Dashboard for per-row drill and triage.

Both the count and the traffic-light banding respond to the dashboard's date-range filter (the *Date From* / *Date To* picker near the top). The 30-day window is the standard board-cadence review scope — wide enough to catch emerging patterns but narrow enough to reflect current health status.

## How to read the numbers

The KPI feeds from the `<prefix>_l1_exceptions` [matview](../_glossary.md#matview--materialized-view), which is a master UNION of all L1 invariant violation types: `drift`, `ledger_drift`, `overdraft`, `limit_breach`, `expected_eod_balance_breach`, `balance_cadence_gap`, `stuck_pending`, `stuck_unbundled`, and the L2FT [chain](../_glossary.md#chain)-coherence checks (`chain_parent_disagreement`, `xor_group_violation`, `fan_in_disagreement`, `multi_xor_violation`).

The SQL counts rows where `((magnitude_amount IS NOT NULL AND magnitude_amount > 0) OR (magnitude_count IS NOT NULL AND magnitude_count > 0))` — filtering out zero-magnitude entries that represent noise or resolved states. The `magnitude_amount` column carries the dollar value of account-bound violations (drift, overdraft); `magnitude_count` carries the leg count on transfer-bound violations (chain parent disputes). The OR predicate ensures both columns participate: account-keyed rows use the magnitude-amount side; transfer-keyed rows use magnitude-count.

The date filter pushes down via `<<$pExecDateStart>>` and `<<$pExecDateEnd>>` over the `business_day` column, so the count obeys your date-range selection at query time.

The threshold banding is fixed in v0: amber triggers at 1 (any violation), red triggers at 20 (systemic scope). These thresholds are locked; they cannot be customized per-institution in the current release.

## Common patterns

### Green (zero violations)

All internal [accounts](../_glossary.md#account) agree with their postings and all [transfer](../_glossary.md#transfer) chains fire their declared legs. This is the steady-state target. If you see green, your L1 foundations are solid — the board can scan other operational metrics without alarm. **Confirm freshness** by checking the App Info sheet's *Matview Status* table to ensure the last refresh is recent (within the last ETL cycle); a zero count from stale data is not a clean signal.

### Amber (1–19 violations)

At least one invariant has fired. The violation count is low enough that it's probably one broken account or one stuck transfer chain, not a feed-wide incident. **Drill into the L1 Dashboard** (link below the KPI) to see which `check_type` is active, which account or [rail](../_glossary.md#rail), and the specific violation's magnitude. Filter the L1 sheet by date and `check_type` to narrow scope — a single-account drift is a different remediation than a bank-wide limit-breach wave.

### Red (≥20 violations)

Systemic alarm. Either a major feed failed (e.g., the daily-balance import stopped, or a batch [posting](https://en.wikipedia.org/wiki/Posting_(accounting)) process re-ran with stale data) or the institution's L1 processes accumulated a backlog. **Act immediately**: cross to the L1 Dashboard, sort by `check_type` and `account_role` to understand the scope — if the same role dominates, it's feed-specific; if violations are scattered, the issue is structural. Loop in the relevant upstream system teams (the balance file owner, the [ACH](https://en.wikipedia.org/wiki/Automated_Clearing_House) processor, etc.) with the scoped finding.

## What "no rows" means

Program Health never shows "no rows" — it always emits exactly one row, the count. But if you see a **zero count** on a green tile:

- **Confirm the matview is fresh.** Cross to *App Info* and check the *Matview Status* table's `<prefix>_l1_exceptions` row. If `last_refresh_at` is more than a few minutes old AND new postings landed since, the count may be clean *as of the last refresh* but data-stale. The institution refreshes matviews on every ETL load; ad-hoc dashboard hits do not trigger one.
- **Check the date filter.** A very narrow date window (e.g., a single day with no postings) shows zero correctly. Widen the window to the trailing 7 or 30 days; if violations still don't appear, the system is genuinely clean within that scope.
- **Don't assume all-clear.** Green Program Health means *L1* violations are absent. L1 is the ledger layer; if Account Coverage shows low participation or the Transaction Volume shows zero flows, those are operational issues that sit outside L1 scope — check those sheets too.

If *App Info* shows `last_refresh_at` as null or the matview row count as zero across the board (not just this sheet's count), the L1 invariant pipeline didn't run. That is an infrastructure alert, not a "clean" signal.

## Cross-sheet drills

- **KPI tile → L1 Dashboard** (click the title text). Opens the account-integrity invariants dashboard with your current date-range filter pre-applied, landing on the *L1 Exceptions* sheet which lists every violation by `check_type`, `account_role`, and `rail_name`. Use that sheet's drill actions to narrow to a specific account's *Daily Statement* or a specific [transfer](../_glossary.md#transfer)'s *Transactions* detail.

## Related handbook pages

- [L1 Dashboard — Drift](../l1/drift.md) — the foundational per-account balance-agreement invariant; starts here when drift is your dominant violation type.
- [Glossary](../_glossary.md#executives--program-health-scope) — definitions of `L1`, `matview`, `account_role`, `rail`, `chain`, and the other project-specific terms.

---

*First time here? See the [Vocabulary](../_glossary.md) for `L1`, `matview`, `account_role`, `rail`, `chain`, and the other project-specific terms.*
