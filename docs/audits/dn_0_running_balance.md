# DN.0 — Running balance on Daily Statement (scoping)

**Date:** 2026-06-15
**Phase:** DN.0
**Status:** Scoping doc. Open questions flagged inline; operator confirm needed before DN.3 (starting-balance UX choice) and DN.4 (audit PDF extension) start. Ready for DN.1 (contract update + dataset SQL) as soon as the cross-dialect SQL shape locks below clears review.

## Problem statement

Daily Statement's "Posted Money Records" Table visual (`apps/l1_dashboard/app.py:1986-2025`) currently shows posted legs as discrete rows — one row per money record, columns `transaction_id` / `transfer_id` / `rail_name` / `amount_money` / `amount_direction` / `status` / `origin` / `posting`. The operator triaging a drift / reconciliation question against a chosen account-day can read each leg's amount + direction but cannot read **where the account's balance sits after each leg posts**. To find the moment a balance crossed zero (or the moment a drift started accumulating) the operator currently has to do mental arithmetic across the visible rows.

Running balance = the cumulative signed `amount_money` summed in posting order, per `(account_id, business_day)`. It is the standard banking-statement column shape — every CPA reading the dashboard expects it there.

## What this delivers

A new `running_balance` column on the Posted Money Records visual, visible on **both QS and App2**. The column is a per-row window aggregation over the existing per-leg projection — same SQL on both renderers, no per-renderer divergence. Optional sub-feature (see § Starting balance): a small badge or synthetic opening row that anchors the first running-balance value to the account's actual prior end-of-day balance instead of 0.

**Parity feature, not a symmetry break.** Unlike DM (App2-only Role cascade + day-availability decoration), DN is a row-shape change that both renderers can ingest identically — `[[project_audit_dashboard_agreement]]` applies in full force. QS / App2 / direct-DB / PDF must agree row-for-row on `running_balance`.

## Decision: window function, no matview

**Recommendation: SQL window function in the existing Daily Statement transactions dataset. No new matview.** Document the decision plus the reversibility (matview can be added in a follow-up phase if perf surfaces an issue).

Rationale:

- **Portability.** `SUM(amount_money) OVER (PARTITION BY account_id, business_day ORDER BY posting ROWS UNBOUNDED PRECEDING)` is in the SQL standard window-function surface that PG 17+, Oracle 19c, and DuckDB all support. The codebase already uses cross-dialect window functions in three places: `<prefix>_daily_statement_summary` matview emit (`LAG(eb.effective_money) OVER (PARTITION BY account_id ORDER BY business_day_start)` — `common/l2/schema.py:3579`), the supersession surface (`COUNT(*) OVER (PARTITION BY id)`), and the investigation anomaly z-score (`AVG / STDDEV_SAMP OVER (PARTITION BY pair)`). The pattern is exercised on every dialect under the chain. Note Schema_v6's "no named `WINDOW w AS` clause" caveat — inline the `OVER (…)` definition; that constraint costs nothing here since we have exactly one window expression.
- **Cardinality.** Daily Statement is narrowed to **one (account, business_day)** by the existing `pL1DsAccount` + `pL1DsBalanceDate` dataset parameters (`apps/l1_dashboard/datasets.py:1339-1342`). After the WHERE clause the row set is the per-leg activity for a single account-day — typically <500 rows, usually <50. A per-row window aggregation is O(rows in partition); on the post-WHERE result set this is negligible. The window is computed against the already-narrowed set, not against the full matview.
- **Refresh coupling.** Adding a matview means another refresh dependency in `refresh_matviews_sql` and another emit branch in `common/l2/schema.py`. The window-function path adds zero schema surface — the new column is a SELECT-list projection on an existing dataset.
- **Reversibility.** If at some future point a use case wants the cumulative running balance across multiple days for an account (a multi-day "balance evolution" chart), a matview becomes the right shape because the partition cardinality grows. That phase can add `<prefix>_daily_statement_running_balance` keyed `(account_id, posting)` with the running balance precomputed. The decision to start with a window function does not foreclose that path; it defers it until there is a use case.

**Rejected: matview at DN.1.** Would add a third "daily statement" matview alongside `daily_statement_summary` and the `current_transactions` matview the SELECT already reads from. Premature given the per-account-day cardinality of the visual.

## Contract change

`DAILY_STATEMENT_TRANSACTIONS_CONTRACT` (`apps/l1_dashboard/datasets.py:571-591`) gains one column:

```python
ColumnSpec("running_balance", "DECIMAL"),
```

Currency-formatted via `currency=True` on the visual column wrapper (per `[[feedback_kpi_currency_decimals_strict]]` and the standing rule for money measures). The column is placed in the visual between `amount_direction` and `status` — adjacent to the per-row amount so the operator's eye reads "this leg moved $X, balance is now $Y" in one fixation.

`_daily_statement_transactions_sql` (`apps/l1_dashboard/datasets.py:1298-1343`) gets one SELECT-list addition. Sketched shape (post-CTE, pre-WHERE — actual diff lands in DN.1):

```sql
SELECT tx.id AS transaction_id,
       tx.account_id, tx.account_name,
       {business_day} AS business_day,
       tx.posting,
       tx.transfer_id, tx.rail_name,
       {amount} AS amount_money, tx.amount_direction,
       tx.status, tx.origin,
       SUM({amount_signed}) OVER (
         PARTITION BY tx.account_id, {business_day}
         ORDER BY tx.posting, tx.id
         ROWS UNBOUNDED PRECEDING
       ) AS running_balance,
       tx.metadata
  FROM {prefix}_current_transactions tx
 WHERE …  -- unchanged
```

**Order-by tiebreak.** `ORDER BY tx.posting, tx.id` — `posting` is the primary banking-statement order; `tx.id` is the deterministic tiebreaker for legs sharing a `posting` timestamp (multi-leg same-timestamp transfers post atomically in the seed). Without the tiebreaker, the window function's ordering is non-deterministic on tied rows and the running balance can flicker across runs. Verify in DN.5 that the tiebreaker matches the visual's display sort.

**Signed amount.** `amount_money` is already signed at the storage layer per the schema CHECK constraint (`Credit ≥ 0 / Debit ≤ 0` — `common/l2/schema.py:1986-1988`), so `SUM(amount_money) OVER (...)` directly produces the per-row running balance with no `CASE WHEN amount_direction = 'Credit' THEN ... ELSE -... END` translation needed. The signed-storage convention is the same one `<prefix>_daily_balances.balance = SUM(signed_amount)` keys off — the running-balance projection reuses the existing invariant.

**Account scoping comes from the WHERE clause** (`pL1DsAccount` dataset parameter — single-valued per Y.2.g). The `PARTITION BY account_id` is redundant at runtime since the WHERE already narrows to one account_id; keeping it in the SQL is defensive (matches the production-honest shape — the same SQL evaluated against a multi-account fixture in unit tests still gives the right per-account result; see `[[feedback_production_honest_invariants]]`).

**Date scoping** — the existing `pL1DsBalanceDate` dataset parameter narrows to one business_day. The `PARTITION BY ... {business_day}` is similarly defensive. The window function's partition is a single bucket at render time.

## Starting balance — already present, sparse-account robustness needs verification (operator-confirmed 2026-06-16)

The running balance as defined above starts at 0 + the first leg's signed amount. The **statement-shaped** view a CPA expects anchors the first row to the account's prior end-of-day balance.

**Operator-confirmed 2026-06-16: the opening-balance KPI already exists.** It sits ABOVE the Posted Money Records table on Daily Statement, sourced from `DAILY_STATEMENT_SUMMARY_CONTRACT.opening_balance` (see `apps/l1_dashboard/datasets.py:555` + `app.py:1933` — `ds_summary["opening_balance"].max(currency=True)`). Computed at `_daily_statement_summary_sql` time as `LAG(money) OVER (PARTITION BY account_id ORDER BY business_day_start)` — yesterday's stored EOD balance via window function over `<prefix>_current_daily_balances`. No DN.3 build leaf needed; the KPI's there.

**What DN inherits — running-balance arithmetic anchor.** The Posted Money Records' first row's running balance starts at `0 + first_signed_amount`, not at `opening_balance + first_signed_amount`. The operator-readable view stacks the table BELOW the existing opening-balance KPI; mental model is "opening + accumulated deltas = closing." Whether the table's running_balance projection should anchor against the KPI's opening_balance (visual + arithmetic continuity) vs stay anchored at 0 (pure relative view) is a UX call. The matview-side `closing_balance_recomputed = opening + total_credits - total_debits` (per Drift KPI shape) is the arithmetic constraint either way; DN.5's agreement assertion still applies (`running_balance(last_leg) + opening_balance ≡ closing_balance_recomputed` in the anchored variant; `running_balance(last_leg) ≡ closing_balance_recomputed - opening_balance` in the unanchored variant). DN.1 author picks; lock to ANCHORED unless test-data churn is excessive.

### Sparse-account robustness — operator-flagged 2026-06-16

Operator asked: "are sparse accounts going to be an issue?" The existing opening_balance KPI uses `LAG(money) PARTITION BY account_id ORDER BY business_day_start` against `<prefix>_current_daily_balances`. Two sparse-shape risks:

1. **`<prefix>_current_daily_balances` is sparse (only rows on days with activity).** `LAG()` returns the immediately-prior row in the partition's ordering — which IS the most-recent-prior balance, regardless of date gap. So LAG works **correctly by accident**: even if days N-3 and N have rows but N-2 and N-1 don't, the LAG for day N returns day N-3's money. That IS carry-forward semantics. **No fix needed for that shape.**

2. **Picked day has no row in `<prefix>_current_daily_balances`.** The summary itself is empty on the picked (account, day) → KPI shows blank, running-balance table is empty, opening_balance is undefined. This is a real sparse-data failure mode in production deployments where the ETL only emits on activity. Carry-forward fix: change the opening-balance source from `WHERE business_day_start = picked` to `WHERE business_day_start <= picked ORDER BY business_day_start DESC LIMIT 1`. That returns the most-recent-prior balance for the account even when the picked day itself has no balance row.

**DN.5 (tests) gains a sparse-account fixture assertion.** Plant an account with gaps in `<prefix>_current_daily_balances` (e.g. balance rows on days 1, 3, 7; pick day 5 in the test) and assert the opening_balance KPI shows day 3's balance, not NULL. If the existing matview SQL needs the carry-forward change to pass, that's a DN.1 follow-up (or absorbed into DN.1 directly since the SQL touches the same `_daily_statement_summary_sql`).

**Edge case: account has zero prior balance rows.** First-ever-activity day with no opening snapshot. The carry-forward query returns NULL → KPI shows the special "no prior balance" badge (or hides entirely, operator preference). Default: render as `$0.00 (no prior)` so the running-balance arithmetic still composes; DN.5's edge-case assertion confirms the operator-visible string.

## Agreement contract

Per `[[project_audit_dashboard_agreement]]` the running_balance column must produce identical sequences across all four renderers — QS, App2, direct-DB, PDF.

- **QS ≡ App2** — same dataset SQL, same projection. The window-function path here is identical to the existing per-renderer agreement contract for every other Daily Statement column. The agreement gate at `tests/e2e/qs_browser/` / `tests/e2e/app2/` extends with one new column-presence + per-row-value check.
- **Direct-DB ≡ dataset SQL** — DN.5 adds a unit test that fixes a 5-leg per-day fixture, calls the dataset's SQL through the dialect-specific runner, and asserts the produced `running_balance` sequence matches a Python-computed `itertools.accumulate(signed_amounts)` over the same input. Cross-dialect: run the test under each Dialect enum value so the SQL emitter's per-dialect window-function output is verified at unit time.
- **PDF ≡ dashboards** — `DailyStatementTransaction` dataclass (`cli/audit/__init__.py:1166-1180`) gains a `running_balance: Decimal` field; the audit query (`_query_daily_statement_walks` at `cli/audit/__init__.py:1207-…`) extends with the same window-function projection. The audit's per-row agreement walker (the existing 4-way gate's `audit ≡ direct-DB ≡ QS ≡ App2` shape) picks up running_balance automatically once the contract column lands.

**Drift KPI false-alarm risk.** The summary KPI `closing_balance_recomputed` is `opening_balance + total_credits - total_debits` (per `<prefix>_daily_statement_summary` matview). If the row-level running balance lands at value X on the last leg, then `running_balance(last_leg) ≡ closing_balance_recomputed - opening_balance` must hold. DN.5 asserts this — if the window function and the matview disagree on the closing-of-day arithmetic, that's a real arithmetic bug worth surfacing (or, more likely, an order-of-rows bug between the matview's SUM-per-day and the window-function's running sum).

## Sub-leaves

- [x] **DN.0 — Audit + design lock** at `docs/audits/dn_0_running_balance.md` (this doc). Locks the SQL shape, the no-matview decision, the agreement contract, and the open question on starting-balance UX. Marks DN.3 as operator-confirm-required.
- [ ] **DN.1 — Contract update + dataset SQL.** Add `ColumnSpec("running_balance", "DECIMAL")` to `DAILY_STATEMENT_TRANSACTIONS_CONTRACT`. Extend `_daily_statement_transactions_sql` with the `SUM(...) OVER (...)` projection (PG 17 / Oracle 19c / DuckDB — single SQL, no per-dialect branches expected; verify the unit dialect-emit test). Unit test asserts the projected column matches contract on every dialect.
- [ ] **DN.2 — Posted Money Records visual: add running_balance column on both renderers.** `apps/l1_dashboard/app.py:1995-2004` visual columns gains `ds_txn["running_balance"].numerical(currency=True)` between `amount_direction` and `status`. App2 + QS pick it up automatically through the existing render path.
- [ ] **DN.3 — Carry-forward fix for sparse-account opening_balance.** The existing opening-balance KPI uses `LAG(money) ORDER BY business_day_start` — works correctly when prior balance rows exist but returns NULL when picked day has no row in `<prefix>_current_daily_balances` (sparse-ETL deployments). Change `_daily_statement_summary_sql`'s opening source from `WHERE business_day_start = picked` to `WHERE business_day_start <= picked ORDER BY business_day_start DESC LIMIT 1` so carry-forward fires regardless of gap. Edge case (no prior row at all): NULL → render `$0.00 (no prior)` in the KPI. Unit-test against a planted sparse fixture.
- [ ] **DN.4 — Audit PDF extension.** `DailyStatementTransaction` dataclass gains `running_balance: Decimal`. `_query_daily_statement_walks` projects it via the same window function. PDF table rendering adds the column. Existing 4-way agreement walker picks it up automatically.
- [ ] **DN.5 — Tests.** (i) Unit cross-dialect SQL-emit test for `_daily_statement_transactions_sql` covering PG / Oracle / DuckDB — asserts the produced column sequence matches Python-computed `accumulate(signed_amounts)`. (ii) Unit test asserting `running_balance(last_leg) ≡ closing_balance_recomputed - opening_balance` against a planted fixture. (iii) **Sparse-account fixture**: plant an account with gaps in `<prefix>_current_daily_balances` (e.g. balance rows on days 1, 3, 7; pick day 5), assert the opening_balance KPI shows day 3's balance via the DN.3 carry-forward fix (not NULL). (iv) E2E both renderers (`[qs, app2]`) — assert column present + arithmetic correct on a known seeded account-day. (v) Audit PDF parity test extends to include running_balance.
- [ ] **DN.6 — Phase exit + release cut.** Release version depends on DM's ship order: if DM ships first as v14.5.0, DN ships as v14.6.0; if DN ships first, v14.5.x patch series. Operator-driven release-notes + tag at the cut moment per `[[feedback_always_ask_before_release_cut]]`.

## Cross-app implications

Scouted 2026-06-15 — Daily Statement is the only target.

- `grep -rn "running_balance\|running-balance\|cumulative balance\|carried balance" src/recon_gen/apps/` — zero hits. No other sheet across L1 Dashboard / L2 Flow Tracing / Investigation / Executives surfaces per-row balance evolution today.
- The Posting Ledger sheet (Transactions sheet, `TRANSACTIONS_CONTRACT`) shows every leg across all accounts; running balance there would need a partition over the full matview cardinality and would also have ambiguous semantics (cross-account ordering doesn't correspond to any real bookkeeping concept). Out of scope.
- The Drift sheet (`DRIFT_CONTRACT`) shows per-day stored vs computed; not a per-leg surface. Out of scope.
- The L2 Flow Tracing visuals show transfer-graph navigation, not per-account-day balance walks. Out of scope.
- Executives KPI tiles show roll-ups; not relevant.

If a future phase wants running balance on a multi-day "balance evolution" chart (today none exists), the matview path becomes the right shape — see § Decision above.

## Risks + open questions

- **Synthetic opening row breaks the `transaction_id` shape (shape (b) only).** If DN.3 falls back to shape (b), the synthetic opening row needs a sentinel `transaction_id` (e.g. `__opening__`) plus a visual annotation that drill affordances are suppressed on it. The row also breaks every test that asserts `len(rows) == seed_leg_count` — those tests need to adjust for the +1. Operator-confirm + decision lands at DN.3.
- **Window vs `daily_statement_summary` arithmetic drift.** The matview's `closing_balance_recomputed` is `opening_balance + total_credits - total_debits` (`common/l2/schema.py:3623-3637`). If a planted fixture lands a leg with `amount_direction = 'Credit'` but `amount_money < 0` (would violate the CHECK constraint — should be impossible), the window-function arithmetic and the matview's CASE-based total would disagree. The CHECK constraint defends against this at insert time, but DN.5's "window ≡ matview closing arithmetic" assertion catches any drift that slips through (e.g. a future schema change that loosens the CHECK). Flagged because the drift KPI false-alarm cost would be real — surfacing as a Drift sheet anomaly that's actually a math bug.
- **QS subtitle expression-injection capability** (DN.3 blocker). Whether QS's table-visual subtitle accepts `<<$DatasetName.column>>` reads (vs. only `<<$paramName>>`) is the load-bearing question for shape (a). `[[project_qs_text_box_rich_formatting]]` documents the rich-formatting XML on `SheetTextBox` but doesn't specifically cover table-visual subtitles. Operator-confirm or DN.0-lock-time spike needed before DN.3 starts.
- **Window function performance at high per-day cardinality.** On a synthetic L2 where a single account posts thousands of legs in a day (e.g. a high-volume merchant DDA on settlement day), the per-row window sum is O(rows) and at 5k+ legs the visual could feel sluggish. Daily Statement's narrowing to a single (account, day) keeps real-world cardinality low; if a customer hits this, the follow-up phase is the matview path. Not a blocker for DN.1.
- **Order-by tiebreaker assumption.** DN.1's `ORDER BY tx.posting, tx.id` assumes the visual's display sort matches; if a future change reorders the table display by amount or rail_name, the running-balance column would visibly "skip" because the SQL still computes it in posting order. Lock the display sort to posting in DN.2's visual config + add a unit/json-tier test asserting it. Documented as a constraint in the dataset docstring.
- **Audit PDF re-anchoring.** The PDF currently doesn't display opening balance per-walk-page; if DN.3 ships shape (a), the audit also wants the subtitle's opening balance. DN.4 adds the column; whether the PDF surfaces the opening-balance badge is a separate decision (default: yes, mirror the dashboard).

## Cross-references

- Implementation tasks: PLAN.md `## Phase DN` block (currently placeholder, sub-leaves expanded by this doc).
- Sibling phase, parallel design: `docs/audits/dm_0_daily_statement_app2_cascade.md` — App2-only Role cascade + day-availability picker on the same sheet. DN is the row-shape change; DM is the find-the-row UX change. Both can ship in either order; DN.6 release notes coordinate the version bump.
- Related: `[[project_audit_dashboard_agreement]]` — QS / App2 / direct-DB / PDF must agree row-for-row, the credibility contract DN.5 enforces.
- Related: `[[feedback_production_honest_invariants]]` — running balance must match real-system arithmetic; the signed-storage convention reused here is the same one `<prefix>_daily_balances.balance = SUM(signed_amount)` keys off. Demo-only filters are not an option.
- Related: `[[project_oracle_19c_compat]]` — window functions are in the portable cross-dialect surface; inline `OVER (...)` (no named `WINDOW w AS` clause) is the constraint Schema_v6 documents.
- Related: `[[feedback_kpi_currency_decimals_strict]]` — `running_balance` is currency-formatted; the parser-layer KPI test catches >2-decimal drift on every read site.
- Related: `[[project_qs_text_box_rich_formatting]]` — load-bearing for DN.3 shape (a). Open question on table-visual subtitle expression-injection capability.
- Related: `[[feedback_qs_convention_origin]]` — DN is NOT a renderer-capability symmetry break (contrast with DM); the running-balance column is a data-row-shape change both renderers ingest identically.
- Existing surface: `apps/l1_dashboard/datasets.py:571-591` (`DAILY_STATEMENT_TRANSACTIONS_CONTRACT`), `apps/l1_dashboard/datasets.py:1298-1363` (`_daily_statement_transactions_sql` + `build_daily_statement_transactions_dataset`), `apps/l1_dashboard/app.py:1986-2025` (Posted Money Records visual), `cli/audit/__init__.py:1162-1210` (`DailyStatementWalk` audit dataclass + query). These are the surfaces DN.1 / DN.2 / DN.4 touch.
