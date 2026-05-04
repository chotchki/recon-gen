# How do I author a new app on the tree?

*Customization walkthrough — Developer / Product Owner. Building a fifth (or sixth) dashboard.*

## The story

You've read the [shared schema](../../Schema_v6.md), pointed your
production data at `{{ l2_instance_name }}_transactions` +
`{{ l2_instance_name }}_daily_balances`, and the four shipped apps (L1
Reconciliation Dashboard, L2 Flow Tracing, Investigation,
Executives) cover most of what your operations team needs. But
you have one more reporting cadence — say a board-level summary
beyond the Executives view, a fraud-team triage view, a marketing
rollup — that doesn't fit any of the four existing apps' question
shapes.

You want to build a fifth dashboard from scratch. You don't want
to fork an existing app, because most of the wiring you'd inherit
is the wrong shape for your question. And you don't want to drop
into raw QuickSight JSON, because that's how the constants-heavy
maintenance burden the tree replaced started.

The tree primitives in `common/tree/` are the answer. This
walkthrough walks the **Executives** app — the codebase's own
greenfield example — start to finish: 4 sheets, 5 visuals on the
biggest one, 1 visual-pinned filter, no parameters, no cross-app
drills. The total app file is ~600 lines of Python; no per-app
visual builders, no constant-flooded `constants.py`, no manual
visual-ID bookkeeping.

## The question

"What's the minimum I need to write to add a fifth standalone
dashboard, given that the dataset interface
(`{{ l2_instance_name }}_transactions` + `{{ l2_instance_name }}_daily_balances` emitted by
`common/l2/schema.py::emit_schema`) is already in place?"

## Where to look

Five reference points:

- **`src/quicksight_gen/apps/executives/app.py`** — the worked example.
  4 sheet specs, 4 populator functions, 1 filter group, 1
  `build_executives_app()` entry point. Read the whole file before
  starting; it's the smallest end-to-end app in the codebase.
- **`src/quicksight_gen/apps/executives/datasets.py`** — the dataset
  side. Two custom-SQL datasets, two `DatasetContract` declarations,
  one `build_all_datasets()` helper that registers contracts with
  `register_contract()` as a module-import side effect.
- **[API Reference — Tree primitives](../../api/index.md)** — the L1
  API surface. Each typed Visual subtype, Filter wrapper, and Drill
  action's signature is the canonical place to look up parameter
  shape.
- **`src/quicksight_gen/cli/_app_builders.py`** — the
  `_generate_executives()` helper. Add a sibling
  `_generate_<myapp>()` here. **`src/quicksight_gen/cli/json.py`**
  — the `json_apply` command body that calls each
  `_generate_<app>()` in turn; add a line for yours.
  **`src/quicksight_gen/cli/_helpers.py`** — the `APPS` tuple
  shared across the artifact groups; append your app slug.
- **`tests/test_executives.py`** — 18-test starter pack that walks the
  tree to assert structural invariants (sheet count, visual presence,
  filter scoping, CLI smoke). Mirror this shape in your app's tests.

## What you'll see in the demo

Build your app as **`apps/<myapp>/`** alongside the existing four. The
file layout is:

```
src/quicksight_gen/apps/myapp/
    __init__.py     # one-line docstring
    app.py          # everything except datasets — sheet IDs, populators, build_myapp_app()
    datasets.py     # build_all_datasets(), DatasetContract declarations, register_contract() calls
```

Three things you do *not* need:

- **No `constants.py`.** Sheet IDs are inline in `app.py` (URL-facing,
  must stay stable, ~4 lines). Internal IDs (visual_id, filter_group_id,
  action_id, layout element IDs) are auto-derived from tree position by
  the L.1.16 resolver — you never write them.
- **No `visuals.py` / `filters.py` / `analysis.py`.** The tree's typed
  builders (`row.add_kpi(...)`, `row.add_table(...)`,
  `FilterGroup.with_numeric_range_filter(...)`) replace the per-app
  builder modules entirely. Wiring lives in `app.py` populator
  functions, one per sheet.
- **No `demo_data.py`.** Because the four shipped apps all read the
  same per-instance prefixed base tables, the L2 instance's seed (and
  any per-app overlay seed) populates your new app for free. If your
  app needs its own seed shape, add `demo_data.py` next to
  `datasets.py`; otherwise skip it.

The skeleton of `app.py` looks like:

```python
from quicksight_gen.common.config import Config
from quicksight_gen.common.ids import SheetId
from quicksight_gen.common.tree import App, Sheet
from quicksight_gen.common.tree.filters import FilterGroup
from quicksight_gen.apps.myapp.datasets import (
    DS_MYAPP_FOO,
    build_all_datasets,
)

# Sheet IDs (URL-facing, stable; inline since there's no constants.py).
SHEET_MYAPP_OVERVIEW = SheetId("myapp-sheet-overview")
SHEET_MYAPP_DETAIL = SheetId("myapp-sheet-detail")


def build_myapp_app(cfg: Config) -> App:
    app = App(cfg=cfg)

    # Register datasets (typed Dataset nodes; visuals reference these).
    datasets = build_all_datasets(cfg)
    ds_foo = app.register_dataset(DS_MYAPP_FOO, datasets[0].DataSetArn)
    # ... register the rest

    # Pre-register sheet shells so cross-sheet drills can target them
    # by Sheet object ref (not string ID) before they're populated.
    overview_sheet = app.analysis.add_sheet(
        sheet_id=SHEET_MYAPP_OVERVIEW, name="Overview",
        title="Overview", description="...",
    )
    detail_sheet = app.analysis.add_sheet(
        sheet_id=SHEET_MYAPP_DETAIL, name="Detail",
        title="Detail", description="...",
    )

    # Populate each sheet (one function per sheet — keeps `app.py`
    # readable as the dashboard grows).
    _populate_overview(overview_sheet, ds_foo, drill_target=detail_sheet)
    _populate_detail(detail_sheet, ds_foo)

    # Create the dashboard mirroring the analysis.
    app.create_dashboard(
        dashboard_id_suffix="myapp-dashboard",
        name="My App",
    )
    return app


def _populate_overview(sheet: Sheet, ds_foo, drill_target: Sheet) -> None:
    row = sheet.layout.row(height=8)
    row.add_kpi(
        title="Total Foos",
        subtitle="Count of all foo records.",
        value=ds_foo["foo_id"].distinct_count(),
    )
    table = row.add_table(
        title="Foo Detail",
        subtitle="Click any row to drill into the Detail sheet.",
        group_by=[ds_foo["foo_id"].dim(), ds_foo["foo_name"].dim()],
        values=[ds_foo["amount"].sum()],
        actions=[Drill(
            writes=[(some_param, ds_foo["foo_id"].dim())],
            name="See detail for this foo",
            trigger="DATA_POINT_CLICK",
            target_sheet=drill_target,  # Sheet *object*, not string ID
        )],
    )

    # Visual-pinned filter (sheet-wide also available — see filter_group docs).
    fg = FilterGroup.with_numeric_range_filter(
        column=ds_foo["amount"], min_value=100,
        filter_group_id=FilterGroupId("fg-myapp-amount-min"),
    )
    fg.scope_visuals(table)
    sheet.filter_groups.append(fg)


def _populate_detail(sheet: Sheet, ds_foo) -> None:
    # ... same shape
    pass
```

Datasets follow the same pattern as the four shipped apps —
`DatasetContract` lists the column projection; the SQL must produce
exactly those columns; `register_contract()` wires the contract
into the typed-Column validation that catches column-name typos at
the wiring site (loud `KeyError`) instead of at deploy (silent
broken visual).

## What it means

Four properties of the tree-built app pattern that internalize once
you've shipped one:

1. **Object refs, not string IDs.** Visuals reference `Dataset` nodes,
   not dataset identifier strings; drills reference `Sheet` nodes, not
   sheet IDs. `app.emit_analysis()` runs validation walks (dataset /
   calc-field / parameter / drill-destination references) — a missing
   reference fails at construction with a stack trace pointing at the
   wiring site, not at deploy with an opaque "InvalidParameterValue".
2. **Pre-register all sheets.** Cross-sheet drills need their target
   `Sheet` ref to exist before the source visual is constructed. The
   pattern: declare every sheet shell first (`app.analysis.add_sheet(...)`
   for each), THEN populate them one at a time with the
   already-resolved `Sheet` references in scope.
3. **Sheet IDs explicit, internal IDs auto.** URL-facing identifiers
   (`SheetId`, `ParameterName`) and analyst-facing identifiers
   (`Dataset` identifier, `CalcField` name) stay explicit because they
   show up in URLs / DOM / analyst tooltips; internal IDs are
   auto-derived because they're positional and only the tree itself
   reads them. See L.1.16 in `PLAN.md` for the rationale.
4. **The tree IS the source of truth.** Tests walk the tree to derive
   expected sets — `tests/test_executives.py` is a good template. Don't
   maintain a parallel hand-listed set of expected visual titles in
   the test fixture; the tree walks every sheet's visuals and the test
   asserts what the tree emits, not what someone hand-typed.

## Drilling in

The L.1 primitives expose more than this walkthrough surfaces:

- **Calculated fields**: `CalcField` for analysis-level computed
  columns. Ties to one `Dataset`; usable across visuals.
  [`api/tree-data.md`](../../api/tree-data.md).
- **Parameters + parameter controls**: `StringParameter` /
  `IntegerParameter` / etc. + their `Control` wrappers (dropdown,
  slider, datetime picker). Drills can write to parameters; filters
  can read from them.
  [`api/tree-filters-controls.md`](../../api/tree-filters-controls.md).
- **Cross-app drills**: `CustomActionURLOperation` builders in
  `common/drill.py` for jumping to another app's deployed dashboard
  with parameter values pre-set in the URL — but note the
  [QuickSight URL parameter sync limitation](../../handbook/customization.md)
  before relying on it.

## Next step

1. Skim `apps/executives/app.py` end-to-end — it's the shortest
   reference implementation in the codebase.
2. Skim `tests/test_executives.py` for the test pattern (walk the
   tree, assert what's emitted).
3. Build a minimal `app.py` with one sheet and one visual; `pytest
   tests/test_<myapp>.py -v` to confirm it builds.
4. Wire it into the CLI: append your app slug to the `APPS` tuple
   in `cli/_helpers.py`, add `_generate_<myapp>()` to
   `cli/_app_builders.py` (mirror `_generate_executives`), and add
   one call to it in `cli/json.py`'s `json_apply` body. `json
   apply` then emits + deploys your app alongside the others.
5. Add e2e tests mirroring `tests/e2e/test_exec_*.py` once a
   deployment exists.

## Related shape

- [How do I swap the SQL behind a dataset?](how-do-i-swap-dataset-sql.md)
  — same `DatasetContract` mechanism your app's datasets use.
- [How do I configure the deploy for my AWS account?](how-do-i-configure-the-deploy.md)
  — once your app slug is in the `APPS` tuple, `json apply --execute`
  emits + deploys it alongside the existing four every time.
- [How do I reskin the dashboards for my brand?](how-do-i-reskin-the-dashboards.md)
  — theme presets in `common/theme.py` apply to your app the same way.
