# QuickSight Quirks Log

Running log of QuickSight rendering / wire / behavior surprises this
codebase has bumped into. Per `feedback_quirks_log_ever_growing`: when
we discover a new QS bug or quirk, append an entry HERE as part of the
same fix branch — not as a follow-up. The log is append-only;
historical entries stay even after the underlying bug is fixed on
AWS's side so we can recognize the shape on the next regression.

## `CategoricalMeasureField(COUNT)` silently renders DISTINCT on a string column also used as a Dim — BL.1, 2026-05-27

### Symptom

A KPI bound to `ds["account_id"].count()` (which emitted
`CategoricalMeasureField(AggregationFunction="COUNT")`) returns the
distinct-value count of `account_id`, not the row count, whenever the
same `account_id` column is also used as a dimension elsewhere on the
same visual or sheet (e.g. a table below the KPI binds `account_id`
as a column).

Observed ratios on the bundled spec_example deploy: KPI shows ~138
when the matview has ~811 day-rows across ~138 distinct accounts;
209 actual rows vs 26 distinct accounts. Off by exactly the
distinct-vs-rows ratio.

### Confirmed-via

Five honest-gate KPI tests xfail'd citing BL.1:

- `tests/e2e/test_l1_filters.py::test_bg3_drift_sheet_kpis_match_matview_counts`
- `tests/e2e/test_l1_filters.py::test_bg6_todays_exceptions_kpi_matches_dataset_count`
- `tests/e2e/test_l1_filters.py::test_bg3_overdraft_kpi_matches_matview_count`
- `tests/e2e/test_l2ft_exceptions.py::test_bg6_l2ft_exceptions_kpi_matches_dataset_row_count`
- `tests/e2e/test_inv_filters.py::test_bg4_recipient_fanout_kpis_match_inflows_only_truth`

All had "App2 leg passes (raw `COUNT(column)`)" + "QS leg renders
distinct count." Same shape across all five — single root.

### Workaround

Don't emit `CategoricalMeasureField(COUNT)` for row-count semantics.
Two safe alternatives — both implemented in `Measure.kind == "count"`
as of BL.1:

1. **`NumericalMeasureField(SUM)` over a literal-1 `CalcField`**. QS
   wire is pure numerical aggregation; no string column appears in
   the COUNT slot so the distinct-quirk has nothing to trigger on.
   The CalcField is auto-registered on the Analysis (one per
   `Dataset` referenced by a count Measure) by
   `App.resolve_auto_ids`; the convention name is
   `_row_one_<sanitized-dataset-id>`. `App2`'s `_visual_sql` emits
   `SUM(1)` for `kind == "count"` to stay symmetric with the QS
   wire.
2. **Use `.distinct_count()` if DISTINCT is what you actually want.**
   The original `.count()` semantic became ambiguous; if a callsite
   wants distinct-of-column behavior, the explicit method makes
   the intent visible.

Reference implementations: `common/tree/fields.py::Measure.emit`
(QS-side wire) and `common/html/_visual_sql.py::_measure_sql`
(App2-side SQL). `App.resolve_auto_ids` auto-registration:
`common/tree/structure.py`.

### Notes

The DISTINCT-rendering behavior is undocumented. The same wire shape
on a *numeric* column appears to render row count correctly — the
quirk seems specific to string columns that double as Dims. We did
not search for a config flag to opt out; the CalcField-1 fix is the
operational answer regardless of what the underlying flag would be.

## `TimeRangeFilter(time_granularity="DAY")` excludes upper-edge late-day rows — 2026-05-27 (DISSOLVED by Phase BM, 2026-05-27)

### Symptom (historical)

An analysis-level `TimeRangeFilter` with `time_granularity="DAY"` and
`IncludeMaximum=True` over a `TIMESTAMP` column was claimed (per the
codebase's `app2_date_filter` docstring) to truncate the column at
the day boundary during filter evaluation and INCLUDE the full upper-
edge day. App2's mirror clause uses `column < CAST(:date_to AS DATE)
+ 1 day` for the same effect.

Observation on bg5 Exec KPI parity:

- App2: `Total Transactions = 2392`, `Gross Money Moved = $4,779,478.81`
- QS:   `Total Transactions = 2390`, `Gross Money Moved = $4,771,878.81`

The 2 missing transactions and $7,600 delta were 2030-01-01 rows with
`posting` > 00:00:00 — late-day timestamps that fall on the window's
upper edge. App2 included them; QS excluded them. So QS's behavior
was effectively `column <= '2030-01-01 00:00:00'` even with
`time_granularity="DAY"` + `IncludeMaximum=True`.

### Resolution — Phase BM (date-picker pushdown unification)

The second "operational fix candidate" below shipped. The pre-BM
dual-SQL form (analysis-level `TimeRangeFilter` on QS + `{date_filter}`
template on App2) dissolved in favor of unified `<<$pXxxDateStart>>`
/ `<<$pXxxDateEnd>>` dataset-SQL parameter pushdown via
`common/sql/app2_filters.py::universal_date_range_clause`. Both
renderers now run the same `column >= start AND column < end + 1 day`
shape against the same parameter values; the day-edge asymmetry
dissolves by construction. The two bg5 [qs] xfails (`test_bg5_*` in
`tests/e2e/test_exec_sheet_visuals.py`) were dropped at the same time.

### Notes (kept for historical context)

The asymmetry only mattered when the source column was a `TIMESTAMP`
with non-midnight values. Matview columns that are date-truncated at
create time (`business_day_start`, `posted_date`) didn't trip the
quirk — both renderers saw the same coarse-granularity values.
The transaction_summary case uses `t.posting` directly (raw
timestamp), which was the failure shape. Post-BM the same column
is narrowed via `TO_DATE(<<$pExecDateEnd>>, ...) + 1` on Oracle /
the equivalent on PG + SQLite, so late-day rows on the upper-edge
day are included on both renderers.
