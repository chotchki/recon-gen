# BM.0 spike — date-picker pushdown unification

Branch: `bm-date-picker-unification` (2026-05-27)
Phase: BM (Date-picker pushdown unification)
Status: design lock + migration template — implementation queued for BM.1

## What the spike confirmed

**The "lose the chrome widget" UX concern is moot.** The current L1 + Exec
date pickers are already sheet-level controls (`ParameterDateTimePicker`
added via `sheet.add_parameter_datetime_picker(...)` — see
`apps/l1_dashboard/app.py::_wire_date_range_filter:1815-1825`). The
analysis-level `TimeRangeFilter` is purely a narrowing mechanism; it
emits no visible chrome. Users see exactly the same two picker widgets
on each data-bearing sheet before vs after the migration.

This collapses the original "BM.5 release needs no version bump unless
cold-read flags it" risk to near-zero. The change is invisible to
operators.

## Current wire (the "dual-SQL" form)

For each data-bearing dataset on L1 (drift, ledger_drift,
drift_timeline, ledger_drift_timeline, overdraft, limit_breach,
todays_exceptions, transactions) + Exec (account_summary,
account_summary_active, transaction_summary):

1. **`build_<x>_dataset`** carries a `{date_filter}` slot in its SQL
   template + `app2_date_column="<col>"` kwarg.
2. **`build_dataset`** dual-emits:
   - QS-side: `{date_filter}` → empty string. SQL has no date narrowing.
   - App2-side (registered via `register_sql`): `{date_filter}` →
     `AND col >= CAST(:date_from AS DATE) AND col < CAST(:date_to AS DATE) + 1 day`
     (day-inclusive upper bound, sentinel match-all on empty binds).
3. **Analysis declares** `DateTimeParam(P_L1_DATE_START)` +
   `DateTimeParam(P_L1_DATE_END)` with defaults from a shared
   `DateView(frame=cfg.test_generator.as_of_frame(window_days=7))`.
4. **`_wire_date_range_filter`** creates one `SINGLE_DATASET` FilterGroup
   per dataset with a `TimeRangeFilter` bound to those params. The
   FilterGroup is scoped to the sheet (`fg.scope_sheet(sheet)`).
5. **`ParameterDateTimePicker`** controls on each sheet bind to the
   analysis params — picker → analysis param → TimeRangeFilter → visual
   narrowing on QS; picker → URL param → bind layer → SQL narrowing on
   App2.

The BL.2 work added `Analysis.default_universal_date_range = DateView`
+ `_apply_default_date_range()` helper in `_tree_fetcher.py` to handle
the case where App2's URL has empty `date_from`/`date_to` on initial
render (the bind layer substitutes the default from the analysis's
`default_universal_date_range` field).

## Target wire (the unified form)

For each affected dataset:

1. **Dataset SQL** uses `<<$pL1DateStart>>` / `<<$pL1DateEnd>>`
   placeholders directly:

   ```sql
   SELECT ... FROM <prefix>_drift
   WHERE <existing pushdown clauses>
     AND business_day_start >= <<$pL1DateStart>>
     AND business_day_start <= <<$pL1DateEnd>>
   ```

2. **DataSetParameter** declarations for both, with `TimeGranularity="DAY"`
   + `DefaultValues = StaticValues=[ISO-formatted default]`. Default
   matches the analysis param's default (derived from
   `cfg.test_generator.as_of_frame(window_days=7)`). App2's empty-URL
   case falls back to this default; QS overrides via the analysis bridge.

3. **Analysis `DateTimeParam`** gets `mapped_dataset_params=[(ds, "pL1DateStart"), ...]`
   bridging analysis → dataset parameter. List grows per dataset added.

4. **`_wire_date_range_filter`** drops the `TimeRangeFilter` FilterGroup
   loop. Picker wiring stays unchanged (already sheet-level).

5. **`build_dataset`** drops `app2_date_column=` kwarg + `{date_filter}`
   template handling. Single SQL form across renderers.

6. **`_tree_fetcher.make_tree_db_fetcher`** drops the
   `_apply_default_date_range` step + the
   `Analysis.default_universal_date_range` read. Defaults live on the
   dataset parameter, not on the analysis tree.

## Per-dataset migration template

For each `build_<x>_dataset` call site with `app2_date_column=` set:

```python
def build_<x>_dataset(cfg, l2_instance):
    # ... existing column / cents-wrap setup ...
    sql = (
        f"SELECT ..."
        f" FROM {prefix}_<view>\n"
        f"WHERE <existing pushdown clauses>\n"
        # BM.1 — date filter via dataset-SQL parameter pushdown.
        # Replaces the pre-BM `{date_filter}` template slot.
        f"  AND <date_col> >= <<${{P_L1_DATE_START}}>>\n"
        f"  AND <date_col> <= <<${{P_L1_DATE_END}}>>"
    )
    default_from = _l1_universal_range_default_from(cfg)
    default_to = _l1_universal_range_default_to(cfg)
    return build_dataset(
        cfg, ...,
        sql, <X>_CONTRACT,
        visual_identifier=DS_<X>,
        dataset_parameters=[
            <existing categorical / sentinel params>,
            DatasetParameter(DateTimeDatasetParameter=DateTimeDatasetParameter(
                Name=str(P_L1_DATE_START), ValueType="SINGLE_VALUED",
                TimeGranularity="DAY",
                DefaultValues=DateTimeDatasetParameterDefaultValues(
                    StaticValues=[default_from],
                ),
            )),
            DatasetParameter(DateTimeDatasetParameter=DateTimeDatasetParameter(
                Name=str(P_L1_DATE_END), ValueType="SINGLE_VALUED",
                TimeGranularity="DAY",
                DefaultValues=DateTimeDatasetParameterDefaultValues(
                    StaticValues=[default_to],
                ),
            )),
        ],
        # app2_date_column kwarg REMOVED.
    )
```

New helpers (one place):

```python
def _l1_universal_range_default_from(cfg: Config) -> str:
    frame = cfg.test_generator.as_of_frame(window_days=7)
    return f"{frame.window.start.isoformat()}T00:00:00"

def _l1_universal_range_default_to(cfg: Config) -> str:
    frame = cfg.test_generator.as_of_frame(window_days=7)
    return f"{frame.as_of.isoformat()}T00:00:00"
```

Same shape for Exec (window_days=30, P_EXEC_DATE_START / P_EXEC_DATE_END).

## `_wire_date_range_filter` simplifies

Drops the inner `_scope_one` closure entirely. Becomes:

```python
def _wire_date_range_filter(analysis, *, datasets, date_scoped_sheets, universal_range_view):
    date_start_mappings = [(datasets[k], str(P_L1_DATE_START)) for k in _DATE_SCOPED_DATASETS]
    date_end_mappings = [(datasets[k], str(P_L1_DATE_END)) for k in _DATE_SCOPED_DATASETS]
    date_start = analysis.add_parameter(DateTimeParam(
        name=P_L1_DATE_START,
        time_granularity="DAY",
        default=universal_range_view.emit_qs_analysis_default_start(),
        mapped_dataset_params=date_start_mappings,
    ))
    date_end = analysis.add_parameter(DateTimeParam(
        name=P_L1_DATE_END,
        time_granularity="DAY",
        default=universal_range_view.emit_qs_analysis_default_end(),
        mapped_dataset_params=date_end_mappings,
    ))
    for sheet in date_scoped_sheets:
        sheet.add_parameter_datetime_picker(parameter=date_start, title="Date From")
        sheet.add_parameter_datetime_picker(parameter=date_end, title="Date To")
```

`_DATE_SCOPED_DATASETS = [DS_DRIFT, DS_LEDGER_DRIFT, DS_DRIFT_TIMELINE,
DS_LEDGER_DRIFT_TIMELINE, DS_OVERDRAFT, DS_LIMIT_BREACH,
DS_TODAYS_EXCEPTIONS, DS_TRANSACTIONS]`. The per-dataset mapping list
replaces the per-dataset FilterGroup. `TimeRangeFilter` import dropped.

## What dissolves (BL.2 cleanups)

- `Analysis.default_universal_date_range: DateView | None` field — gone.
- `_apply_default_date_range()` helper in `common/html/_tree_fetcher.py` — gone.
- `_default_date_from` / `_default_date_to` build-time captures in `make_tree_db_fetcher` — gone.
- Test helper `_l1_default_date_binds(cfg)` — gone (test pickers fire via URL `?param_pL1DateStart=...`).
- Test helper `_exec_default_date_binds(cfg)` — gone (same).
- `_sql_for(..., visual_identifier=DS_*)` kwarg — gone (only-one-SQL).
- `_sql_and_params_for(..., visual_identifier=DS_*)` kwarg in `test_l1_filters.py` — gone.
- bg5 `[qs]` xfail strict=False (the QS day-edge quirk) — gone, both renderers use the same `BETWEEN` semantics.

## App2 URL contract

**Open question** for the implementer: keep the existing URL keys
`date_from` / `date_to` (and map them to the parameter placeholders
server-side), or switch to `param_pL1DateStart` / `param_pL1DateEnd` /
`param_pExecDateStart` / `param_pExecDateEnd` (the natural shape that
falls out of the `<<$pX>>` → `:param_pX` translation)?

- **Keep existing keys**: less URL-contract churn (deep-links survive).
  Server-side mapping logic per-app (L1 uses pL1Date*, Exec uses
  pExecDate*).
- **Switch to param_* keys**: zero server-side mapping, more URL-keys
  to remember, breaks any bookmarked deep-links.

Recommendation: **switch to `param_*` keys** for symmetry with the
other categorical filter URL params (`param_pL1DriftAccount`, etc.).
The "deep link survival" concern is theoretical for an internal tool;
the symmetry win is permanent.

## Test surface impact

Three test files need updating once the migration lands:

1. `tests/e2e/test_l1_filters.py` — `_l1_default_date_binds` helper +
   `_sql_and_params_for(..., visual_identifier=)` callers simplify.
2. `tests/e2e/test_exec_sheet_visuals.py` — `_exec_default_date_binds`
   + `_sql_for(..., visual_identifier=)` callers simplify.
3. `tests/unit/test_html_filter_widgets.py::test_bl_2_date_range_prefilled_when_analysis_has_default`
   — currently tests the bind-layer prepop; replace with a test
   asserting the dataset's `DateTimeDatasetParameter.DefaultValues`
   carry the analysis-default range.

JSON fixture byte-locks in `tests/json/` will re-roll (date params
added to each affected dataset's `DatasetParameters` array). That's
expected churn for an intentional wire shape change.

## Sequencing recommendation

1. **BM.1 sweep** lands all 8 L1 datasets + 3 Exec datasets in ONE
   commit. The migration template is mechanical; partial migration
   leaves mixed state (some datasets pushdown-based, some FilterGroup-
   based) which complicates `_wire_date_range_filter` during the
   transition.
2. **BM.2 dead-machinery removal** in the same commit (or the next) —
   `{date_filter}` template handling, `app2_date_column` kwarg, helper
   funcs, `Analysis.default_universal_date_range` field, bind-layer
   helper.
3. **BM.3 test cleanup** can be the same commit OR a follow-up; the
   migration's correctness gate is the existing BG.X honest tests.
4. **BM.4 quirks cleanup** — drop bg5 [qs] xfail; update quirks doc
   entry. Can ride with BM.3.
5. **BM.5 release** — minor version bump.

Estimate: 1-2 days of focused work for the full sweep.
