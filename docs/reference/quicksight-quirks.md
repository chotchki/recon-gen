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
