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
