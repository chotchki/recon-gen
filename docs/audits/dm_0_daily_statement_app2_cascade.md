# DM.0 — Daily Statement App2-only cascade + day-availability picker (design lock)

**Date:** 2026-06-15
**Phase:** DM.0
**Status:** Locked. Operator-confirmed in PLAN.md `## Phase DM` block on 2026-06-15. Ready for DM.0.5 / DM.1.

## The principle

**App2 cascades on search/filter; the DATA stays consistent with QS.**

The symmetry break here is intentional and operator-confirmed. It is the
right *kind* of asymmetry — driven by renderer capability, not by
arbitrary preference. The data the operator reads on Daily Statement is
identical across QS and App2: same `<prefix>_transactions` rows, same
`<prefix>_daily_balances` rows, same SQL projection, same DateView
semantics, same as_of clamp. What differs is *how the operator finds the
row they want to look at*. App2 cascades the picker chain (Role narrows
Account, Account decorates Day); QS keeps the flat Account + Day pair
because its cascade primitive doesn't work end-to-end.

This is the second authorized App2-over-QS divergence. The first was
Phase CN.2's cell-click-opens-menu behavior, recorded at
`[[feedback_qs_convention_origin]]` ("App2 may break the QS convention
when a better affordance exists; don't over-defend"). DM extends the
same logic to picker cascading. The QS left/right click rule, the
parameter-store-vs-control-sync gap, the cascading-dataset-parameter
silent-fail — these are QS implementation details the renderer wears as
contracts. App2 doesn't owe them honor when its server-rendered model
can do the operator-obvious thing cheaply.

The motivation is empirical: every tester who has worked the Daily
Statement sheet has, at some point, been burned by QS's
cascade-that-doesn't. The original `pL1DsRole` cascade
(`_wire_daily_statement_filters` historic note: CQ.4 dropped it because
QS's `GetUniqueAttributeValuesSyncForAnalysis` can't execute
parameterized datasets, so the Role narrow only ever worked on App2 in
the first place). And cross-sheet drills that try to populate the
picker hit `[[project_qs_url_parameter_no_control_sync]]` — the data
filters but the picker widget shows "All". App2 was already silently
doing-the-right-thing on cascade; DM is the *intentional* version of
that — the gate gets typed into the tree, the divergence gets
documented, and App2 stops pretending it can't elegantly do what QS
can't.

## What this delivers

Daily Statement gains a three-picker chain on App2: Role → Account →
Day. The Role picker (top of the chain) narrows the Account picker's
option universe via the existing BR.1 cascade-refresh endpoint. The Day
picker decorates calendar dates with CSS markers (`.has-transactions`,
`.has-balance`, `.has-both`) based on a server-supplied date-availability
map but does NOT disable empty dates — sparse accounts mean every day up
to `as_of` is a valid pick target. The Day picker stays a full
single-date Flatpickr input. QS keeps the existing two-picker (Account +
Day) shape with no Role widget, no day decoration. The DATA the two
renderers project for any given Account-Day pair is byte-identical.

## Locks (operator-confirmed 2026-06-15)

These come straight from PLAN.md `## Phase DM` and are not relitigated
here; this section restates them for the design-doc reader who isn't
holding PLAN.md open in another window.

- **Picker order**: Role → Account → Day (top-to-bottom in sidebar;
  left-to-right in horizontal layout). Reads as a natural drill-down
  progression: "I'm looking at accounts owned by Role X, specifically
  account Y, on day Z." Role is the broadest narrow, Day the finest.
- **Role → Account cascade via the existing BR.1 endpoint pattern**.
  The endpoint shape (`dropdown-options/{dataset}/{column}`) already
  handles arbitrary source picks via query params (`param_<source>`);
  Role is one more source on the same route. No new endpoint surface
  unless DM.2 finds a reason.
- **Account → Day filter — DECORATION not RESTRICTION.** Sparse
  accounts mean any day up to `as_of` stays valid as a pick target.
  Flatpickr's `onDayCreate` callback adds CSS classes based on a
  server-supplied date map. No `enable:` / `disable:` arrays — the full
  date space stays clickable. Rationale in
  "Why decoration not restriction" below.
- **QS keeps Account + Day pickers, no work** — just document the
  divergence via the structured triple
  (`docs/reference/quicksight-quirks.md` entry +
  `project_qs_no_searchfilter_cascading.md` memory file). QS doesn't
  get a Role picker; it can't cascade reliably, so adding one is worse
  than not adding one.
- **Renderer-gate primitive: strongly typed in core trees**
  (per `[[feedback_invariants_in_types]]`, not a registry side-table).
  The DM.0.5 scout result is below; spoiler: no such primitive exists
  today, so DM.0.5 builds it.
- **One commit per leaf** (bisect-friendly); release ships as v14.5.0.

## DM.0.5 scout result — no renderer-gate primitive exists today

Grep over `src/recon_gen/common/tree/` for `app2_only`, `qs_only`,
`renderer_only`, `skip_qs`, `exclude_from`, `qs_emitter`,
`renderer_gate` returns only references inside
`app2_parity_registry.py`'s `parity_break` reason strings (e.g.
`"chart_visual_drill_clicks_unsupported_app2_only_table_drill"`).
Those are documentation of existing divergences for the App2 parity
gate at `App.resolve_auto_ids()` — they explain *why* a tree field
exists on QS but App2 doesn't render it. They are not a typed gate for
the inverse case (a node that should render on App2 but be invisible to
the QS emitter walk).

The App2 parity registry's three entry shapes (`App2Consumed`,
`TreeOnly`, `ByDesign`) cover three of the four parity-disposition
cells. The missing fourth — "QS-skipped, App2-rendered" — is exactly
the gate DM needs. DM.0.5 fills the gap.

**DM.0.5 design shape (subject to refinement in implementation, but
operator-confirmed in PLAN.md):**

Add a typed `app2_only: bool = False` field to the relevant tree
primitives (`ParameterDropdown`, `ParameterDateTimePicker`, the
`Sheet.add_parameter_dropdown` / `add_parameter_datetime_picker` call
surfaces). The QS emitter walk
(`Analysis.emit_aws_json()` → `Sheet.emit_qs(…)`) checks the field on
every parameter / filter control node and skips emission for
`app2_only=True` nodes. The App2 renderer (`common/html/render.py`
→ `_VisualPlan` extraction) ignores the field — it renders every
control node it sees.

The choice between (a) one boolean on each primitive vs (b) an enum
`render_in: frozenset[Renderer] = {Renderer.QS, Renderer.APP2}` is
left to DM.0.5's implementer. The enum is more flexible (could later
gate on Studio / PDF too), the boolean is fewer LOC and less surface
area for "wait, what does an empty frozenset mean?" footguns. Default
should match today's behavior — both renderers see every control —
so the field is opt-in.

The tree-walking unit test in DM.5 asserts: for an analysis where any
control node has `app2_only=True`, the QS-side parameter-control count
is strictly less than the App2-side parameter-control count, and the
delta is exactly the count of `app2_only=True` nodes.

Tracking the gate in the tree (typed) over a side-registry follows
`[[feedback_invariants_in_types]]` — Rust-influenced operator
preference for typed wrappers that fail at the wiring site over
post-hoc output-walking tests. The DL.1 `iter_cross_sheet_drills`
walker and the DA.5 `Table.__post_init__` type-system gate are
precedent: tree-walking already pays off, adding one more walked
attribute is in-pattern.

## Why decoration not restriction

Cite `[[feedback_production_honest_invariants]]`: "L1/L2FT checks run
on real customer data; spurious demo violations get fixed seed-side,
never a demo-prefix/metadata filter in the invariant SQL." DM extends
the same operating principle into picker UX: the data is what it is;
the picker should not lie about what is reachable.

Concrete cases the decoration shape handles correctly that the
restriction shape would lie about:

- **Sparse account, partial-balance day.** Account X has a daily
  balance recorded for 2030-01-05 but no transactions on that day
  (the day's `transactions` count is 0 but `daily_balances.balance`
  is present — the balance carried forward from a prior posting).
  This is a meaningful "no activity, but the account still has
  funds" answer. Decoration: 2030-01-05 lights `.has-balance`; the
  operator can click and see the carried-forward balance. Restriction:
  the operator's calendar disables 2030-01-05 because the activity
  predicate fails, and the answer "this account is dormant but has
  funds" is unreachable via the picker.
- **Newly opened account, future-as-of dry-run.** The account-open
  event is the first transaction on 2030-01-01. The operator wants to
  confirm "did anything actually post on day-1?" — they pick 2030-01-01
  expecting to see the open event. Decoration handles this: the day
  lights `.has-both`. Restriction handles this too. But the restriction
  shape breaks on the day-before: 2029-12-31 is correctly disabled,
  but the operator now can't see "the account doesn't exist yet" as
  a clean answer — they get UI-disabled, no signal about why.
- **Account closed mid-window.** Activity stops on 2030-01-15. The
  operator wants to confirm closure happened on 01-15, not 01-14.
  Decoration: 01-15 is `.has-both` (closing entries), 01-16+ are
  plain. Restriction: 01-16+ are disabled. Both work, but decoration
  also surfaces "this account stayed closed through the rest of the
  window" as a passive visual scan; restriction hides that.
- **`as_of` clamp interaction.** The DK.10 `max_date` already clamps
  the picker upper bound to the data anchor. Restriction would
  additionally disable every day with no activity below the clamp;
  decoration leaves the visible-but-empty days as picks. Combined:
  upper-bound clamp + per-day decoration = "you cannot pick past the
  data feed, AND you can see at a glance where the activity was."

In every case, decoration preserves the operator's ability to ask
"what happened on day X, including 'nothing'?" — which is the
production-honest stance. Restriction makes "nothing" un-pickable,
which forces the operator to use a different surface (often: drop the
account filter and scan the L1 dashboard) to get back to it.

## Cascade endpoint contract (Role → Account)

The existing endpoint
(`src/recon_gen/common/html/server.py::dropdown_options`, BR.1) already
matches this contract; DM.2 adds Role as a new caller, not a new route.

**URL shape:**

```
GET /dashboards/{dashboard_id}/sheets/{sheet_id}/dropdown-options/{dataset}/{column}
    ?param_pL1DsRole=<role_value>
    [&param_<other_form_state>=<...>]
```

Path params:
- `dashboard_id` — the dashboard the sheet lives on. Today's BR.1
  callers all pass the L1 dashboard id; Daily Statement reuses the
  same.
- `sheet_id` — `daily-statement` (the existing Daily Statement sheet).
- `dataset` — the source dataset's identifier. For the Role-narrowed
  Account picker this is the unparameterized accounts dataset that
  CQ.4 introduced (`DS_L1_DS_ACCOUNTS` or a sibling — DM.2 picks the
  exact identifier; the CQ.4 unparameterized `scope = 'internal'`
  shape is the natural source).
- `column` — `account_display` (matches the AA.E.2 / CQ.1 binding
  shape that the existing Account dropdown uses).

Query params:
- `param_pL1DsRole=<role_value>` — the source-of-cascade value. The
  existing endpoint already threads `param_<name>` keys into
  `options_search_fetcher` via the URL-params multidict (see
  `server.py::dropdown_options` lines 928-938).
- Other `param_*` keys — preserved (form state); the endpoint's
  CR.2 design already passes them through so other dropdowns remain
  current-narrow.

**Response shape:**

`text/html` — `<option>` fragments only (no surrounding `<select>`),
swapped by HTMX into the live Account `<select>`. Identical shape to
today's BR.1 response.

```html
<option value=""></option>
<option value="Account A Display (123-456)">Account A Display (123-456)</option>
<option value="Account B Display (789-012)" selected>Account B Display (789-012)</option>
...
```

The blank leading option exists for HTMX-on-empty semantics. The
`selected` attribute survives when the operator's prior pick is still
in the narrowed universe (CR.2's preservation rule). If the operator
had Account B picked and Role X narrows the universe to {Account A,
Account B}, the swap keeps Account B selected. If Role Y narrows to
{Account C, Account D}, the swap clears the selection.

**SQL projection:**

```sql
SELECT DISTINCT account_display
FROM <prefix>_l1_ds_accounts_view
WHERE :param_pL1DsRole IS NULL
   OR account_role = :param_pL1DsRole
ORDER BY account_display
LIMIT 2000;
```

The `IS NULL` arm handles the seed page (no Role pick yet — show all
internal accounts). The DuckDB / Postgres / Oracle dialect branches
follow `common/sql/dialect.py`; for sentinel-default Role values
(e.g. `__no_role__`) the WHERE clause uses the L1 sentinel pattern
(see `apps/l1_dashboard/datasets.py::_data_value_clause`).

**No new endpoint** — DM.2 adds a third row to the existing
`dropdown-options` route's caller table, alongside today's Role-narrows-Account
cascades on other sheets. The CR.2 truncated-flag is structurally False
on this caller (query=""), matching the existing HTML-cascade convention.

## Day-availability endpoint contract

The Day picker needs a per-account date-availability map keyed on the
visible calendar window. Three integration shapes were considered; the
operator-preferred shape is **(2) extend `dropdown-options` with a new
dataset/column pair** because it reuses the existing form-state
threading and stays inside the established route convention.

**URL shape (option 2 — preferred):**

```
GET /dashboards/{dashboard_id}/sheets/{sheet_id}/dropdown-options/{dataset}/{column}
    ?param_pL1DsAccount=<account_display>
    &window_start=<YYYY-MM-DD>
    &window_end=<YYYY-MM-DD>
```

The `{dataset}/{column}` pair is a synthetic identifier — `l1_ds_day_availability`
/ `business_day_iso` — that the server-side resolver in
`server.py::options_search_fetcher` (or a sibling map indexed by the
synthetic name) recognizes and dispatches to a custom projection
function instead of the dataset-walk. Identical caller pattern to BR.1;
the indirection lives in the resolver.

Alternatively (option 1 — a new sibling route
`/dashboards/{id}/sheets/{id}/day-availability/{dataset}`):

```
GET /dashboards/{dashboard_id}/sheets/{sheet_id}/day-availability
    ?param_pL1DsAccount=<account_display>
    &window_start=<YYYY-MM-DD>
    &window_end=<YYYY-MM-DD>
```

DM.3 picks option 1 if extending the synthetic-dataset resolver feels
hostile to readers; option 2 if the existing `options_search_fetcher`
plumbing absorbs the call cleanly. Both shapes return the same
response.

**Response shape:**

```json
{
  "dates": {
    "2030-01-01": ["transactions", "balance"],
    "2030-01-02": ["balance"],
    "2030-01-05": ["transactions", "balance"],
    "2030-01-06": ["transactions"]
  }
}
```

Days not present in the response map render plain (no decoration class).
A date with `["transactions", "balance"]` gets BOTH `.has-transactions`
and `.has-balance` classes (and optionally a `.has-both` shorthand for
CSS specificity convenience). Empty arrays don't appear — a date is
either in the map with at least one tag, or absent.

**SQL projection:**

Single roundtrip via the existing pool (no per-account-day-per-month
fan-out). Union of two `SELECT DISTINCT` projections, filtered by the
window:

```sql
SELECT DISTINCT
  CAST(business_day_start AS DATE) AS business_day,
  'transactions' AS source
FROM <prefix>_current_transactions
WHERE account_display = :param_pL1DsAccount
  AND business_day_start >= :window_start
  AND business_day_start <= :window_end

UNION ALL

SELECT DISTINCT
  CAST(business_day_start AS DATE) AS business_day,
  'balance' AS source
FROM <prefix>_current_daily_balances
WHERE account_display = :param_pL1DsAccount
  AND business_day_start >= :window_start
  AND business_day_start <= :window_end;
```

The server collapses the two-column row set into the per-date tag-list
map. Single roundtrip; the unioned subqueries are both
account-display-equality + day-range — the existing
`(account_id, business_day_start)` indexes
(`<prefix>_current_daily_balances` per `common/l2/schema.py:623`)
serve both arms cheaply. Where `account_display` is a derived column
not directly indexed, the resolver translates to `account_id` via the
accounts view (one extra hop, still single roundtrip via CTE).

**Window-start / window-end derivation:**

The Flatpickr calendar widget renders one month at a time by default;
the operator can flip to adjacent months. DM.3's JS wires
`onMonthChange` to re-fetch the day-availability map for the new
window (plus a generous overscan — fetch 60 days before and after the
displayed month to absorb the next flip without a re-fetch). The
debounce on `onDayCreate` calls into a JS-side cache keyed on
`(account, window_start, window_end)` so a tight back-and-forth
between months doesn't hammer the endpoint.

If `param_pL1DsAccount` is empty (sentinel default), the endpoint
returns an empty `dates` map immediately and the picker renders
undecorated — no SQL fired. This matches the "no account selected,
no data to look at" state of the existing summary visual.

## Empty-state UX

When the operator has picked an Account but the
day-availability endpoint returns an empty `dates` map for the visible
window — meaning the account has no transactions or daily-balance
rows in that range — DM.3 surfaces an inline passive hint near the
calendar:

> No activity for **<account display>** in the visible window
> (<window_start> to <window_end>).

NOT an alert banner. NOT a modal. NOT a destructive replacement of the
calendar. The calendar still renders (all days clickable per the
decoration-not-restriction lock), the picker controls still work, the
hint is one line of muted text adjacent to the picker. The operator
can still pick a day (and get the "no activity" Daily Statement view,
which is a meaningful answer). The operator can also widen the
month-navigation to find a window where activity exists.

Implementation: the JS-side cache check sees the empty map and toggles
a hidden `<p>` element under the date input. CSS class:
`.day-picker-empty-window` — keep it visually quiet (muted text color,
no icon, no border). Cite `[[feedback_browser_drivers_user_facing_locators]]`
for the visible-text locator: a stable `role="status"` /
`aria-live="polite"` annotation lets the e2e test in DM.5 locate the
hint via the user-facing string, not the CSS class.

## QS divergence — the structured triple

QS doesn't get any of DM.1-DM.3. The Daily Statement sheet stays
Account + Day on QS. POLICY 2 (CLAUDE.md "Build hygiene contract")
requires a structured triple for any permanent renderer-capability
gap; DM.4 produces it.

**Triple part 1 — the renderer-gate primitive's QS emitter branch.**
DM.0.5's `app2_only=True` field, when set on the Role
`ParameterDropdown`, causes the QS emitter to skip the node. There is
no `NotImplementedError` on a driver verb here — the gap isn't a verb
call, it's an entire control's emit-path being skipped. The emitter
branch carries a comment: `# DM.0.5 — app2_only gate. Role cascade is
unsupported in QS (cascading dataset parameter silent-fail + URL-param
no-control-sync); see project_qs_no_searchfilter_cascading.md`.

**Triple part 2 — `docs/reference/quicksight-quirks.md` entry.**
Append an entry titled "Search / filter cascading via dataset
parameters silently fails on initial load" with the symptom
(picker control widget shows "All" despite the parameter store carrying
the cascade value), the verb that triggers it (any
`MappedDataSetParameters` write driven by a sibling-control change on
the initial render path), the workarounds tried + rejected (the CQ.4
historic context — `GetUniqueAttributeValuesSyncForAnalysis` doesn't
execute parameterized datasets), and the operator-confirmed
resolution (App2-only the cascade, QS keeps the flat picker pair).
Cross-link to `[[project_qs_url_parameter_no_control_sync]]` (the
parent quirk — DM is a downstream consequence).

**Triple part 3 — memory file
`project_qs_no_searchfilter_cascading.md`.** Operator-confirmed
reason the gap is permanent: the cascade primitive
(`CascadingControlConfiguration`) requires `MappedDataSetParameters`
to fire on the source-control change, but QS's
`GetUniqueAttributeValuesSyncForAnalysis` rejects parameterized
datasets (the actual API call that drives the picker-options refresh
runs a non-parameterized DISTINCT over the source dataset; the param
isn't bound). The narrower QS bug (`[[project_qs_url_parameter_no_control_sync]]`)
addresses the cross-sheet drill case; this entry addresses the
same-sheet cascade case. Workarounds tried: bake the role-narrowed
account universe into the deploy-time dataset (rejected — operator
lock 2026-06-08 "ALL internal accounts should be searchable");
client-side JS in the embed wrapper (rejected — violates the
no-redeploy invariant). Resolution: the App2 renderer's server-side
endpoint cascades correctly because it runs the SQL projection
per-request; this is a real capability divergence, not a feature gap,
and Phase DM acknowledges it explicitly.

The triple satisfies POLICY 2: the failure is traceable (emitter
branch + quirks entry + memory file), reviewable (operator can audit
the rationale in one read), and aging-friendly (if AWS ships a fix
to `GetUniqueAttributeValuesSyncForAnalysis`, all three artifacts
retire together).

## Cite precedent for the principle

The principle ("App2 cascades on search/filter; the DATA stays
consistent with QS") is not unprecedented in this codebase. Two prior
authorized App2-over-QS divergences set the pattern:

1. **CN.2 cell-click-opens-menu** — App2 tables open a row-context
   menu on cell-click; QS uses the left-click=drill / right-click=menu
   QS convention. Captured at `[[feedback_qs_convention_origin]]`
   ("the QS convention is itself a workaround — App2 may break it when
   a better affordance exists").
2. **CQ.2 server-side typeahead** — App2's `Tom Select`-driven
   typeahead handles 500-element option universes; QS's native picker
   tops out around 100. The data the dropdowns project is identical;
   the UX of finding the row is renderer-specific.

DM joins those two as the third authorized App2-over-QS divergence:
**App2 cascades on search/filter; QS doesn't.** The pattern is
consistent: the renderer that can do the operator-obvious thing
cheaply (App2's per-request server query) does it; the renderer that
can't (QS's deploy-time dataset-parameter wiring) doesn't pretend
to. The data symmetry is preserved because the underlying SQL is the
same; only the picker chrome differs.

## DM.1 — Role picker shape on Daily Statement

DM.1 adds the Role picker as the new top-of-chain control on Daily
Statement (App2 only via DM.0.5's renderer gate). The picker's data
contract:

**Source dataset.** A new (or revived) unparameterized DISTINCT
view of account roles. The CQ.4-dropped `pL1DsRole` parameter shape
worked off
`<prefix>_l1_ds_accounts` — that dataset (or a `_ds_l1_ds_roles`
sibling) becomes the Role picker's source. The view's projection:

```sql
SELECT DISTINCT account_role
FROM <prefix>_current_accounts
WHERE scope = 'internal'
  AND account_role IS NOT NULL
ORDER BY account_role;
```

Role values are short labels (e.g. `concentration`, `funds_pool`,
`dda_operations`) — the L2 yaml's `persona:` block defines them. The
universe is bounded (typical: 6-15 roles per L2 instance). The
`<<$pL1DsRole>>` dataset parameter is NOT needed here — the picker
sources from a top-level un-narrowed query. (The Account picker
downstream of Role is the one that *consumes* the Role value, via
the BR.1 cascade endpoint's URL-param threading; the Role picker
itself stays un-cascaded.)

**Analysis-level parameter.** A new `P_L1_DS_ROLE` StringParam
with `default=["__no_role__"]`, `value_when_unset="__no_role__"`,
no `mapped_dataset_params` (the Role narrow happens in the
App2-side endpoint, not via QS's MappedDataSetParameters bridge —
which DM.0.5's gate skips emitting anyway). The default sentinel
matches the dataset's `WHERE :param_pL1DsRole IS NULL OR
account_role = :param_pL1DsRole` shape: the empty / sentinel value
means "match all roles" (cascade endpoint returns all internal
accounts).

**Renderer-gate field on the dropdown.** The
`ParameterDropdown(parameter=ds_role, ..., app2_only=True)` flag —
DM.0.5's new primitive field — instructs the QS emitter walk to
skip the control. App2's `render.py` renders it normally.

**Cascade wiring on the downstream Account dropdown.** The existing
Account dropdown (already in place from CQ.4) gains a
`cascade_source=role_dropdown,
cascade_match_column=accounts_view.account_role` wiring. The
`cascade_source` field on `ParameterDropdown` already exists
(`common/tree/controls.py:200`); App2's BR.1 endpoint already reads
the sibling-control state from the URL multidict. The QS side of
`cascade_source` emits a `CascadingControlConfiguration` block —
DM.0.5's gate has a choice here: either skip the CascadingControl
emission entirely (since the source control isn't being emitted at
all), or emit the block but have it silently do nothing on QS
(which is the CQ.4 historic state — QS's
`GetUniqueAttributeValuesSyncForAnalysis` can't execute parameterized
datasets). DM.0.5 implementer picks based on QS emitter cleanliness;
the operator-visible behavior is identical either way.

**Why a new `app2_only` field on the primitive, not on the cascade
wiring.** The renderer-gate primitive lives on the *control* (the
Role `ParameterDropdown`), not on the cascade edge (the
`cascade_source` field on the Account dropdown). This is intentional:
the cascade edge is a directional relationship between two controls;
gating the edge would mean "QS skip the cascade but render both
controls," which produces a Role picker that does nothing on QS
(operator confusion). Gating the control means "QS skip the control
entirely," which produces the operator-confirmed flat Account+Day
shape. The cascade edge follows the control's gating implicitly:
when the source control is skipped, the cascade has nothing to
cascade from.

## DM.2 — Account picker cascade reuse

The Account dropdown already exists (CQ.4 / AA.E.2 / CQ.1 binding).
DM.2 wires the `cascade_source` field and confirms the BR.1
endpoint accepts the new `param_pL1DsRole` URL param. Code touch
points:

- `apps/l1_dashboard/app.py::_wire_daily_statement_filters` —
  add the Role picker construction, set `cascade_source` on the
  existing Account dropdown.
- `common/html/server.py::dropdown_options` — no code change
  required; the URL-params multidict-threading already accepts
  arbitrary `param_*` keys. The behavior is data-driven by the
  tree wiring.
- `common/html/render.py::_resolve_linked_options` — confirms the
  initial-page-render cascade fires when a `?param_pL1DsRole=X`
  comes in via the page URL (drill-into-Daily-Statement scenarios).
- `apps/l1_dashboard/datasets.py` — the
  `DS_L1_DS_ACCOUNTS` dataset (the un-parameterized internal
  accounts view) gains a `account_role` column projection so the
  cascade match-column resolves. If the column isn't already in the
  SELECT list (verify in DM.2; CQ.4 may have dropped it when the
  Role narrow was removed), restore it.

The DM.2 unit test asserts: rendering a Daily Statement page with
`?param_pL1DsRole=concentration` produces an Account dropdown whose
`<option>` list is exactly the concentration-role accounts. A second
unit test fires the cascade endpoint directly with the same query
string and asserts the response fragment matches the page-render's
options.

## DM.3 — Day decoration wiring

The Day picker decoration extends the existing
`wireFlatpickrSingle` (`common/html/assets/js/bootstrap.js:2636`).
DK.10 already wired `maxDate` reading via `data-max-date`; DM.3
adds an `onDayCreate` handler that reads from a JS-side
day-availability cache.

**HTML surface** (rendered by `render.py::_render_parameter_date`):

```html
<input type="text"
       data-flatpickr-single
       data-target-input="param_pL1DsBalanceDate"
       data-max-date="2030-01-15"
       data-day-availability-url="/dashboards/l1/sheets/daily-statement/dropdown-options/l1_ds_day_availability/business_day_iso"
       placeholder="Latest day" />
```

The `data-day-availability-url` attribute is the new addition.
Empty / absent → the Flatpickr instance skips the decoration path
(legacy behavior; QS-side never renders this attribute via
DM.0.5's gate; App2-side renders it when the parent
`ParameterDateSpec` carries a day-availability source).

**Flatpickr config additions** in `wireFlatpickrSingle`:

```javascript
var dayAvailabilityUrl = el.dataset.dayAvailabilityUrl || null;
var dayAvailabilityCache = {};

function loadDayAvailability(windowStart, windowEnd, accountValue) {
  var cacheKey = accountValue + "|" + windowStart + "|" + windowEnd;
  if (cacheKey in dayAvailabilityCache) {
    return Promise.resolve(dayAvailabilityCache[cacheKey]);
  }
  var qs = new URLSearchParams();
  qs.set("param_pL1DsAccount", accountValue);
  qs.set("window_start", windowStart);
  qs.set("window_end", windowEnd);
  return fetch(dayAvailabilityUrl + "?" + qs.toString())
    .then(r => r.json())
    .then(body => {
      dayAvailabilityCache[cacheKey] = body.dates;
      return body.dates;
    });
}

function applyDayClasses(dayElem, dateStr, datesMap) {
  var tags = datesMap[dateStr] || [];
  if (tags.includes("transactions") && tags.includes("balance")) {
    dayElem.classList.add("has-both");
  }
  if (tags.includes("transactions")) {
    dayElem.classList.add("has-transactions");
  }
  if (tags.includes("balance")) {
    dayElem.classList.add("has-balance");
  }
}

flatpickr(el, {
  mode: "single",
  dateFormat: "Y-m-d",
  defaultDate: hidden && hidden.value ? hidden.value : null,
  maxDate: maxDate,
  onDayCreate: function(dObj, dStr, fp, dayElem) {
    if (!dayAvailabilityUrl) return;
    var accountInput = scope.querySelector('input[name="param_pL1DsAccount"]');
    var accountValue = accountInput ? accountInput.value : "";
    if (!accountValue) return;
    var dateStr = fp.formatDate(dayElem.dateObj, "Y-m-d");
    var windowStart = fp.formatDate(fp.days.firstChild.dateObj, "Y-m-d");
    var windowEnd = fp.formatDate(fp.days.lastChild.dateObj, "Y-m-d");
    loadDayAvailability(windowStart, windowEnd, accountValue)
      .then(datesMap => applyDayClasses(dayElem, dateStr, datesMap));
  },
  onMonthChange: function(_, __, fp) {
    // Re-fetch the new window's availability map. The cache absorbs
    // repeat month-flips; the operator's tight back-and-forth between
    // two months never re-hits the endpoint.
    fp.redraw();
  },
  onChange: function(selectedDates, _dateStr, instance) {
    var d = selectedDates[0]
      ? instance.formatDate(selectedDates[0], "Y-m-d")
      : "";
    if (hidden) {
      hidden.value = d;
      hidden.dispatchEvent(new Event("change", { bubbles: true }));
    }
  },
});
```

The `onDayCreate` callback fires per visible day-cell. The promise
chain is fire-and-forget — when the response lands, the CSS class
gets added. The empty-state hint render also lives on the
promise-then: if `Object.keys(datesMap).length === 0`, toggle the
`.day-picker-empty-window` element visible.

**CSS markers** (`common/html/assets/css/widgets-theme.css` or
sibling — DM.3 picks):

```css
.flatpickr-day.has-transactions::after {
  content: "";
  position: absolute;
  bottom: 4px;
  left: 50%;
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--accent-tx);
  transform: translateX(-50%);
}

.flatpickr-day.has-balance::before {
  content: "";
  position: absolute;
  top: 4px;
  right: 4px;
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--accent-balance);
}

.flatpickr-day.has-both {
  /* the .has-transactions and .has-balance dots both render; the .has-both class
     exists for CSS specificity convenience in operator-facing test assertions */
}
```

The dot positions (bottom-center for transactions, top-right for
balance) are visually distinct without overpowering the Flatpickr
selected-day chrome. The `::before` / `::after` pseudo-elements ride
on top of the cell's color box; Flatpickr's own `.selected` rule
still owns the cell background. The `--accent-tx` / `--accent-balance`
CSS variables resolve from the L2-instance theme (see
`build_theme`).

## Test impact analysis

### Unit tier (`tests/unit/`)

- **Cascade SQL emit shape** — given a DM.1 Role picker wired with
  `cascade_source=role_dropdown, cascade_match_column=accounts_view.account_role`,
  the `_resolve_linked_options` walker produces the cascade SQL with
  the `param_pL1DsRole` substitution. Asserts SQL shape, not text
  byte-identity (per `[[feedback_sql_dialect_convergence_preferred]]`
  the test is cross-dialect parametrize).
- **Day-availability SQL projection** — given a DM.3 endpoint call
  with `param_pL1DsAccount=<x>` + window bounds, the projection
  function produces the UNION-ALL SQL with the right two tables and
  the right WHERE clauses. Asserts SQL shape, asserts the result
  collapse function turns `[(date, 'transactions'), (date, 'balance')]`
  pairs into the expected map.
- **Empty-state hint surfaces** — given a `dates: {}` response, the
  fragment renderer produces the `role="status"` hint with the
  account-display + window strings. No CSS class assertion; visible
  text only.
- **DM.0.5 renderer-gate enforcement** — given a `Sheet` with one
  control flagged `app2_only=True`, `Analysis.emit_aws_json()` emits
  N-1 controls; the App2 render still emits N. Tree walk asserts the
  delta is the exact count of `app2_only=True` nodes.

### App2 e2e cell (`tests/e2e/app2/`)

- **Cascade narrows** — open Daily Statement, pick Role X, assert the
  Account dropdown's option universe is exactly the set
  Role-X-owns. Read via `driver.filter_options("Account")`.
- **Calendar decoration** — pick Account Y (sparse account with known
  seed-planted activity on three days in 2030-01), open the Day
  picker, walk the displayed calendar via the new
  `driver.calendar_day_class("YYYY-MM-DD")` verb (DM.5 may need to
  build it; per `[[feedback_build_verbs_not_skip]]` build it on App2,
  raise `NotImplementedError` on QS with a comment naming the
  decoration-doesn't-exist-on-QS gap and a structured-triple
  cross-reference).
- **Empty-state hint** — pick Account Z (no activity in 2030 — seed
  the case explicitly), assert
  `driver.text_visible("No activity for Z")` returns True. NOT
  `driver.locator(".day-picker-empty-window")` — visible text per
  `[[feedback_browser_drivers_user_facing_locators]]`.
- **Day picks are still valid in empty windows** — same fixture as
  the empty-state test, pick any day in the window, assert the
  summary visual renders (zero rows, but not a 500 — the
  no-activity-but-valid-pick case must work end-to-end).

### QS e2e cell (`tests/e2e/qs_browser/`)

- **Role picker absent on QS** — open Daily Statement on QS, assert
  `"Role" not in driver.filter_labels()`. One assertion; QS branch
  is a no-op for DM.1-DM.3 mechanics. This is the structured-triple's
  observable surface in the test suite.
- **Account + Day still work** — existing tests, no change.

### Renderer-gate tree-walk gate (DM.0.5 unit test)

For every existing app's `Analysis`, walk the tree and assert that
the App2-side parameter-control count minus the QS-side count equals
the count of `app2_only=True` nodes on the tree. Post-DM.1 this is
exactly 1 on the L1 dashboard (the Role picker) and 0 on the other
three apps.

## Risks + open questions

1. **Cascade endpoint scope leak.** The BR.1 endpoint was authored
   for the CQ.4-era app2 cascade. DM.2 introduces a Role-source
   cascade that other sheets in the L1 dashboard might want to read
   as a precedent. The risk: a future author wires Role cascading
   on Drift / Overdraft, the endpoint accepts the URL shape, and
   the same datasets get hit on every dropdown swap. Mitigation:
   DM.2's call site documents that the cascade source set is curated
   per-sheet in the tree (`cascade_source` field on
   `ParameterDropdown`) — the endpoint trusts the tree's wiring, and
   the tree's wiring is the gate. No endpoint-level scope check.
2. **Flatpickr `.has-transactions` CSS class collision.** Flatpickr
   uses its own day-cell classes (`.flatpickr-day.today`,
   `.flatpickr-day.selected`, `.flatpickr-day.disabled`, etc.). The
   DM.3 decoration classes prefix with `has-` and don't collide by
   name, but the CSS specificity battle (Flatpickr's
   `.flatpickr-day.selected` overrides the default cell color)
   means the decoration markers need higher-specificity rules to
   show through. Mitigation: DM.3's CSS uses pseudo-element dots
   (`.has-transactions::after`) rather than background-color
   replacement, so the Flatpickr selected-day rule still owns the
   cell's primary visual and the dot rides on top. Visual mockup
   needed before DM.3 commits CSS.
3. **Running balance (Phase DN, parallel design).** Operator flagged
   2026-06-16 that adding a running-balance column to Daily Statement
   is a separate concern from DM. DN is the row-shape work
   (window aggregation over `signed_amount` per account-day); DM is
   the picker UX work. DM.3 should not preclude DN — specifically,
   the day-availability endpoint's response shape doesn't need to
   carry balance values, only the boolean "balance exists for this
   day" tag. DN will project the running balance into the summary
   visual; the Day picker stays decoration-only. No coupling.
4. **App2 server cache eviction on `as_of` change.** The DK-tier
   `as_of` value can change between requests (live mode vs locked
   mode). If the day-availability cache is keyed on
   `(account, window_start, window_end)` alone, an `as_of` flip
   could serve stale "this day has activity" tags from a prior
   anchor. Mitigation: include the `as_of` in the cache key
   (`(account, window_start, window_end, as_of_iso)`). DM.3
   resolves this when wiring the cache.
5. **`account_display` indexed?** The cascade SQL and day-availability
   SQL both filter on `account_display`, which is a derived column
   (CQ.1: `COALESCE(account_name, account_id) || ' (' || account_id || ')'`).
   It is NOT in the existing
   `(account_id, business_day_start)` index. The accounts-view side
   handles this cheaply (small table, no index needed), but the
   day-availability projection over
   `<prefix>_current_transactions` / `<prefix>_current_daily_balances`
   needs to resolve `account_display` → `account_id` via a CTE or
   subquery so the indexed path fires. DM.3 confirms the projection
   shape against a seed-realistic profile.
6. **Cache invalidation on cross-tab data refresh.** If the operator
   has Daily Statement open in two tabs and seed-applies in another
   shell, the day-availability cache in tab A serves the pre-apply
   map until the window navigation triggers a re-fetch. Acceptable
   for the demo/iteration use case; document as a known limitation
   if Studio's auto-refresh pattern ends up touching the Day picker.

## Cross-references

- **Implementation tasks**: PLAN.md `## Phase DM` block, leaves DM.0
  through DM.6 (committed 2026-06-15).
- **Sibling phase**: PLAN.md `## Phase DN` — running-balance column on
  Daily Statement; both renderers; row-shape work parallel to DM's
  picker-UX work. DN does not change DM's day-availability response
  shape.
- **Renderer-gate precedent**:
  `src/recon_gen/common/tree/app2_parity_registry.py` — the
  `App2Consumed` / `TreeOnly` / `ByDesign` registry covers the
  inverse case (QS-rendered, App2-skipped). DM.0.5 adds the missing
  fourth cell (QS-skipped, App2-rendered) as a typed field on the
  primitive, not a registry entry.
- **Cascade endpoint origin**:
  `src/recon_gen/common/html/server.py::dropdown_options` (BR.1) —
  the route DM.2 reuses. CQ.2 added the JSON typeahead sibling
  (`dropdown_search`); DM doesn't touch that route, though if the
  Account picker on Daily Statement has a typeahead surface (it
  does — `Tom Select`-driven), the same Role narrow needs to thread
  through `dropdown_search` too. DM.2 confirms.
- **Day picker max-date clamp**: DK.10 (`ParameterDateSpec.max_date`
  + `wireFlatpickrSingle`'s `data-max-date` read). DM.3 extends the
  same Flatpickr instance with the `onDayCreate` callback; no
  conflict with DK.10's `maxDate` (Flatpickr supports both
  simultaneously).
- **Operator preference for typed primitives**:
  `[[feedback_invariants_in_types]]` — Rust-influenced preference for
  typed constructors that fail at the wiring site over post-hoc
  output-walking tests. DM.0.5's
  `app2_only` field on the primitive (over a side-registry) is in
  pattern.
- **Production-honest UX**:
  `[[feedback_production_honest_invariants]]` — applied here to
  picker UX (decoration-not-restriction over hiding-empty-days). The
  data is what it is; the picker should not lie about what is
  reachable.
- **QS convention origin**: `[[feedback_qs_convention_origin]]` —
  App2 may break the QS convention when a better affordance exists;
  the second authorized App2-over-QS divergence (after CN.2's
  cell-click-opens-menu).
- **QS URL-parameter no-control-sync** (the parent quirk):
  `[[project_qs_url_parameter_no_control_sync]]` — the cross-sheet
  drill version of the cascade-doesn't-work pattern. DM.4's
  new memory file `project_qs_no_searchfilter_cascading.md`
  documents the same-sheet variant.
- **CQ.4 historic context**:
  `apps/l1_dashboard/app.py::_wire_daily_statement_filters` — the
  `pL1DsRole` cascade that CQ.4 dropped because QS couldn't honor
  it. DM resurrects the cascade as App2-only via DM.0.5's
  renderer gate.
- **Day-picker e2e locator convention**:
  `[[feedback_browser_drivers_user_facing_locators]]` — the
  empty-state hint and the calendar decoration markers get
  user-facing locator surfaces (`role="status"`,
  `aria-label="day has transactions"`) so the DM.5 e2e doesn't
  bind to Tailwind utility classes.
- **POLICY 1 / POLICY 2** (CLAUDE.md "Build hygiene contract") —
  the DM.4 structured triple (quirks.md entry + memory file +
  emitter-branch comment) is POLICY 2's permanent-capability-gap
  carve-out. The cascade gap is operator-confirmed permanent
  (CQ.4 already established it pre-DM); the triple makes the
  divergence reviewable and aging-friendly.
- **DL.0 sibling design lock**:
  `docs/audits/dl_0_drill_guardrail_design.md` — the same
  POLICY 2 / structured-triple discipline applies. DL guards
  cross-sheet drills via tree-walked enumeration; DM guards
  picker UX via the typed renderer-gate primitive. Both extend the
  "browser drivers catch UX bugs the operator would otherwise
  miss" pattern.
