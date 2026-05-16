# QuickSight quirks log

Bugs, undocumented behaviors, and silent-failure modes we've hit
while building the four shipped dashboards. Each entry captures the
observed behavior, the user-visible symptom, the workaround we
ship, and the suggested fix on the QuickSight side.

This page exists for two reasons:

1. **Defect reports.** We've collected enough QS-side issues that
   filing them with the QuickSight team needs a single canonical
   reference, not bug-by-bug archeology across this repo's commit
   history.
2. **Operator survival kit.** When a dashboard renders blank or a
   control behaves oddly, scan this page first — most of the
   "didn't I just fix that?" moments are a returning instance of one
   of these classes.

---

## ⚠️ Read this first — the worst footgun

**URL-parameter writes don't reach the dataset substitution layer.
Controls populate, analysis-level filters work, but
`MappedDataSetParameters` bridges (which carry params from analysis
into the dataset's `<<$paramName>>` SQL substitution) do NOT fire on
initial URL-driven load. The data ignores the URL value until a
manual widget interaction commits it.**

Every cross-app drill, every embedded deep link, every
``CustomActionURLOperation`` that targets a dataset-bridged
parameter hits this. The control widget shows the URL value (so the
analyst thinks the filter is applied) but the dataset SQL runs with
the parameter at its analysis-default sentinel. They see the
unfiltered universe under a label that says it's filtered.

**This was studied exhaustively in Y.1.k → Y.1.p. Three
analysis-level reference shapes were tried — `CategoryFilter` using
the param as match value, an echo column + tautological filter,
and a calc field with `${param}` in its expression (LuisBorrego's
community workaround). All three failed identically. The bug is
QS-side; no JSON shape on our end works around it.**

**What we've done to minimize the damage**

- Cross-app drills (the K.4.7 family) were dropped from
  Investigation → other apps because the destination's controls
  could never be made to read right. We kept *intra-app* drills
  where the data signal is the contract and the control mismatch
  is a smaller papercut.
- Sheets that receive a URL-parameter write carry a description
  paragraph telling analysts "trust the chart, not the control
  text". The L1 Pending Aging → Transactions drill (v8.5.7) and
  every cross-sheet drill since carry that callout.
- Drill-write date params snap the destination's date picker
  visibly to the new value (entry 2.2 below) — that's not a fix
  for the control-sync defect, it's the price we pay to keep the
  data + control in agreement on date params specifically. The
  picker visibly jerking is the lesser evil vs. silently filtering
  to "no data".
- The L1 cross-sheet drill always writes a wide ``[1990, 2099]``
  date range so the destination's universal filter can never narrow
  the target row out of view (v8.5.7). This combines with 2.3's
  "static literals only" restriction — there's no way to write
  "rolling 7 days" via a drill, so we write the widest possible
  window every time and accept that the picker visibly snaps to it.

**If you're considering a new cross-sheet or embed-driven parameter
write, assume the destination control will lie. Plan the UX around
that.** The detailed entries on this defect class are **2.1**, **2.2**,
and **2.3** below.

---

## 1. Silent rendering failures

### 1.1 Spinner-forever — entire dashboard stuck, no error surfaced

**Observed.** Every visual on every sheet shows the loading spinner
indefinitely. No error banner, no narrowing-to-zero filter, no
API-level error from `describe-dashboard`. Datasets describe as
`CREATION_SUCCESSFUL` and return rows when queried directly through
the QS data-source connection. The database itself responds in
milliseconds.

**Diagnostic ladder we use:**

1. Verify the database returns rows for the underlying SQL via
   `psycopg2` / `oracledb` — proves the data is there.
2. Verify `describe_data_set` returns `CREATION_SUCCESSFUL` —
   proves the dataset exists.
3. Open the dashboard in a fresh incognito window — rules out
   browser cache.
4. Assume QuickSight itself is the broken layer. Either wait it out
   (sometimes clears on its own) or force a full
   delete-then-create of the entire QS resource graph (theme,
   datasource, all datasets, analysis, dashboard) plus a clean
   re-seed + matview refresh.

**Workaround.** Capture the diagnostic ladder in CLAUDE.md so we
don't keep re-checking the SQL or the data when the data is fine.

**Suggested fix.** Either surface a useful error to the user when
the QS rendering pipeline stalls, or expose a `dashboard health`
endpoint so the cause is debuggable without blind delete-then-create.

---

### 1.2 KPI silently renders blank with a partially-populated `KPIOptions`

**Observed.** `CreateAnalysis` accepts a `KPIOptions` shape that's
missing a few fields the QS UI always populates. The KPI then
renders as a blank tile in the deployed dashboard — no error at
create time, no warning in the UI. A separately-emitted error
message ("Only `PrimaryValueFontSize` display property...") shows
up only when emitting *certain* partial shapes — not all of them.

**Workaround.** Mirror exactly what QS UI defaults to: emit the
full `KPIOptions` block with `Comparison`, `PrimaryValueDisplayType`,
`SecondaryValueFontConfiguration`, `TargetValues=[]`,
`TrendGroups=[]` even when not used. See `common/tree/visuals.py`
`KPI.emit()` for the canonical shape (M.4.4.8).

**Suggested fix.** Document `KPIOptions` as required (not optional)
on `KPIVisual.ChartConfiguration`, OR make the API server-side fill
in the missing defaults when CLI sends a partial shape.

---

### 1.3 Filter binding to a parameter the analyst can't set silently
empties every visual

**Observed.** A `CategoryFilter.with_parameter(...)` /
`TimeEqualityFilter` / `NumericRangeFilter` (with
`minimum_parameter` / `maximum_parameter`) bound to a parameter
that has *no* sheet control becomes a `WHERE clause matches nothing`
at runtime. Every visual that depends on the filter renders
empty. No error.

**Workaround.** Tree validator
(`App._validate_filter_param_settability`) walks the tree at
construction time and rejects any parameter-bound filter whose
parameter isn't reachable from a sheet control. Catches the bug
class at emit time, not at deploy time.

**Suggested fix.** QS could surface a "filter parameter is
unreachable" warning in the UI when the analyst hovers the empty
visual.

---

## 2. Drill / parameter-write quirks

### 2.1 URL parameter writes don't reach the dataset substitution layer (the cross-app footgun)

**Observed.** When a deep-link URL sets a parameter via the
`p.<name>=<value>` query-string convention:

- ✅ The control widget DOES populate from the URL (post-Y.1.p
  finding — earlier K.4.7 read of "controls stay All" was wrong;
  controls do show the URL value).
- ✅ Analysis-level filters that take a parameter directly (e.g.
  `CategoryFilter.with_parameter`, `NumericRangeFilter.with_parameter`,
  `TimeRangeFilter`) DO read the URL value.
- ❌ Analysis parameters bridged to dataset-level parameters via
  `MappedDataSetParameters` DO NOT propagate the URL value into the
  dataset's `<<$paramName>>` substitution. The bridge stays at the
  parameter's analysis-default sentinel until the user manually
  interacts with the widget.

**Practical impact.** Phase Y SQL pushdown is fine for *manual
interaction* (the Y.1 σ-slider spike works — drag the slider, the
pushdown runs). But **cross-app drills that need to land on a sheet
with the dataset cascade pre-narrowed by URL params will not narrow
on initial load.** The destination dashboard renders the unfiltered
universe; the analyst then has to re-pick the values manually.

**Mechanism, by way of pg_stat_statements.** Probe the deployed
dataset SQL via `scripts/qs_substitution_probe.py inspect` (post-deploy
the dataset has the right `<<$pKey>>` placeholders + DatasetParameters
declared). Probe pg_stat_statements with `--filter <sheetId>` after a
URL-stamp page load: the query fires with the placeholders bound to
the analysis-level parameter's *default* value, not the URL value.
After a manual widget interaction, the query re-fires with the URL
value bound. Confirmed across PG; Oracle path same shape.

**Workarounds attempted (Y.1.p, all failed):**

- **B1**: replace OR-cascade WHERE with a `_meta_match_value`
  CASE projection + analysis-level `CategoryFilter.with_parameter`
  on that column. Filter SQL emitted correctly; bridge still bound
  default on URL load.
- **B2**: B1 + `_meta_key_echo` echo column + tautological
  `CategoryFilter.with_parameter` filter on the echo column to
  give pKey its own analysis-level reference. Same outcome.
- **B3**: replace the parameter-using filters with analysis-level
  `CalcField` expressions referencing `${param}` (LuisBorrego's
  community workaround pattern), then filter on the calc field
  with literal "match" values. Same outcome — bridges still
  bound defaults on URL load.

After all three, pg_stat_statements showed the SQL was emitted with
the right shape (calc fields pushed down as `CASE WHEN _meta_X = $N
THEN $M ELSE $K END IN ($L)`), but `$N` (the parameter bind)
remained the sentinel default until manual interaction.

**Workaround we actually use.** Cross-app URL-driven drills target
*analysis-level* params only — not bridged dataset params. Where the
data narrowing has to happen at the dataset level (the L2FT
cascade), accept that URL stamping won't narrow on initial load and
build the UX around manual interaction (text-field input + Enter,
dropdown picks). Sheet descriptions tell analysts "pick the Key, type
the Value" rather than expecting a clickable cross-app link.

**Suggested fix.** Make MappedDataSetParameters bridges fire on
analysis-parameter VALUE changes including URL-driven initial-load
writes — not just on widget interaction events. Or expose a
`setParameters` method on the embedding SDK that triggers the
bridge synchronously.

**Diagnostic harness.** `scripts/qs_substitution_probe.py` was built
during Y.1.o specifically for this class of bug. `inspect` dumps the
deployed dataset's CustomSQL + DataSetParameters. `snapshot` /
`diff` capture pg_stat_statements deltas across user actions. Reach
for it before manual screenshot debugging on any future
parameter-binding issue.

---

### 2.2 In-app drill writes to a `DateTimeParam` snap the picker
visibly to the static value

**Observed (v8.5.7).** `SetParametersOperation.ParameterValueConfigurations`
that writes a `CustomValuesConfiguration.CustomValues.DateTimeValues`
to a destination `DateTimeParam` correctly updates the parameter
AND snaps the destination's picker control to that value. There's
no way to "write the parameter value but leave the picker alone"
or "widen the parameter without changing the picker".

**Workaround.** When a cross-sheet drill needs the destination's
universal date filter to be wide enough that the target row is
visible (e.g. drilling a stuck-pending leg older than 7 days into
the date-scoped Transactions sheet), we write
`pL1DateStart=1990-01-01` and `pL1DateEnd=2099-12-31` — the
"all time" sentinel pair. The picker visibly snaps to those values.
Documented as a UX wart.

**Suggested fix.** Either (a) allow writes that update the param
without re-rendering the picker control, or (b) expose a
`SetParameters` operation that takes an *expression* (e.g.
`addDateTime(-N, 'DD', truncDate('DD', now()))`) so the rolling
default can re-anchor without a static literal showing in the
picker.

---

### 2.3 `SetParametersOperation` only accepts static values or
column refs — no `now()` or rolling-date expressions

**Observed.** Drill writes to `DateTimeParam` destinations can only
carry one of: a `SourceField` reading from a clicked row column, or
a `CustomValues.DateTimeValues=[<ISO-8601 literal>]` static value.
The `RollingDate.Expression` shape that `ParameterDeclaration.DefaultValues`
accepts is NOT accepted as a `SetParametersOperation` value — there's
no way to write "today minus 7 days" via a drill.

**Workaround.** Use the static far-past + far-future literals
(see 2.2). Authors who want a rolling drill-write would have to
build it from the embedding SDK at click time, which defeats the
purpose of declarative drill actions.

**Suggested fix.** Allow `RollingDate.Expression` as a value type
on `SetParametersOperation`.

---

### 2.4 Sankey right-click drill is non-functional in practice

**Observed.** Wiring a `Drill` action to a Sankey visual's
right-click trigger emits successfully but the menu either doesn't
appear or doesn't fire on click. Verified across multiple
configurations.

**Workaround.** Investigation Account Network sheet uses two
separate left-click Sankeys (inbound + outbound) instead of one
bidirectional Sankey with right-click drill. Pattern documented
in `walkthroughs/investigation/what-does-this-accounts-money-network-look-like.md`.

**Suggested fix.** Either fix the right-click drill on Sankey or
remove the option from the API so it doesn't look supported.

---

## 3. Control / widget UX quirks

### 3.1 `ParameterDropDownControl` only opens on the inner grey bar

**Observed.** The dropdown widget renders a wider visible area
than the actual click target. Clicking the visible outer edge of
the control does nothing — the popover only opens when the click
hits the narrow grey bar in the middle of the control. Confused
users assume the dropdown is broken.

**Workaround.** Documented in sheet descriptions where the
dropdown matters (e.g. Account Network anchor picker). Per memory
`project_qs_dropdown_click_target`: suggest as the first thing to
check when an analyst reports an "unresponsive dropdown".

**Suggested fix.** Make the entire control area click-targetable.

---

### 3.2 Single-character sheet names are hidden from the rendered
tab strip

**Observed.** Naming a sheet `"i"` (1-char) makes the tab
invisible in the deployed dashboard's tab strip. The sheet still
exists and is reachable via deep link, but the navigation tab is
gone. Verified against `us-east-2`.

**Workaround.** All app-info / canary sheets renamed to a 2+ char
display name (we ship as `Info`).

**Suggested fix.** Either drop the implicit 1-char filter or
document it.

---

### 3.3 Tables virtualize ~10 DOM rows regardless of page size

**Observed.** Even with the table's page size set to a large value
(say 10000), the DOM only mounts ~10 rows at a time. Browser-side
e2e assertions that count visible rows saturate at 10. This isn't
a bug per se, but it's surprising to anyone treating the table as
"all rows in the DOM" for assertion purposes.

**Workaround.** `count_table_rows` returns DOM-visible (saturates
at ~10). For accurate post-filter counts, use
`count_table_total_rows` which scrolls + accumulates the true
total. Slower; bumps page size to 10000 and walks the inner
`.grid-container`.

**Suggested fix.** Either document the virtualization behavior or
expose a "snapshot total row count" property the client can read
without scrolling.

---

### 3.4 QS holds open WebSocket connections so `networkidle` never
fires

**Observed.** Playwright's `wait_for_load_state('networkidle')`
never fires on a deployed QS dashboard because QS holds open
WebSocket / long-polling connections continuously. Naively waiting
for networkidle burns the entire page timeout.

**Workaround.** Wait on a DOM signal instead — the
`[role="tab"]` selector attaching is the authoritative
"chrome is up" signal, in practice ~1s after embed-URL load
completes. See `wait_for_dashboard_loaded` in
`common/browser/helpers.py`.

**Suggested fix.** Document the network behavior or expose a
"dashboard ready" event the embedding SDK can wait on.

---

### 3.5 `MULTI_SELECT` dropdown: "Select all" is disabled when already
all-selected, and emptying it reverts a bridged dataset param to its
default

**Observed.** A `ParameterDropDownControl` of type `MULTI_SELECT`
sourced from `StaticValues`, bound to a parameter whose default is the
full value set:

1. The "Select all" entry in the popover renders `aria-disabled="true"`
   `aria-selected="true"` — it can't be clicked to *de*select. To get
   to "nothing selected" the analyst deselects values one at a time.
2. When the analyst does empty the selection entirely, QuickSight does
   **not** propagate an empty list to a dataset parameter the
   analysis-level parameter is bridged to (`MappedDataSetParameters`).
   The dataset parameter reverts to its declared default. So a dataset
   CustomSQL predicate like `WHERE col IN (<<$p>>)` never sees `IN ()`
   — it sees `IN (<all default values>)` instead. (Verified Y.2.c.0
   against a deployed Aurora dashboard: deselect-all → table shows all
   rows, no SQL error.)

**Why it matters.** Pushdown-via-dataset-parameter (the Y.1/Y.2
pattern) relies on (2): you can set the dataset param's default to
"every value" and trust that an emptied multi-select is a safe no-op,
not a SQL-breaking `IN ()`. You do *not* need to defensively rewrite
the predicate to handle the empty list.

**Workaround.** None needed for the SQL — declare the dataset
parameter's default as the full closed-set of values and the empty
case is handled. For e2e tests that want to exercise "deselect all",
use `set_multi_select_values(page, title, [], ...)` (deselects each
item individually) rather than `clear_dropdown` (clicks "Select all",
which is disabled here).

**Suggested fix.** Either let "Select all" toggle off when fully
selected, or surface the empty-multi-select → dataset-param behaviour
in the docs (today it's only discoverable by experiment).

---

### 3.6 After a parameter write, the prior page's table rows linger in
the DOM until the re-query lands

**Observed.** Pick a value in a `ParameterDropDownControl` (or set a
date picker) bound to a dataset parameter, and QS fires a fresh dataset
query. But the table visual keeps showing the *previous* result's
`sn-table-cell-*` rows until that query returns — only then does it
briefly clear (the spinner gap) and repopulate. So a Playwright e2e
that does "set the filter, then wait for `sn-table-cell-0-0`, then read
the rows" returns on the **stale** rows; if it instead waits a beat and
reads, it can catch the spinner gap (zero rows) → spurious "the filter
emptied the table". Worse under a load-warmed Aurora where the re-query
is slow (a warm-then-busy cluster widens the spinner gap past a 10–12s
"wait for cells" budget). (Verified X.2.q.3 against the deployed
`sasquatch_pr` L2FT dashboard — the L2FT dropdown e2e flaked exactly
this way until the workaround landed.)

**Workaround.** It turns out QS *does* expose a usable signal — just
not where you'd expect. The embedded dashboard runs every dataset
query as a JSON text frame over a single long-lived WebSocket
(`wss://<region>.quicksight.aws.amazon.com/embed/<id>/websocket/?mbtc=…`):
the client sends `{"type":"START_VIS","cid":"<uuid>","request":{…}}` to
kick off a visual's query and `{"type":"STOP_VIS","cids":[…]}` once
it's processed the response + torn down the rendering pipeline. So
`sent_START − sent_STOP` is the in-flight re-query count, and watching
that drain to zero (after at least one fresh START fired, and a ~300 ms
quiet window — the pick triggers two bursts: an immediate one and a
debounced follow-up ~2 s later) is the "data layer is done" signal.
`QsEmbedDriver._settle_after_param_change` in `tests/e2e/_drivers/qs.py`
does this via `_QsWsActivityTracker` (hooks `page.on("websocket")` +
`ws.on("framesent")` at driver construction); `pick_filter` /
`set_date_range` / `drill_from_first_row` call it so a read after a
write sees the post-filter state, not the spinner gap. No fixed sleeps.
(X.2.r — full capture + design at `docs/audits/x_2_r_event_wait_spike.md`.
Pre-X.2.r the workaround was a sleep-and-poll content-stability
heuristic; it worked but was a smell.)

Note: Playwright's WebKit channel surfaced only `framesent` frames in
the X.2.r capture, never `framereceived` — the server's data responses
came through some other channel (or WebKit just doesn't expose inbound
WS frames). The client-sent `STOP_VIS` is sufficient anyway: the client
wouldn't tear down a visual without having processed its response.

**Suggested fix.** Document the WebSocket data-layer protocol (or
expose a per-visual "query in flight / query complete" event on the
embedding SDK), so callers don't have to reverse-engineer `START_VIS` /
`STOP_VIS` frame semantics.

---

### 3.7 `ParameterDropDownControl` DOM shape diverges by option count
(simple ↔ search-enabled variants — different `sheet_control_*`
automation-ids)

**Observed.** QuickSight renders the same `ParameterDropDownControl`
with two different DOM trees depending on how many distinct values
the linked column produces. Small option-universe (Account Network's
~25 accounts) → the **simple variant**: trigger at
`[data-automation-id="sheet_control_value"]`, popover at
`[data-automation-id="sheet_control_value-menu"]`. Large option-
universe (Money Trail's ~8080 chain roots) → the **search-enabled
variant**: trigger at
`[data-automation-id="sheet_control_search_results_dropdown"]` —
**`sheet_control_value` is not in the DOM at all** — popover at
`[data-automation-id="sheet_control_menu_dropdown"]`. Both popovers
contain `[role="option"]` children.

The cardinality threshold isn't documented; QS appears to pick the
variant client-side based on the dataset's value count. The same
`ParameterDropDownControl` JSON produces either shape depending on
how many rows the `LinkedValues.from_column(...)` companion returns
at render time.

**Workaround.** `_open_control_dropdown` in
`src/quicksight_gen/common/browser/helpers.py` now dispatches on
selector presence: it counts `sheet_control_value` matches inside
the card; if zero, falls back to `sheet_control_search_results_dropdown`.
The wait-for-popover step accepts either popover-container
automation-id, so `[role="option"]` children are discovered the same
way for both variants. (Verified AA.H.6+B.1 era against
`qsgen-sp_pg_aw-investigation-dashboard` — the Money Trail
"Chain root transfer" dropdown failed for 4 consecutive chains
because the driver only knew about the simple variant; the AA.H+H.6
DOM-dump capture surfaced the divergence in one look.)

**Suggested fix.** Document the cardinality threshold (or expose a
stable variant-agnostic automation-id like
`sheet_control_trigger`), so dashboard authors don't need to write
DOM dispatchers per option-count tier.

---

## 4. Data type / shape quirks

### 4.1 `DateDimensionField` vs `CategoricalDimensionField` — column
type must match

**Observed.** A column declared as `DATETIME` in the dataset
contract MUST be wrapped in a `DateDimensionField` when used as a
chart Category. Wrapping it in a `CategoricalDimensionField` (the
default for non-date columns) produces a dashboard that
silently fails to render the visual — the field appears in the
field-well but the chart is blank.

**Workaround.** Per memory `project_qs_date_dimensions`: enforced
by the typed `Dim.date()` factory in `common/tree/datasets.py`.
The bare `Dim()` constructor for a date column raises at
construction time.

**Suggested fix.** Auto-detect the column type from the dataset
contract and pick the right `DimensionField` subtype, OR raise a
useful error at create-analysis time.

---

### 4.2 Conditional formatting expression must guard against the
zero-rows case

**Observed.** A `ConditionalFormatting` expression like
`{column} > 0` (numeric threshold) silently breaks when the table
has zero rows — no error, the table just doesn't apply the
formatting. Empirically the formatting only fires when the
expression contains a "self-equality" guard.

**Workaround.** Per memory `project_qs_conditional_formatting`:
always wrap the expression as `{col} <> "<sentinel>"` so the
expression is *always true* when the column is non-null. The
formatting then fires unconditionally, and we use the column value
itself for the actual styling decision elsewhere.

**Suggested fix.** Document the empty-table behavior or fix the
expression evaluator to treat zero rows as "format nothing"
gracefully.

---

### 4.3 `DateTimeParam.default` is required — UI errors with
"epochMilliseconds must be a number, you gave: null"

**Observed.** A `ParameterDeclaration` for a `DateTimeParameter`
that omits `DefaultValues` deploys cleanly. When the analyst opens
the dashboard, the UI throws "epochMilliseconds must be a number,
you gave: null" — visible only in the JS console — and the
DateTime picker control associated with the param fails to
hydrate.

**Workaround.** Type-encode in `tree/parameters.py`:
`DateTimeParam.default` is REQUIRED (not optional). Attempts to
construct `DateTimeParam(default=None)` raise at construction
time. See M.4.4.10d.

**Suggested fix.** Either reject `DateTimeParameter` declarations
without a default at create-analysis time, or make the picker
hydrate cleanly with a null param value (e.g. show empty until
analyst picks).

---

### 4.4 `SheetTextBox.Content` rejects `<br>` as a child of `<li>`

**Observed.** The text-box XML grammar accepts `<br/>` for line
breaks AND `<ul><li>...</li></ul>` for bullet lists. Putting one
inside the other — `<li>foo<br/>bar</li>` — is rejected by the
parser at `CreateAnalysis` time with
`Element 'li' cannot have 'br' elements as children`. The error
message names the offending text-box by `TextBoxId` and the
sheet by `SheetId`, but no other rich-text element class is
called out (e.g. `<li>` is fine inside `<ul>` and `<a>` is fine
inside `<li>`). Surfaces silently up to deploy: the JSON
serialises cleanly and the dataset describes cleanly.

**Workaround.** `common/rich_text.py::bullets()` post-processes
each item to strip `<br>`, `<br/>`, and `<br />` (case-insensitive)
and emits a `UserWarning` per offender. Triggered by L2 YAML
descriptions authored as `description: |` block scalars: the
embedded `\n` from human-readable line wrapping reflowed to
`<br/>` via `markdown()` and crashed the L1 Drift sheet's
`l1-drift-accounts` text box — see `common/rich_text.py` and
`tests/json/test_text_box_safety.py::test_no_br_inside_li_in_text_box_content`
(v8.5.8).

**Suggested fix.** Either accept `<br/>` inside `<li>` (the most
permissive web-HTML behavior matches most authors' expectations),
or surface the rejection at JSON-validation time (before the
`CreateAnalysis` round-trip) so callers learn about it without
deploying.

---

### 4.5 Chart axis `CustomLabel` silently ignored without `ApplyTo`

**Observed.** ``BarChartConfiguration.CategoryLabelOptions``,
``ValueLabelOptions``, and ``ColorLabelOptions`` (and the LineChart
equivalents ``XAxisLabelOptions`` / ``PrimaryYAxisLabelOptions``)
each carry a ``ChartAxisLabelOptions`` block whose
``AxisLabelOptions[].CustomLabel`` *should* override the axis title.
Setting only ``CustomLabel`` produces a clean ``CreateAnalysis``
round-trip — no error, the JSON describes back identical — but the
deployed chart still renders the raw column name on the axis. The
override is silently no-op.

**Workaround.** ``AxisLabelOptions`` requires an ``ApplyTo`` ref
binding the label to a specific field-well leaf
(``{FieldId, Column: {DataSetIdentifier, ColumnName}}``). Same
FieldId-binding pattern table column headers use
(``TableFieldOption.FieldId``). v8.6.1 added the
``_axis_label_apply_to(leaf)`` helper in ``common/tree/visuals.py``
and wires it into both BarChart and LineChart emit; the class
regression in ``tests/json/test_bar_chart_axis_labels.py`` asserts
no ``CustomLabel`` escapes without ``ApplyTo``.

**Suggested fix.** Either reject ``CustomLabel`` without
``ApplyTo`` at ``CreateAnalysis`` time (loud failure beats silent
no-op), or auto-bind to the first axis field when ``ApplyTo`` is
absent.

---

## 5. Backend / refresh quirks

### 5.1 Embed URL must be signed by the dashboard's region (not
the QuickSight identity region)

**Observed.** `generate_embed_url_for_registered_user` called via
a boto3 client constructed in the QS identity region (`us-east-1`)
returns a URL that, when opened, errors with "We can't open that
dashboard, another QuickSight account or it was deleted" — even
though the dashboard, account, and permissions are all correct.
The error message is misleading: it implies an account/permission
problem when the actual cause is region mismatch.

**Workaround.** Construct the boto3 client in the *dashboard's*
region (not the identity region). See
`common/browser/helpers.py::generate_dashboard_embed_url` —
takes `aws_region` keyword and constructs the client itself so
callers can't pass the wrong-region client. Burned ~1 hour
debugging this on the M.4.1.i first AWS-side dry-run.

**Suggested fix.** Either accept identity-region-signed URLs for
cross-region dashboards, or surface a "region mismatch" error
instead of the misleading "another account" message.

---

### 5.2 `boto3.client("quicksight")` overload set is so large
pyright reports "type partially unknown"

**Observed (v8.5.2).** `boto3-stubs[quicksight]` typing is
correct, but pyright's overload resolution on `boto3.client` —
which has Literal-overloads for every AWS service — reports the
return type as "partially unknown" even when the inferred type is
the right `QuickSightClient`. Two contradictory errors at the same
line.

**Workaround.** `qs: QuickSightClient = boto3.client(...)  # pyright: ignore[reportUnknownMemberType]`
— targeted suppression at the call site that lets the LHS type
annotation drive downstream inference.

**Suggested fix.** This is more of a `boto3-stubs` packaging
concern than QS itself, but flagging because every typed Python
client of the QS SDK hits this.

---

### 5.3 Materialized views don't auto-refresh

**Observed.** L1 invariant matviews + Investigation matviews
created via `CREATE MATERIALIZED VIEW` don't auto-refresh on
underlying-table changes. Every ETL load (and every `data apply`)
must explicitly call `REFRESH MATERIALIZED VIEW`. Not a QS bug —
a Postgres / Oracle behavior — but the dashboard reads stale data
silently when the refresh is missed, with no error.

**Workaround.** `quicksight-gen data refresh --execute` runs the
refresh in dependency order. CLAUDE.md "Operational Footguns"
section flags this as a footgun.

**Suggested fix.** Not a QS bug per se. Documenting here because
"the dashboard shows old data" is often initially blamed on QS.

---

## How to use this page when filing defects

For each issue you want to file with the QuickSight team:

1. Find the entry above (or add a new one if it's a new class).
2. Reproduce against a minimal hand-built analysis JSON — strip
   our generator's wrappers down to the smallest dict that
   triggers the behavior.
3. Capture the JSON, the API response (if any), and a screen
   recording of the misbehavior.
4. Cross-reference this page in the report so the QS team can see
   the workaround context — sometimes the workaround clue helps
   diagnose the root cause faster than the bare repro.
