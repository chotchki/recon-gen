# AA.A.6 picker enumeration spike

**Date:** 2026-05-16
**L2 instance:** `tests/l2/spec_example.yaml`
**Question:** Can a generic additive-pickers row-survival test be written
without a per-picker filter-column map (the "off-table column" challenge)?

## Method

Tree-walked all 4 apps (L1 / L2FT / Investigation / Executives) via the
spike script at `/tmp/picker_walk.py` (paraphrased: instantiate each
`build_*_app(cfg, l2_instance=l2)`, call `emit_analysis()` to resolve
auto-IDs, iterate `app.analysis.sheets[].parameter_controls`).

## Enumeration totals

- **18 sheets total** across 4 apps (4 + 6 + 6 + 4)
- **11 sheets with pickers** (excluding `Getting Started` + `Info`,
  plus `L2 Exceptions` which has no pickers)
- **48 pickers** total

### Picker kind breakdown

| Kind                          | Count | Has DOM options? | Notes                                  |
| ----------------------------- | ----- | ---------------- | -------------------------------------- |
| `ParameterDropdown` (static)  | 18    | yes (≤7 each)    | enum dropdowns: status, role, type     |
| `ParameterDropdown` (linked)  | 8     | yes (DB-fed)     | account_display, transfer_id, etc.     |
| `ParameterSlider`             | 5     | no (range only)  | range + step on the spec               |
| `ParameterDateTimePicker`     | 14    | n/a (date input) | universal date-range, paired f/t       |
| `ParameterTextField`          | 3     | n/a (free text)  | L2FT metadata value                    |

### Sheet-level picker counts (sheets with ≥1 picker)

- **L1 Dashboard** (10 of 11): Drift (4), Drift Timelines (3),
  Overdraft (4), Limit Breach (4), Pending Aging (3), Unbundled Aging (3),
  Supersession Audit (1), Today's Exceptions (5), Daily Statement (3),
  Transactions (7).
- **L2 Flow Tracing** (3 of 6): Rails (7), Chains (6), Transfer
  Templates (6).
- **Investigation** (4 of 6): Recipient Fanout (1), Volume Anomalies (1),
  Money Trail (3), Account Network (2).
- **Executives** (3 of 4): Account Coverage / Volume / Money Moved (each:
  just Date From + Date To — 2 pickers, no content dropdowns).

## Decision: path (2) is viable

The original AA.A.6 entry flagged three resolution paths for the
"off-table column" challenge — pickers that bind columns NOT in the
displayed table (e.g., Daily Statement's Role filters by `account_role`
but Posted Money Records' table doesn't show that column).

After the enumeration, **path (2) — read picker's advertised options,
pick first, check row count** — handles the generic case cleanly:

- For each picker, ask the driver `driver.filter_options(label)`. Static
  dropdowns return their literal options. LinkedValues dropdowns return
  the live DB-fed options (already a thing — see `_resolve_linked_options`
  in `common/html/server.py`). Sliders / date pickers / text fields don't
  participate in this loop (no "first option" — they're range / scalar).
- For each picker, pick the first option. Assert `0 < table_rows ≤
  before`.
- Additively: pick each picker's first option in sequence (don't clear).
  Assert combined ≥ 1.

This doesn't NEED the picker→filter-column map. The "off-table column"
challenge only matters if we want to read the anchor row's value FROM
the table — which path (1) needed. Path (2) sidesteps that by reading
the picker's *options* (which always exist regardless of where the
filter column lives).

**What we lose by not having the map:** AA.A.7's "toggle to a deliberately
non-matching value and assert the anchor row disappears" still works
for dropdowns (pick a different option) and sliders (slide outside
the matching range), but we can't *prove* a single specific row
disappeared — we only prove the row count dropped. That's usually
sufficient signal.

**What we still need a map for:** sliders + text fields don't have
"options" to iterate. For sliders, the spec carries `minimum_value` /
`maximum_value` / `step_size` — pick `minimum_value` as the "low
permissive" anchor, `maximum_value` as the "high restrictive" exclusion.
For text fields, the only sensible anchor is empty (no filter) and
exclusion is some sentinel ("__no_match__"). Both kinds are handled
without needing to know which column they bind to.

## Out-of-scope for AA.A.6

- **Date pickers** are universal-date-range, already covered by
  `test_l1_filters.py::test_universal_date_filter_narrows_table`. AA.A.6
  doesn't need to re-test them.
- **L2 Exceptions / Info / Getting Started** sheets have no pickers —
  skip them; the test param-list parametrize will filter automatically.
- **L2FT Metadata Value text field** could break the additive test if a
  random first dropdown option for `pL2ftMetaKey` doesn't have any
  rows when we then put a value in. Solution: skip free-text picker
  in the additive loop, exercise it separately if at all.

## Implementation order

**Hard rule (user-confirmed 2026-05-16):** the parametrize list MUST be
derived from the tree at collection time — never hardcoded. A
hand-listed `[("L1 Dashboard", "Daily Statement"), ...]` would rot the
moment a sheet is added / renamed; the whole point of the generic test
is to catch the new-sheet case without a test author remembering to add
it. Pattern matches existing tree-walker tests (`TreeValidator`,
`tests/e2e/_kitchen_app.py`).

1. **AA.A.6.a** — helper `enumerate_picker_sheets() → list[SheetWithPickers]`
   in a new `tests/e2e/_picker_walk.py` module. Builds all 4 apps,
   walks `app.analysis.sheets[].parameter_controls`, returns one
   entry per (app_name, sheet_name) where ≥1 picker is "exercisable"
   (dropdown w/ options or slider — date pickers + text fields skipped).
   Each entry carries the list of `PickerSpec` (label / kind / how to
   pick-first / how to pick-excluded).
2. **AA.A.6.b** — parametrized test in `tests/e2e/test_picker_survival.py`
   over `[qs, app2]` × `enumerate_picker_sheets()` — pytest collects
   the matrix at startup, so adding a sheet automatically extends the
   test set. Body: enumerate the sheet's pickers, pick first per,
   assert row count > 0 individually, assert combined > 0.
3. **AA.A.6.c** — verify, file failures (likely a few real bugs surfaced).
4. **AA.A.7** — extends AA.A.6.a's PickerSpec with `pick_excluded()`
   strategy; same tree-derived parametrize shape; assert row count
   *drops* per toggle.

## Open: target visual for "table_rows"

Each sheet has multiple visuals. AA.B.4's per-sheet test names the target
explicitly (`"Posted Money Records"` for Daily Statement). For the
generic test, two approaches:

1. **Pick the largest table.** Walk `sheet.visuals` for `Table` kind,
   pick the one with the most rows pre-filter. Heuristic — could pick
   wrong target.
2. **Test every Table visual on the sheet.** Stronger — proves every
   table responds correctly to every picker.

Path (2) is the right answer; runtime cost = N_tables × N_pickers per
sheet, but N is small (≤7 picks × ≤3 tables = 21 ops, all under one
sheet load).

## Tree primitives confirmed usable

- `Sheet.parameter_controls: list[ParameterControlLike]` — surfaces all
  pickers on the sheet directly.
- `ParameterDropdown.parameter.name` — gives the URL key (`pL1DsRole`
  etc.); the test doesn't actually need it (the driver's `pick_filter`
  keys on the visible label), but useful for failure messages.
- `ParameterDropdown.selectable_values` — branches `LinkedValues` (DB)
  vs `StaticValues` (Python literal). Either way the live DOM exposes
  the option list, so the test reads from the driver, not the tree.
- `ParameterSlider.{minimum_value, maximum_value, step_size}` — for
  the slider's "pick a non-default value" probe.
