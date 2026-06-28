# App Info

> **What this sheet teaches.** Dashboard health — whether the rendering pipeline is operational and whether the matviews feeding this dashboard are fresh. When a sheet elsewhere renders blank and you don't know why, App Info is the diagnostic ladder's first rung.

## What you're looking at

A single *Liveness* KPI tops the sheet — a count of user-visible database tables. Below sit three sections: a *Matview Status — sources this app reads from* table showing per-matview row counts and most-recent timestamps, a *Latest Balance Day* cell live-querying the most recent day the feed carries data for and a *Deploy Stamp* text box listing the software version, git commit hash, generation timestamp, SQL dialect, deployment name and as-of anchor baked at deploy time.

## How to read the numbers

The *Liveness* KPI runs a real query against the database's catalog (Postgres: `information_schema.tables` filtered to `public`; Oracle: `USER_TABLES`; DuckDB: `information_schema.tables` filtered to `main`). If the KPI shows a number, you know the renderer → database round-trip is working and the rendering pipeline is healthy. A blank KPI means the dashboard server itself cannot reach the data source — not a SQL issue, not a data issue.

The *Matview Status* table lists every [matview](../_glossary.md#matview--materialized-view) (a SQL view whose results are stored in a regular table and refreshed on demand) and base table this specific dashboard depends on, paired with:

- `view_name` — the fully-qualified table/matview name
- `row_count` — COUNT(*) as of the most recent ETL load. Zero means the ETL hasn't refreshed that matview yet; non-zero means data is present.
- `latest_date` — the MAX of the matview's natural date column (e.g., `business_day_end` for drift matviews, `posting` for transaction tables). NULL when the matview has no date dimension. Stale when `latest_date` falls behind the base tables' freshest date.

The *Deploy Stamp* text box carries metadata baked at dashboard generation time:
- `recon-gen` version (software build number)
- `git` short SHA (which source commit was deployed)
- `generated` ISO timestamp (when the dashboard was built)
- `dialect` (Postgres, Oracle or DuckDB; dev builds flag DuckDB explicitly)
- `prefix` (the deployment name carried on every table, e.g., `recon-prod`)
- `as_of (at emit)` (the calendar day the dashboards default to, baked at generate time)
- `as_of source` (where that day came from — pinned in YAML, pinned via env, derived from the feed's data anchor or a wall-clock fallback)
- `cadence` (internal entities by balance cadence — N sparse, M explicit_daily; present only when the app passes its L2 instance)

Each dashboard's App Info is isolated per-app scope by design — the L1 ([account-integrity](../_glossary.md#l1-dashboard--account-integrity-invariants)) dashboard reads ~12 matviews; the Executives dashboard reads 2 base tables. To assess total deployment freshness, check every app's App Info sheet.

## Common patterns

### Liveness KPI shows a number; other sheets render blank

The rendering pipeline is healthy. The blank sheet indicates a data or visual-binding problem, not a system failure. Check the *Matview Status* table for the matview feeding that sheet — if its `row_count` is zero, the SQL is dry and the ETL hasn't populated it yet. If `row_count` is positive, the issue is in the visual configuration on that sheet (column binding, filter pushdown or aggregation mismatch).

### Liveness KPI is blank

The dashboard server cannot execute the direct-query SQL against the database. This is a server-side infrastructure problem — check your network connectivity, the database credentials in your cfg's `demo_database_url` and whether the database is still reachable. Restart the dashboard server or refresh your browser.

### Matview row_count is zero; latest_date is NULL

The ETL hasn't run since the dashboard was generated. The matviews are initialized but empty. This is normal in a fresh deployment. Run `recon-gen data refresh --execute` to populate the matviews, then reload the dashboard.

### Matview row_count is positive; latest_date is stale

The matview was refreshed at some point but hasn't caught up to the most recent data load. Check the base tables' `latest_date` values (usually `<prefix>_transactions` and `<prefix>_daily_balances`) — if they are newer than this matview's `latest_date`, the matview hasn't been recomputed since the last ETL load. Run `recon-gen data refresh --execute` to refresh the matviews. Ad-hoc dashboard hits do NOT trigger a matview refresh; only ETL loads do.

### Base tables fresh; one matview stale

A single matview's `latest_date` lags the base tables while others stay current. The matview may have a SQL bug (infinite loop, missing WHERE clause, circular join). Check the database logs and the matview's SQL definition in `src/recon_gen/common/l2/schema.py` — look for `{matview_create_kw} {prefix}_<matview_name>` and verify the date-column logic and JOIN structure.

### latest_date across all matviews is NULL

A matview was registered with no date column, so its `latest_date` renders NULL by design. Every matview the bundled apps ship today carries a date column — the Investigation money-trail walk maps each edge to its target leg's `posted_at`, for example — so an all-NULL column points at a custom matview added without a freshness date. That matview is still fresh if `row_count > 0` and the last MANUAL refresh you ran is within a few hours.

## What "no rows" means

This sheet does not have an empty state — it always shows at least the *Liveness* KPI, the *Latest Balance Day* cell and the *Deploy Stamp*. The *Matview Status* table may be sparse (only the matviews this app reads), but it will not be blank. If you see zero rows in *Matview Status*, the dashboard was built before any matviews were declared (an app-initialization edge case) — check the `build_matview_status_dataset` call in the app's code to verify the `view_specs` list is non-empty.

## Cross-sheet drills

This sheet has no drill actions. It is a diagnostic endpoint, not a drill destination. Other sheets' drill actions do not land here.

## Related handbook pages

- [Daily Statement](../l1/daily-statement.md) — the canonical per-account narrative; use it when you've narrowed down to a specific account and day.
- [Drift](../l1/drift.md) — the L1 account-integrity violation sheet; the first place to look when stored balances disagree with postings.
- [Overdraft](../l1/overdraft.md) — internal accounts going negative; another L1 invariant orthogonal to drift.

---

*First time here? See the [Vocabulary](../_glossary.md) for `matview`, `L1` and the other project-specific terms.*
