# DR.0 — Supersession Audit: detection fix + transaction-id navigation (design lock)

Design lock for Phase DR. Supersedes the earlier `dr_0_supersession_investigation.md` (on the `dr-investigation` branch). Grounded in a workflow that reproduced the bug on a seeded DuckDB and mapped the affected code. Every claim is `file:line`-cited.

## 1. The bug — confirmed, all-dialects, TWO independent defects

The Supersession Audit ("flagging everything") has two root causes, both stemming from `entry` being a **global insert-order serial** (PG `BIGSERIAL` / DuckDB `BIGINT DEFAULT nextval(seq)` — `schema.py:1795`; reset to 1 only at seed start via `TRUNCATE RESTART IDENTITY`), NOT per-logical-id numbering:

**Defect A — over-FLAG (Finding 1).** `l1_supersession_no_reason = CASE WHEN entry > 1 AND supersedes IS NULL` (`datasets.py:1740`). Since `entry > 1` is true for ~every row, the flag fires on the *original* row of every superseded trail.

**Defect B — over-SELECT (Finding 2, the bigger half).** Row selection is `COUNT(*) OVER (PARTITION BY id) > 1` (`datasets.py:1751/1754`). Reproduced on the full QS-deploy baseline (sasquatch_pr, `l1_plus_broad`, densify×5+broken+drift+boost, DuckDB, 127,710 rows): the predicate selects **77 rows across 11 ids, but only 1 id (10 rows) is a genuine supersession**; the other **67 rows are densified-plant legs** (`supersedes IS NULL` on all). Cause: `densify_scenario(factor=5)` (`auto_scenario.py:770`) replicates each plant varying only `days_ago`, but the spine generators emit a deterministic `id` that **omits the day** (`drift.py:272`, `limit_breach.py:213`, `stuck_pending.py:146`), so 5 replicas reuse one `id` → 5 global `entry` values → trip `COUNT > 1` with no supersedes. Baseline `tx-base-*` ids are monotonic and a transfer's two legs get distinct ids → **0 baseline collisions; only plants collide.**

The two defects are **independent** — the flag fix alone leaves all 67 phantom rows in the table. `build_supersession_daily_balances_dataset` (`datasets.py:1786-1802`) has **no flag at all** (bare `WHERE entry_count > 1`) and selected 0 rows in baseline, so it needs only the SELECT narrowing.

## 2. DR.1 — Detection fix (detection-side, both renderers via shared dataset SQL)

**DR.1.a — flag (Defect A), transactions dataset.** Add `MIN(entry) OVER (PARTITION BY id) AS min_entry` to the inner SELECT (`datasets.py:1747-1751`); rewrite the flag (`datasets.py:1740`):
```sql
CASE WHEN entry > min_entry AND supersedes IS NULL THEN 1 ELSE 0 END AS l1_supersession_no_reason
```

**DR.1.b — SELECT narrowing (Defect B), BOTH datasets.** Add to the inner SELECT:
```sql
MAX(CASE WHEN supersedes IS NOT NULL THEN 1 ELSE 0 END) OVER (PARTITION BY id) AS has_supersede
```
and the outer WHERE (`datasets.py:1754`; mirror at `:1801` with `PARTITION BY account_id, business_day_start`):
```sql
WHERE entry_count > 1 AND has_supersede = 1
```
Result: 77 → 10 rows (the one real trail). **Decision (ratified): detection-side, not seed-side** (per [[feedback_production_honest_invariants]] — the predicate is genuinely too loose; a production id-reuse would mis-fire the same way; fixing the SQL keeps the matview/Current* contract untouched).

**DR.1.c — exact-set coverage.** A `TestScenarioCoverage` asserting the Supersession Audit table contains *exactly* the planted supersession ids. Closes a real 4-way-gate blind spot: the gate asserts `plants ⊆ direct == QS == App2`, so when all three over-select identically it passes while all three are wrong.

**DR.1.d — re-lock** the semantic snapshot if the violation set shifts (`recon-gen data semantic-lock --l2 sasquatch_pr`).

**Unit-test shape (write first):** one `id` with entries `{min:NULL, +1:NULL, +2:TechnicalCorrection}` → all 3 selected; flags 0/1/0. Negative: an id with `{n:NULL, n+1:NULL}` (densified-plant shape) → 0 rows selected (fails `has_supersede=1`).

## 3. DR.2–DR.5 — transaction-id navigation (operator's idea)

Supersession is keyed on the logical transaction `id`; `transfer_id` groups *legs of a transfer event* (a different axis). Navigating on `id` aligns with the audit's key.

**DR.2 — `ColumnShape.TRANSACTION_ID`** (`dataset_contract.py:34-119`) — new nominal shape (no existing fits; reusing `TRANSFER_ID` defeats the K.2 drill type-safety). Tag `TRANSACTIONS_CONTRACT.transaction_id` (`datasets.py:614`) + `SUPERSESSION_TRANSACTIONS_CONTRACT.transaction_id` (`datasets.py:710`), both `shape=None` today.

**DR.3 — transaction-id search** on the L1 Transactions sheet. The dataset already projects `id AS transaction_id` (`datasets.py:1490`); `id` is UNIQUE-indexed on the matview (`schema.py:600-602`) → sub-ms. Reuse the existing "Transfer" **typeahead** picker pattern (`_populate_pushdown_value_dropdown` `app.py:2470` + `LinkedValues` companion + `PickerMatviewHint`) — typeahead, not enumerated, since `id` is unbounded. Both renderers. Add a visible `transaction_id` column to the Posting Ledger table so search narrows a visible column. **Keep the Transfer dropdown alongside** (non-breaking; ratified).

**DR.4 — crosslink → transaction id. RESOLVED — operator confirmed option (b), 2026-06-17: a same-sheet filter of the Supersession Audit trail by transaction `id`.** The current Supersession Audit drill keys on `transfer_id` (`app.py:1739/1774/1781`). A prior author chose `transfer_id` *deliberately* (`app.py:1734-1738`) because the *Current\** Transactions sheet is max-entry-only → a transaction_id lands on a single row, and the multi-entry trail lives ONLY on the Audit's own base-table dataset. Options:
- **(b) RECOMMENDED — same-sheet filter of the Audit trail to that `id`.** Preserves the full trail (the point of the surface); being a control-write not a cross-sheet URL nav, it **sidesteps the QS URL-param-no-control-sync quirk** ([[project_qs_url_parameter_no_control_sync]]) → works identically on QS + App2.
- (a) literal "drill to Transactions sheet" → single current row, loses the trail, hits the QS quirk.
- (c) new base-table transactions destination → most work, least payoff.

Mechanics for (b): `_DP_TX_TRANSACTION` DrillParam + `_L1_DRILL_RESET_PARAMS` entry (`app.py:247-258, 2699`); self-filter pushdown on `build_supersession_transactions_dataset` rather than a cross-sheet drill. **Daily Statement → Transactions drill stays on `transfer_id`** (out of scope; ratified).

**DR.5 — "(No reason)" filter** on the Supersession Audit (depends on DR.1.a's corrected flag). A SINGLE_SELECT/toggle param → sentinel-OR pushdown on `l1_supersession_no_reason` (`_data_value_clause`, `datasets.py:284`). Picker threads into the transactions dataset; daily_balances has no flag (its existing Account dropdown covers narrowing).

## 4. Sequencing + open decision

DR.1 is the actual correctness bug (still shipped in v14.5.1) — **lands first, as its own commit chain, not gated on the navigation feature.** Then DR.2 → DR.3 → DR.4 → DR.5 (DR.5 hard-depends on DR.1.a). DR.1/DR.3/DR.5 land on QS + App2 + PDF (shared dataset SQL → agreement gate covers all three). DR.4 lands on both renderers under option (b).

**One open decision for the operator: DR.4 crosslink target — confirm (b) same-sheet trail filter** (the recommendation; (a)/(c) are the alternatives).
