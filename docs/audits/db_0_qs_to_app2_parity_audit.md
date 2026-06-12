# DB.0 — QS→App2 parity audit

**Date:** 2026-06-12
**Phase:** DB.0 (audit — Phase DB feeds DB.1 fix scope)
**Goal:** measure App2 coverage of every QS-emit-able Visual feature, so DB.1 closes the lurking-gap shape DA fixed for `conditional_formatting`.

## Methodology

Walked every Visual class in `common/tree/visuals.py` (+ `text_boxes.py`). For each:

1. Inventoried the QS JSON fields its `emit()` method sets (the `Visual(...VisualKind=...(VisualId=, Title=, Subtitle=, ChartConfiguration=, Actions=, ConditionalFormatting=, ...))` shape).
2. Walked the App2 consumer chain: `_VisualPlan` fields + `_chart_meta` / `_kpi_*` / `_table_column_meta` extractors → `shape_*` adapters → `bootstrap.js::render*` renderers.
3. Cross-referenced each emit field against the consumers. Anything emitted by QS but not consumed by App2 is a gap.

A gap is **shipping** when at least one app callsite actually uses the feature; **latent** when the tree primitive exists but no app uses it. Both go on the DB.1 fix list — latent gaps are cheap to close while we're already in the area.

## Visual kinds in scope

`KPI`, `Table`, `BarChart`, `LineChart`, `Sankey`, `ForceGraph` (App2-only by design — `emit()` raises; no parity to check), `TextBox` (chrome, not a SQL visual — covered by the `project_qs_text_box_rich_formatting` memory's Tailwind parity gate; out of scope for this audit but flagged for completeness).

## Coverage table

Legend: ✅ honored / ⚠ partial / 🟥 shipping gap / 🟨 latent gap (tree exists, no app callsite) / 🟫 by-design (QS-only feature App2 deliberately doesn't render).

### KPI

| QS emit field | App2 consumer | Verdict |
|---|---|---|
| `VisualId` | section ID in HTML | ✅ |
| `Title` | `<h2>` | ✅ |
| `Subtitle` | `.subtitle` | ✅ |
| `ConditionalFormatting.PrimaryValue` (zero indicator) | `_VisualPlan.kpi_zero_is_healthy` → `shape_kpi(state_icon/state_color)` → `renderKPI` | ✅ |
| `ConditionalFormatting.PrimaryValue` (sign indicator) | `_VisualPlan.kpi_inflow_is_healthy` → `shape_kpi` → `renderKPI` | ✅ |
| `ConditionalFormatting.PrimaryValue` (threshold banding) | `_VisualPlan.kpi_threshold_banding` → `shape_kpi` → `renderKPI` | ✅ |
| `KPIFieldWells.Values[0]` | KPI value via SQL projection + `_kpi_format` | ✅ |
| `KPIFieldWells.TargetValues=[]` + `TrendGroups=[]` | unused by both sides (QS quirk: required-empty per M.4.4.8) | 🟫 |
| `KPIOptions.Comparison`/`SecondaryValueFontConfiguration` | QS-only — no target → no comparison rendered on either side | 🟫 |
| `KPIOptions.Sparkline.Visibility=HIDDEN` (post-DB.1.3) | App2 renderKPI shows just the number — matches QS | ✅ resolved by flipping the tree's hardcoded `Visibility` from `VISIBLE` to `HIDDEN`. Pre-DB.1.3 the tree emitted VISIBLE for schema-validation appeasement (per M.4.4.8 — QS rejects partial KPIOptions), but `TrendGroups=[]` left QS with nothing to plot so it rendered an empty placeholder. HIDDEN tells QS not to reserve the UI space; App2 ≡ QS at the visible-render level. |
| **`KPIOptions.VisualLayoutOptions.StandardLayout.Type=VERTICAL`** | App2 has its own vertical layout | ✅ by-construction |
| Author-extensible KPI conditional formatting | tree only emits the 3 named indicators; arbitrary CF (e.g. text-color on threshold) **not exposed** | 🟨 **latent surface** — if a future app needs arbitrary KPI CF, both tree-level emit AND App2 consumer would need extension. Worth noting in DB.2 gate so we don't drift. |

**KPI verdict:** clean for current usage. One latent gap worth visual-checking (sparkline). DB.5 type gate (DA-equivalent) blocks arbitrary CF from sneaking in untracked.

### Table

| QS emit field | App2 consumer | Verdict |
|---|---|---|
| `VisualId` / `Title` / `Subtitle` | section + `<h2>` + `.subtitle` | ✅ |
| `FieldWells.UnaggregatedFieldWells.Values` (`columns` mode) | SQL projection + `_table_column_meta(labels, formats, hidden)` → `renderTable` | ✅ |
| `FieldWells.AggregatedFieldWells.GroupBy`+`Values` | same via SQL | ✅ |
| `FieldOptions.SelectedFieldOptions[*].CustomLabel` | `column_labels` → `col.label` → `<th>` | ✅ |
| `FieldOptions.SelectedFieldOptions[*]` (order) | column order in SQL projection | ✅ by-construction |
| **`FieldOptions.SelectedFieldOptions[*].Width`** | App2 doesn't read column widths | 🟨 **latent** — no app uses explicit widths today; tree primitive doesn't even expose it. Cosmetic. |
| `SortConfiguration.RowSort` (single column, ASC/DESC) | `_extract_table_sort_default` → URL `sort_column` param → SQL `ORDER BY` | ✅ |
| `SortConfiguration.RowSort` (multi-column) | tree only emits single-column sort | 🟨 latent both sides — same shape. |
| `Actions[*]` (Drill / SameSheetFilter) | `_serialize_table_row_drills` → `data-row-drills` JSON → `wireRowDrills` | ✅ (DA.4 wires + DA.5 gate; row-drill MENU contract restored at `279c52c8`) |
| **`Actions[*]` (SameSheetFilter — highlight-without-narrowing)** | App2 deprecates this construct; not rendered | 🟫 **operator-locked deprecation** per CLAUDE.md filter-authoring section. SameSheetFilter is a QS-only highlight idiom. Documented exemption. |
| `ConditionalFormatting[*]` (Drillable cells) | `column_decoration` → `cell-accent`/`cell-accent-menu` (DA) | ✅ (DA.2/DA.3) |
| `metadata_popup` → row-payload metadata column (tree-level boolean) | `data-metadata-popup="1"` on section + ⋯ menu entry | ✅ (CY.4/CY.6) |

**Table verdict:** clean post-DA. Only latent gaps are tree-level features no app uses (column widths, multi-column sort).

### BarChart

| QS emit field | App2 consumer | Verdict |
|---|---|---|
| `VisualId` / `Title` / `Subtitle` | ✅ | ✅ |
| `BarChartAggregatedFieldWells.Category` | SQL projection col 0 | ✅ |
| `BarChartAggregatedFieldWells.Values` | SQL projection col 1 + `_chart_meta.value_format` | ✅ |
| `BarChartAggregatedFieldWells.Colors` | `_chart_meta.series_column_name` → `shape_bar_chart(series_column)` → multi-series shape | ✅ |
| `BarsArrangement=STACKED/STACKED_PERCENT` | `_chart_meta.stacked` → `shape_bar_chart(stacked=True)` → `renderBarChart` stacks | ✅ |
| **`Orientation=HORIZONTAL`** | App2's `renderBarChart` has NO orientation hook — always renders vertical bars regardless | 🟥 **shipping gap**. **7 callsites across 3 apps** use `orientation="HORIZONTAL"`: `apps/l1_dashboard/app.py:1121, 1387, 1515`; `apps/executives/app.py:400, 420`; `apps/l2_flow_tracing/app.py:1298`. QS renders horizontal (bars sweep right); App2 renders vertical (bars sweep up). Direct visual divergence operator can see today. |
| `CategoryLabelOptions.CustomLabel` | `_chart_meta.x_label` (in vertical mode this is the X axis) → `data.x_label` → `renderBarChart` | ✅ for vertical, **partial** for horizontal (when QS renders horizontal, "category" is the Y axis — App2 paints it as X, mislabeled if/when App2 supports horizontal). |
| `ValueLabelOptions.CustomLabel` | `_chart_meta.y_label` | ✅ for vertical, same partial caveat |
| **`ColorLabelOptions.CustomLabel`** | App2 has no color-legend label | 🟥 **shipping gap**. **4 callsites** use `color_label=`: `apps/l1_dashboard/app.py:1375, 1503`; `apps/executives/app.py:614, 722`. QS shows e.g. "Rail" / "Transfer Type" as the legend header; App2's legend shows just the series values. |
| `ValueAxis.Scale.Logarithmic` (`log_scale=True`) | `_chart_meta.log_scale` → `data.log_scale` → `renderBarChart` (d3.scaleLog) | ✅ (BQ.5) |
| `SortConfiguration` (FieldSort) | App2 chart-side sort follows SQL ORDER BY (initial direction emitted to QS, App2 sorts via SQL) | ⚠ partial — emit-vs-consume both work but the URL-driven re-sort UI is Table-only on App2 |
| `Actions[*]` | App2 charts have no clickable-bar drill today (X.2.e leaves as future work) | 🟨 **latent** both sides (QS-side Bar drills exist but no app uses them) |

**BarChart verdict:** **two shipping gaps** — `Orientation=HORIZONTAL` (7 sites) + `ColorLabelOptions` (4 sites). Both are visually obvious deltas the operator can see today between QS and App2. **Top DB.1 priority.**

### LineChart

| QS emit field | App2 consumer | Verdict |
|---|---|---|
| `VisualId` / `Title` / `Subtitle` | ✅ | ✅ |
| `LineChartAggregatedFieldWells.Category`/`Values`/`Colors` | `_chart_meta` → `shape_line_chart` → `renderLineChart` | ✅ |
| **`Type=LINE/AREA/STACKED_AREA`** | App2's `renderLineChart` has NO `Type` hook — always renders plain line | 🟨 **latent** — zero `chart_type=` callsites in any app; tree primitive exists for future use. If a future app declares `chart_type="AREA"` it silently renders as LINE on App2. Worth closing now while we're in the area. |
| `XAxisLabelOptions.CustomLabel` | `_chart_meta.x_label` | ✅ |
| `PrimaryYAxisLabelOptions.CustomLabel` | `_chart_meta.y_label` | ✅ |
| `SortConfiguration` | same as BarChart — emit happens, consumer is SQL ORDER BY | ⚠ partial |
| `Actions[*]` | latent on both sides | 🟨 |

**LineChart verdict:** one latent gap (`chart_type`). No shipping divergence today.

### Sankey

| QS emit field | App2 consumer | Verdict |
|---|---|---|
| `VisualId` / `Title` / `Subtitle` | ✅ | ✅ |
| `SankeyDiagramAggregatedFieldWells.Source` / `Destination` / `Weight` | SQL projection cols 0/1/2 → `shape_sankey({nodes, links})` → `renderSankey` (d3-sankey) | ✅ |
| `SortConfiguration.WeightSort` (DESC) | App2 sorts links by value DESC by construction | ✅ |
| **`SortConfiguration.SourceItemsLimit.ItemsLimit`** | App2's `shape_sankey` ignores items_limit — emits ALL nodes/links from the SQL result | 🟥 **shipping gap candidate**. QS caps at the tree's `items_limit` value with an `(others)` rollup. App2 doesn't cap; behavior matches when the SQL itself already caps (Money Trail uses a `LIMIT N` in CustomSql), but a `Sankey(items_limit=N)` declaration without a matching SQL `LIMIT` would diverge — QS caps, App2 shows full universe. **Need to confirm whether any app relies on tree-level `items_limit` instead of SQL LIMIT.** |
| `SortConfiguration.DestinationItemsLimit` | same | 🟥 same |
| `Actions[*]` | latent both sides | 🟨 |

**Sankey verdict:** items_limit gap is a candidate — depends on whether any app uses the tree-level cap. Quick grep of `Sankey(items_limit=` will tell us.

### ForceGraph

Out of scope — `emit()` raises. App2-only by design. Enhancement in `PARITY_BREAKS`.

### TextBox

Out of scope here — chrome, not a SQL visual. Covered by `project_qs_text_box_rich_formatting` memory's existing Tailwind parity gate.

## Summary by severity

### 🟥 Shipping gaps (visible divergence today)

1. **BarChart `Orientation=HORIZONTAL` — 7 callsites.** App2 renders vertical; QS renders horizontal. Top priority.
2. **BarChart `ColorLabelOptions.CustomLabel` — 4 callsites.** App2 legend lacks the author's label header.
3. **Sankey `items_limit` — 4 callsites confirmed shipping.** `apps/investigation/app.py:716, 955, 979` + `apps/l2_flow_tracing/app.py:1167`. QS caps + rolls up to `(others)`; App2 shows all rows. Visual divergence today on Investigation Money Trail + L2FT Chains Sankey when data exceeds the cap.

### 🟨 Latent gaps (tree primitive exists, no app callsite)

1. ~~KPI `Sparkline`~~ — **resolved DB.1.3**: tree now emits `Visibility=HIDDEN`. App2 ≡ QS today (both show just the value). Sparkline data source design (TrendGroups wiring + App2 trend renderer) flagged as future enhancement — operator likes the idea but wants to land it later, not in this phase.
2. **KPI arbitrary `ConditionalFormatting`** — tree only exposes the 3 named indicators; future drift surface.
3. **Table `FieldOptions.Width`** — column widths not exposed on tree.
4. **Table multi-column sort** — tree single-column only.
5. **LineChart `Type=AREA/STACKED_AREA`** — tree exposes it; renderer ignores.
6. **Chart `Actions[*]` (clickable bar/point drill)** — neither side wires drills onto chart marks.

### 🟫 By-design / operator-locked

- KPI `TargetValues` + `TrendGroups` empties (M.4.4.8 QS quirk).
- Table `SameSheetFilter` highlight-without-narrowing (operator-deprecated for filter intent).
- ForceGraph — App2-only by design (in `PARITY_BREAKS`).

## DB.1 scope proposal

In priority order:

1. **BarChart orientation.** `_ChartMeta` grows `orientation: Literal["VERTICAL", "HORIZONTAL"]`. `shape_bar_chart` forwards. `renderBarChart` branches: horizontal swaps the x/y scales + axes + bar geometry. Unit + Playwright test on a `HORIZONTAL` fixture. ~3hr (renderer work is the bulk).
2. **BarChart color label.** `_ChartMeta` grows `color_label: str | None`. `shape_bar_chart` forwards as `series_label`. `renderBarChart` paints it above the legend list. ~30min.
3. **Sankey items_limit.** 4 shipping callsites confirmed (`apps/investigation/app.py:716, 955, 979` + `apps/l2_flow_tracing/app.py:1167`). `shape_sankey` walks nodes/links and caps Source/Destination at the limit per QS's `OtherCategories: INCLUDE` shape (link the trimmed source/dest to a synthetic `(others)` node, aggregating remaining weight). `_VisualPlan` grows `sankey_items_limit: int | None`. ~1.5hr.
4. **LineChart `Type`.** `_ChartMeta` grows `type: Literal["LINE", "AREA", "STACKED_AREA"]`. `shape_line_chart` forwards. `renderLineChart` branches on type (AREA = area fill below line; STACKED_AREA = d3.stack + area per series). ~2hr (renderer + tests).
5. ~~KPI Sparkline plumbing~~ — operator-locked at the sparkline conversation: don't plumb for now. Tree flipped to `Visibility=HIDDEN` (`KPI.emit()`) so both sides show just the value. Future-work flag: "App2 ⊇ QS richer graphics" can wire trend data source + renderer when there's a use case to push.

Total estimate: ~7-10hr including tests. Splits cleanly into 4-5 DB.1.x sub-leaves.

## DB.2 completeness gate (preview)

Per the Phase DB lock: "the construction-time completeness gate fails any new mismatch at the wiring site."

Approach: at `App.resolve_auto_ids()` time, walk every Visual, derive the set of QS JSON paths its `emit()` would set, and cross-check against a **typed registry** of App2-consumed keys (one entry per `_VisualPlan` field + `shape_*` parameter). Any emit-path that's not in the registry raises `ValueError` at construction with the Visual + path. The registry IS the typed source of truth — adding a Visual field later forces the author to add a registry entry, so a DA-shape gap can't sneak back in.

Open question for DB.2: registry granularity. Per-field (one entry per Visual class field)? Per-JSON-path (e.g. `"BarChart.ChartConfiguration.Orientation"`)? Per-`_VisualPlan` attribute? Worth a brief operator-confirm at DB.2 start.

## Operator-confirm gates before DB.1 fires

1. **Lock the priority order above.** Is BarChart orientation actually top priority, or do you want to start with the smaller wins (color label, Type) first?
2. ~~Sankey items_limit~~ resolved during audit — 4 callsites confirmed shipping, scoped into DB.1.3 above.
3. ~~KPI Sparkline~~ — operator-locked 2026-06-12: don't plumb sparkline data source for this phase; flip tree to `Visibility=HIDDEN` and land the trend-data design as a future enhancement.
4. **DB.2 registry granularity:** per-field vs. per-JSON-path?
