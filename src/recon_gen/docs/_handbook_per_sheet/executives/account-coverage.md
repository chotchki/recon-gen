# Account Coverage

> **What this sheet teaches.** Your account roster — which accounts you have on the books and which ones have actually moved money in the selected period. It answers whether your account base is healthy (mostly active) or bloated (many idle accounts taking up risk slots without producing any flow).

## What you're looking at

You're starting at two KPIs across the top — *Total Open Accounts* (left) and *Active Accounts (this window)* (right). The gap between them tells the story: if they're equal, every account in your book transacted; if active is much smaller, you have idle accounts worth investigating. Below the KPIs sit two horizontal bar charts side by side: *Open Accounts by Type* (left) and *Active Accounts by Type* (right), both broken down by `account_type` so you can see the shape of your deposit base next to the operational GL control accounts. At the bottom, a detail table called *Account Detail* lists every account with its last activity date and total transaction-leg count, sorted by activity in descending order — the busiest accounts surface first.

## How to read the numbers

The sheet reads from two datasets built over the shared `<prefix>_transactions` and `<prefix>_daily_balances` base tables (not matviews — the Executives app runs on raw transaction flow and daily-balance snapshots).

**Total Open Accounts** counts every unique `account_id` that has ever appeared in `daily_balances`, regardless of window. This is your all-time account count. The `activity_count` for each account is the total number of Posted transaction legs (status='Posted') ever recorded against that account, not just in the window — so an account with no recent activity but historical postings still shows its historical count.

**Active Accounts (this window)** narrows to accounts with at least one Posted leg in the selected date range. The date filter (`pExecDateStart` and `pExecDateEnd`) pushes into the dataset SQL as `WHERE t.posting BETWEEN <start> AND <end+1 day>`. Only accounts passing the `COALESCE(activity_count, 0) > 0` predicate appear in this count, so a zero-activity account (no legs in the window) never surfaces here.

Both bars aggregate counts by `account_type` (which maps to the `account_role` field in `daily_balances`) — so you see CustomerDDA, MerchantDDA, GL control, settlement and other role types side by side. The *Open* bar shows the all-time type distribution; the *Active* bar shows which types are moving money in the window.

The *Account Detail* table shows one row per account in the dataset (the base, date-independent snapshot), with columns:
- `account_id` — the ledger key
- `account_name` — human label
- `account_type` — the role (from `account_role` in `daily_balances`)
- `last_activity_date` — the most recent `posting` timestamp (converted to date grain) across ALL postings for that account
- `activity_count` — total Posted leg count across the entire history

The table is sorted descending by `activity_count`, so your highest-velocity accounts appear at the top.

## Common patterns

### Equality: open = active

Total Open Accounts and Active Accounts (this window) show the same number. Every account you have on the books moved money this period. This is operationally healthy if the window is long (30 days) — idle accounts in your active set suggest low utilization or accounts worth sunsetting.

### Large gap: open >> active

You have many more open accounts than active ones. Scan the detail table sorted by activity count — you'll see clusters of accounts with `activity_count = 0` or very small counts at the bottom. This is normal for a maturing book (old pilot accounts, closed customer slots still in the system awaiting archival). Drill into the bars by type: if all the idle accounts are in one type (e.g., test merchants, archive GLs), that's a known pattern; if idle accounts span types, loop in data governance about retention policy.

### Type imbalance: open distribution ≠ active distribution

The *Open Accounts by Type* and *Active Accounts by Type* bars show different shapes. For example, open has many CustomerDDAs and few GL controls, but active shows the opposite — mostly GL flows, few customer DDAs. This tells you which account families are actually moving money vs which are mostly static. Use the detail table's `account_type` filter to zoom into one type and see which accounts (by name/id) are dragging the average.

### All zeros in activity_count

The detail table shows `activity_count = 0` for every row. Either the date window is too narrow (no postings on those days) or the `<prefix>_transactions` table is stale/empty. Cross to the *App Info* sheet and check the `<prefix>_transactions` row — if `row_count` is zero, no transactions have landed; if it's positive but `latest_date` is lagging the base tables, the Executives datasets are reading stale snapshots.

## What "no rows" means

A clean Account Detail table (zero rows after applying filters) is rare because you almost always have accounts that exist; it means either:

- The date filter is so narrow (single day with no activity on any account) that the active-only variant's `COALESCE(activity_count, 0) > 0` predicate excludes everything. Widen the window to the last 7 or 30 days.
- The `<prefix>_daily_balances` table is empty or stale. Cross to *App Info* and check `<prefix>_daily_balances` `row_count` and `latest_date`.

If you see zero rows for Total Open Accounts + Active Accounts KPIs, the situation is critical: the daily-balances snapshot is missing. That's an ETL alert, not a data-clean signal.

## Cross-sheet drills

No cross-sheet drills are defined on this sheet in the current release. The Account Coverage sheet is a summary dashboard — to investigate specific accounts, use the detail table's sort/filter to find them by name or id, then navigate to the L1 Dashboard (Account Reconciliation's operational sheets) to see their full posting history.

## Related handbook pages

- [Program Health](program-health.md) — the top-level tripwire counting L1 invariant violations. Use when you want to know if anything is structurally broken in your ledger.
- [Getting Started](getting-started.md) — the landing page describing all four Executives sheets and their drilling patterns.

---

*First time here? See the [Vocabulary](../_glossary.md) for terms like `account_type`, `matview`, and the difference between [internal and external scope](../_glossary.md#account-scope-internal-vs-external).*
