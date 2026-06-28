# DW.8.1 — models.py keep/delete audit (verified)

Produced by the `dw8-models-audit` workflow (2026-06-27): a models.py
inventory + 5 per-consumer read-classification agents → synthesis →
3-way adversarial verification. 155 dataclasses classified, zero
uncertain, zero report conflicts. This is the execution map for DW.8.1
(+ DW.8.2 emit-method removal, DW.8.3 theme, DW.8.5 drill — all one
connected component).

## The headline

App2 reads almost NOTHING from `common/models.py` — it reads the **tree
nodes** directly (`common/tree/*.py`), not the QS-emit models. The only
models.py shapes on App2's runtime read path are the `DatasetParameter`
family, consumed by `common/html/_sql_executor.py:110-137` to build SQL
bind defaults. Everything else exists solely to be built inside a tree
node's `.emit()`/`.emit_definition()`/`.emit_declaration()` and serialized
via `to_aws_json()` to the AWS QuickSight API — all dead once QS is gone.

## KEEP (App2 / `_sql_executor` reads them — strip `to_aws_json`, keep the dataclass + fields)

The `DatasetParameter` family (9), read in `_sql_executor.py`:

- `DatasetParameter`, `StringDatasetParameter`, `IntegerDatasetParameter`,
  `DecimalDatasetParameter`, `DateTimeDatasetParameter`
- `StringDatasetParameterDefaultValues`, `IntegerDatasetParameterDefaultValues`,
  `DecimalDatasetParameterDefaultValues`, `DateTimeDatasetParameterDefaultValues`

Plus (from the adversarial refutation — genuine keeps):

- **`DateTimeDefaultValues`** — stored field type on the KEPT tree node
  `DateTimeParam.default` (`tree/parameters.py:162`), constructed in
  non-emit l2ft sheet-builders, walked by App2 at runtime. KEEP.
- **`TimeRangeFilter` (the TREE node, `tree/filters.py:509`)** — App2-read
  (`Sheet.filters`, `structure.py:1641`). NAME COLLISION with the
  emit-only `models.TimeRangeFilter` (aliased `ModelTimeRangeFilter`,
  `filters.py:56`) which DOES delete. Target the alias + emit construction,
  NOT the tree class.

## DELETE (QS-emit-only — the dataclass + its `.emit()` producer both go)

139 confirmed. The cascade producers live in:

- `tree/visuals.py` — `Visual` + KPI/Table/BarChart/LineChart/Sankey
  `*Visual`/`*Configuration`/`*FieldWells`/`*SortConfiguration` + the
  Axis/ChartAxisLabel graph.
- `tree/fields.py` — `DimensionField`/`MeasureField` families +
  `NumericalAggregationFunction` + the NumberFormat graph (see coupling).
- `tree/parameters.py` — `*ParameterDeclaration` + `MappedDataSetParameter`.
- `tree/filters.py` — `Filter`/`CategoryFilter`/`NumericRangeFilter`/
  `TimeEqualityFilter`/`FilterGroup` (model side) + `*FilterScopeConfiguration`
  + the `DefaultFilterControl*` graph + `models.TimeRangeFilter`.
- `tree/controls.py` — every `Parameter*Control`/`Filter*Control` +
  `CascadingControl*`.
- `tree/structure.py` — `AnalysisDefinition`/`SheetDefinition`/`Layout`/
  `GridLayout*`/`DashboardPublishOptions`/`ResourcePermission` +
  `models.Analysis`/`models.Dashboard` (the emit targets).
- `tree/text_boxes.py` — `models.SheetTextBox`.
- `tree/actions.py` + `drill.py` — `VisualCustomAction*`/`CustomAction*`/
  `LocalNavigationConfiguration`/`*FilterOperation*`.
- `tree/_helpers.py` — `VisualTitleLabelOptions`/`VisualSubtitleLabelOptions`.
- `tree/datasets.py` + `dataset_contract.py` — `DataSetIdentifierDeclaration`
  + the DataSet graph (`DataSet`/`CustomSql`/`LogicalTable*`/
  `DataSetUsageConfiguration` — see coupling).
- `theme.py` (DW.8.3) — the Theme graph (12): `Theme`/`ThemeConfiguration`/
  `DataColorPalette`/`UIColorPalette`/`Tile*`/`Gutter`/`Margin`/
  `SheetStyle`/`FontFamily`/`Typography`.
- `models.py` only (DataSource graph, 7): `DataSource`/`DataSourceParameters`/
  `PostgreSqlParameters`/`OracleParameters`/`CredentialPair`/
  `DataSourceCredentials`/`SslProperties` — constructed only in
  `test_models.py` now (the production builder died with `common/datasource.py`).
- `Tag` — see coupling.

**Already-dead (zero construction site repo-wide, bonus removal):**
`PieChartVisual`/`PieChartConfiguration`/`PieChartAggregatedFieldWells`/
`PieChartFieldWells`/`DonutOptions`, `FreeFormLayoutElement`/
`FreeFormLayoutConfiguration`, `TableOptions`,
`AllSheetsFilterScopeConfiguration`, `AxisLinearScale`, `DateMeasureField`,
`LinkSharingConfiguration`. (`tree/__init__.py:74` already documented
"PieChartVisual is modeled but unused.")

## The 5 coupling traps (adversarial-pass findings — a class-only delete NameErrors App2 boot)

1. **`fields.py` module-level format constants.** `_USD_FORMAT` (`fields.py:505`)
   + `_integer_format` (`:525`) build `NumberFormatConfiguration` /
   `DecimalPlacesConfiguration` / `CurrencyDisplayFormatConfiguration` /
   `SeparatorConfiguration` / `ThousandSeparatorOptions` /
   `NumericFormatConfiguration` at MODULE LOAD. `fields.py` is imported by
   App2 (`_tree_fetcher`/`render` import `Dim`/`Measure`), so these run on
   every boot. App2 reads `Measure.currency`/`Measure.decimals` (the tree
   bools), NOT the format objects. **Remove the constants + the
   `FormatConfiguration=` kwargs in `Dim.emit`/`Measure.emit`/
   `emit_unaggregated_field` TOGETHER with the classes.**

2. **`dataset_contract.py::build_dataset` tail (`:589-622`).** Builds
   `InputColumn`/`PhysicalTable`/`CustomSql`/`LogicalTable*`/`DataSet`/
   `DataSetUsageConfiguration`/`Tag`. App2 runs `build_dataset` at serve
   boot (`cli/_html_serve.py::build_real_app`) for the `register_sql` /
   `register_contract` SIDE EFFECTS, then DISCARDS the returned `DataSet`.
   **Refactor `build_dataset` to keep the registry side-effects and drop
   the DataSet-construction tail** (not a `.emit()` method the naive recipe
   catches).

3. **`config.py::tags()` (`:135-139`).** Builds `Tag`, called from
   `build_dataset`'s tail (boot path) + the QS emit/theme paths. Strip with
   `Tag` (overlaps the deferred `cfg.aws.*` config cleanup).

4. **`TimeRangeFilter` name collision** — delete `models.TimeRangeFilter` /
   the `ModelTimeRangeFilter` alias / its emit construction; KEEP the
   `tree/filters.py` node.

5. **`DateTimeDefaultValues`** — KEEP (not a trap, a genuine keep the
   synthesis nearly missed): it's `DateTimeParam.default`'s field type.

## Execution order (top-down cascade, pyright as the oracle, green per stage)

The `.emit()` methods form a call cascade: `App.emit_analysis` →
`Analysis.emit_definition` → `Sheet.emit` → `visual.emit` → `field.emit`.
Delete top-down so each layer's producer is uncalled before it goes; let
pyright + the unit tier catch any miss.

1. **test_models.py** — almost entirely `to_aws_json` tests on deleted
   dataclasses → delete the emit-serialization tests (keep only what tests
   surviving shapes; the tag/datasource tests die).
2. **test_tree.py** — migrate validation/side-effect `emit_analysis()` →
   `validate()`; DELETE the emit-assertion tests (`m = emit_analysis()` →
   assert `.Definition`/`.AnalysisId`; the `emits_*` sheet/visual/param/
   filter tests). NOT circular tree-walks — the AWS shape is gone.
3. `structure.py` — delete `App.emit_analysis`/`emit_dashboard` +
   `Analysis.emit_definition` + `Sheet.emit`/`GridSlot.emit`/layout emit +
   `App._permissions`/`_theme_arn`/`_used_datasets`.
4. tree node `.emit()` removal — visuals/fields (+ format constants) /
   parameters/filters/controls/actions/drill/text_boxes + `datasets.emit_declaration`.
5. `theme.py` (DW.8.3) — delete `build_theme` + the Theme-model imports;
   KEEP `DEFAULT_PRESET`/`ThemePreset`/`resolve_l2_theme`.
6. `dataset_contract.py` — refactor `build_dataset` tail (coupling #2).
7. `models.py` — delete all 139 dataclasses + strip `to_aws_json` from the
   kept DatasetParameter family; drop the now-unused imports in tree modules.
8. Final: pyright-clean + full unit tier green + `grep to_aws_json src/` → 0.

## Execution progress (2026-06-27) + a scope correction the audit got wrong

Stages 1–5 DONE + committed green (`DW.8.1 (1..5/n)`): test_models teardown,
test_tree migration (5-reader workflow classification + AST transformer),
the App/structure emit methods, the four invariant relocations (4a:
currency→`__post_init__`, datetime-aggregation→`Measure.validate_column_type`,
filter-scope→`FilterGroup.validate_scope`, drill-source→`Drill.resolve_source_shapes`,
all walked by `App.validate()`), every leaf `.emit()` (4b, via ruff for the
~100 unused imports), and `build_theme` + the Theme model graph (DW.8.3). The
whole visual/field/filter/control/param/action/text-box/theme emit graph
(~120 classes) is gone. Bonus removal: the now-dead `app2_only` renderer-gate
flag (its only consumer was `Sheet.emit`).

Two things the audit MIS-MAPPED, found during execution:

- **Leaf `.emit()` methods carried DOMAIN invariants, not just QS shape.**
  Currency-on-non-numerical, the v11.24.1 datetime-aggregation guard,
  filter-group-must-be-scoped, and the drill-source shape-tag check all lived
  inside `.emit()`. Deleting blind would have dropped them. They relocated to
  `__post_init__` / the `validate()` walk (stage 4a). `field_label` (was
  `_field_label`) is NOT QS-only either — App2's `_tree_fetcher` imports it for
  headers; it survived + went public. `Drillable.emit` (returns a dict, not a
  QS model) is App2-consumed — KEPT.

- **The DataSet model graph is LOAD-BEARING — NOT a clean DELETE.** The audit
  put `DataSet`/`CustomSql`/`PhysicalTable`/`LogicalTable*`/`InputColumn`/
  `DataSetUsageConfiguration` in the 139-delete set. But `build_dataset` RETURNS
  a `DataSet`, and that return is consumed: every app's `app.py` reads
  `aws.DataSetId` off it to build the tree-Dataset ARN (l1/l2ft/investigation/
  executives + `sheets/app_info.py`), AND ~6 `tests/json/` files + `e2e/db/
  test_dataset_sql_smoke.py` validate dataset SQL by reading
  `ds.DataSetId` / `ds.PhysicalTableMap[..].CustomSql.SqlQuery` / `.Name`.
  `Tag` (via `config.tags()` called in `build_dataset`'s tail) +
  `dataset_permissions` are blocked behind the same refactor.

### Remaining work (stages 6+7 = ONE entangled unit, bigger than mapped)

The DataSet-graph removal is its own focused sub-stage (call it DW.8.1.b):

1. Refactor `build_dataset` to keep the registry side-effects (`register_sql` /
   `register_dataset_params` / `register_contract` / picker hint) and STOP
   building/returning the `DataSet` — return a tiny **`BuiltDataset`** struct
   (frozen dataclass / NamedTuple) carrying `.DataSetId` (the dataset_id), with
   room to grow if a builder later needs another field. The SQL is already the
   App2 source of truth via `register_sql`/`get_sql`. (Operator decision
   2026-06-27: the struct over a bare `str` — keeps the `aws.DataSetId` read
   sites unchanged, far fewer call-site edits.)
2. Update the return-type chain to `BuiltDataset`: every app `build_*_dataset()`
   (`-> DataSet` → `-> BuiltDataset`), each app's `build_all_*`
   (`list[DataSet]` → `list[BuiltDataset]`). The
   `{vid: Dataset(identifier=vid, arn=cfg.aws.dataset_arn(aws.DataSetId))}`
   dict-builders in all 4 apps + app_info + picker_datasets stay AS-IS
   (`aws.DataSetId` still resolves — that's the point of the struct).
3. Migrate the dataset-SQL test surface (`tests/json/test_dataset_sql_contract_projection`,
   `test_executives`, `test_investigation`, `test_l2_flow_tracing`, `test_app_info`,
   `test_dataset_parameters`, `tests/e2e/db/test_dataset_sql_smoke`) off
   `DataSet.PhysicalTableMap/CustomSql/DataSetId` onto `get_sql(id)` /
   `get_contract(id)` / the id string.
4. THEN delete the DataSet graph + `Tag` + `config.tags()` + `dataset_permissions`
   + the DataSource graph (test-only now) from models.py, and strip `to_aws_json`
   from the kept `DatasetParameter` family.
5. Final: `grep to_aws_json src/` → 0 + full suite green.

### DW.8.1.b EXECUTION — DONE (2026-06-28), + a SECOND scope correction

DW.8.1.b shipped in two green commits:

- **1/2 `0242e3f4`** — `build_dataset` returns a frozen `BuiltDataset`
  (`DataSetId` + `visual_identifier` + a SNAPSHOT of this build's `sql` /
  `contract` / `dataset_params`) instead of the AWS `DataSet`. Registry
  side-effects unchanged (App2 runtime still resolves by `visual_identifier`).
  **Fields, not read-through-registry properties** — the registry is
  last-write-wins by key, so the app_info per-dialect liveness probe (builds
  PG + Oracle under the same `visual_identifier` in one test) would read both
  handles as the LAST build's SQL. The old `DataSet` object held its own
  `CustomSql` the same way; a property version shipped + the app_info test
  caught it red, hence the switch to snapshot fields. ~70 `-> DataSet`
  annotations flipped; ~19 test files migrated (`.CustomSql.SqlQuery`->`.sql`,
  `.DatasetParameters`->`.dataset_params`, `.Columns`/`col.Name`->`.contract.
  columns`/`col.name`). Two tautology traps avoided (DW.1 playbook): l2ft +
  inv "emitted cols == contract cols" checks would become `expected ==
  expected` once `.contract` IS the registered contract — l2ft pair (on the
  legacy `build_chains`/`build_exc_*` builders, NOT in any `build_all`)
  retargeted to the projection check; inv triple deleted as redundant with
  `test_dataset_sql_contract_projection`.
- **2/2 `9cf971fa`** — deleted the DataSet graph (DataSet/CustomSql/
  PhysicalTable/LogicalTable/LogicalTableSource/InputColumn/
  DataSetUsageConfiguration) + the DataSource graph (DataSource + 6 helpers)
  + `to_input_column`/`to_input_columns` + the InputColumn-wire-shape test.
  pyright src+tests = 0 dangling refs.

**SECOND scope correction — the recipe's step 4 mis-grouped Tag/permissions.**
Step 4 listed `Tag` + `config.tags()` + `dataset_permissions` +
`ResourcePermission` as part of the DataSet-graph "clean delete". They are
NOT independently deletable: `Tag` and `ResourcePermission` are field types
on **Theme / Analysis / Dashboard** (`Tags:` / `Permissions:` fields), which
DW.8.1.b leaves alive. They're coupled to the emit-graph gut (below), and
moved there. DataSource was a leaf referencer of both, so it deleted fine in
2/2. (Same lesson as the FIRST correction: "clean delete" claims that hinge
on a type's field-references need the reference graph checked, not just the
constructor call sites.)

### REMAINING DW.8.1 (NOT .b) — the emit-graph gut (verified cascade map)

`to_aws_json -> 0` (step 5 above) is the gate for ALL of DW.8.1, not just
.b — it needs Theme/Analysis/Dashboard gone, which cascades to the whole
Visual/Filter/Control/Layout/FieldWell/ParameterDeclaration/SheetDefinition
graph (~120 models.py classes). After DW.8.1.b the 3 surviving `to_aws_json`
defs are exactly Theme / Analysis / Dashboard. Verified dead-producer map
(delete these FIRST, then the model classes fall unreferenced — type oracle):

- **`common/aging.py` + `tests/unit/test_aging.py`** — `aging_bar_visual`
  builds a `models.Visual(BarChartVisual=...)` graph for the DELETED AR/PR
  apps (M.4.3/M.4.4). Zero src callers (only its own test). DELETE both →
  frees BarChart*/Axis*/Dimension/Measure/ColumnIdentifier/Visual classes.
- **`common/tree/_helpers.py::title_label` / `subtitle_label`** — build
  `VisualTitle/SubtitleLabelOptions`. Zero Python callers (the helpers.py
  "title_label" greps are all JS `analysis_visual_title_label` selector
  strings). DELETE → frees those two option classes.
- **`common/drill.py::cross_sheet_drill` / `set_drill_parameters`** —
  build `models.VisualCustomAction`. Zero SRC callers; consumed only by
  `tests/json/test_drill.py` (K.2 drill-param shape-validation — a DOMAIN
  invariant, [[project_drill_param_shape_typing]]). **DELICATE**: the live
  App2 path keeps `DrillStaticDateTime`/`DrillParam`/`DrillSourceField`/
  `DrillResetSentinel`/`field_source` (render.py + actions.py consume them);
  deleting the two QS-emit fns requires re-pointing test_drill's shape-check
  coverage at the live `field_source` / `Drill.resolve_source_shapes` path
  so the K.2 invariant stays gated. NOT a mechanical delete — same
  emit-vs-embedded-invariant shape as stages 1-5's leaf `.emit()`.
- **`config.py::tags()` + `Tag`** and **`dataset_contract.py::
  dataset_permissions` + `ResourcePermission`** — die WITH Theme/Analysis/
  Dashboard (their only field-type users). Test consumers to retire:
  `test_config_proxy_views` (`cfg.aws.tags()`), `test_models` already-trim
  (docstring only). `dataset_permissions` has no caller post-DW.8.1.b.
- **models.py mass-delete** — after the above, delete Theme + the Theme
  config graph (DataColorPalette/UIColorPalette/Tile*/SheetStyle/Typography/
  FontFamily/ThemeConfiguration) + Analysis + Dashboard + AnalysisDefinition
  + SheetDefinition + Layout* + every Visual subtype + FieldWell/Configuration
  + Filter/Control + ParameterDeclaration family + VisualCustomAction family
  + Tag + ResourcePermission. KEEP: the DatasetParameter family (9) +
  `DateTimeDefaultValues` + `_check_static_values_cap`/`_strip_nones`. Run
  pyright as the per-delete oracle; expect test_tree/test_models emit-shape
  test consumers to need retiring (dead QS-wire-shape).
- **Final**: `grep to_aws_json src/` -> 0, rewrite the models.py module
  docstring (drops "Theme/DataSet/Analysis ... to_aws_json"), full suite green.

This remainder carries the drill K.2-invariant delicacy + a broad test-file
retirement surface — fits the DW.8 box's recommended "fresh context +
workflow + adversarial review" treatment.
