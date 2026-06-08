# AA.E.1 — Search-by-name-AND-id pattern decision

**Date:** 2026-05-16
**Branch:** `phase-aa`
**Scope:** account-pickers across L1 + Investigation dashboards.

## The ask

> Operator wants both `account_name` and `account_id` **visible AND
> searchable** across all sheets that show accounts. Today the
> Investigation Account-Network anchor dropdown shows `name (id)` via a
> dedicated dataset; other sheets show one or the other.

Three options the PLAN listed:

a. **Concat calc field** — replace `account_id` / `account_name` with a
   single `name (id)` column everywhere.
b. **Two adjacent columns** — keep current pattern (already in place
   for tables), require operator to filter twice.
c. **Single search input** — keep two columns, add a single search
   widget that matches either field.

## Current state (codebase survey)

### Tables (display rows of accounts)

Every L1 sheet table that shows an account renders it as
**two adjacent columns**: `account_id, account_name`. Surveyed
sites: Drift / Overdraft / Limit Breach / Daily Statement /
Transactions / Pending Aging / Unbundled Aging / Supersession Audit
(`apps/l1_dashboard/app.py` table-builder call sites). Already
sortable independently; nothing to change.

### Dropdowns (filter controls picking ONE account)

Two patterns exist today:

- **Investigation Account-Network anchor** — already uses
  `name || ' (' || id || ')'` concat in the source dataset
  (`apps/investigation/datasets.py:215-225`), surfaced via a typed
  `ColumnShape.ACCOUNT_DISPLAY`. The MUI Autocomplete in the search
  variant lets the operator type either part to surface matches; the
  underlying value still binds the bare `account_id` to the parameter.
- **All 7 L1 account-pickers** — Drift / Drift Timelines / Overdraft /
  Limit Breach / Daily Statement / Transactions / Pending Aging — read
  options as `account_id` only, with `options_dataset=ds_accounts,
  options_column="account_id"` in `apps/l1_dashboard/app.py`. The
  operator can't type "Sasquatch Cash Master" — only the raw id.
  (Daily Statement gained the Role cascade in AA.B.1, but the Account
  dropdown itself still shows only `account_id`.)

## Decision: hybrid — concat in dropdowns, two-column in tables

**For dropdowns (filter controls picking ONE account or ONE
transfer): add a `name (id)` display column to the source dataset,
switch the dropdown to read from it. Keep the underlying parameter
binding the bare `account_id`.**

**For tables: leave the two-column adjacent pattern alone.** It's
already in place across every L1 sheet; no operator complaint about
it. Independent sortability + per-column conditional formatting is a
real benefit the concat would erase.

### Why

- **The dropdown is where search matters.** QS's MUI Autocomplete
  search-variant runs a substring match against the displayed string.
  Concatenating `name (id)` makes both fields searchable in a single
  input — type "Sasquatch" or "external-001", either matches.
  Two-column / smart-input alternatives don't apply to a dropdown
  widget (it has one option text per row).
- **Tables stay sortable + scannable.** Operators reading a Drift
  exception want to glance the id column for the canonical key. A
  single `Sasquatch Cash Master (external-001)` column halves their
  per-row scan-speed AND breaks alphabetical id sorting (which
  becomes alphabetical name sorting).
- **Mirrors the proven Investigation Account-Network pattern.**
  That sheet already does dropdown-concat + table-two-column. The L1
  account-pickers are the ones lagging.
- **Underlying SQL bind stays clean.** The parameter still receives a
  bare id (the visual layer's `LinkedValues` reads `display_column`
  for the *label*, not the *value*). All existing `WHERE account_id =
  <<$pX>>` clauses keep working unchanged.

### What option (c) — "single search input" — would have required

A separate widget composed on top of two columns, with custom
search-logic that walks both fields. QS doesn't have a native
table-cell search box; building one would mean either a custom
analysis sheet control (no good QS primitive) or a JS overlay on the
embed (out of scope; embed is the iframe boundary). App2 has table
sort + pagination only — no search input either. Building one in
both renderers is a separate phase of work, not a Phase-AA scope item.
Skipped.

## AA.E.2 wiring scope

7 L1 dropdowns to flip:

1. `_populate_drift_account_dropdown` (L1 Drift)
2. `_populate_drift_timelines_account_dropdown` (L1 Drift Timelines)
3. `_populate_overdraft_account_dropdown` (L1 Overdraft)
4. `_populate_limit_breach_account_dropdown` (L1 Limit Breach)
5. `_populate_ds_account_dropdown` (L1 Daily Statement — AA.B.1)
6. `_populate_transactions_account_dropdown` (L1 Transactions)
7. `_populate_pending_aging_account_dropdown` (L1 Pending Aging)

Implementation shape (mirrors `apps/investigation/datasets.py`):

- Extend `DS_L1_ACCOUNTS` dataset SQL to add an
  `account_display = account_name || ' (' || account_id || ')'`
  column. Add `ColumnShape.ACCOUNT_DISPLAY` to the contract.
- Switch each dropdown's `options_column` from `"account_id"` to
  `"account_display"`. The label visible to the operator becomes
  `"Sasquatch Cash Master (external-001)"`; the value bound to the
  parameter via `LinkedValues` is `display_column = account_display,
  value_column = account_id` — split via a `LinkedValues` shape if QS
  supports a separate value column, else strip the trailing ` (id)`
  via a SQL pushdown clause's `LIKE '%(' || <<$pX>> || ')'` if
  necessary.
  - **Decision on the split**: pin in AA.E.2 once we verify QS's
    `LinkedValues` accepts distinct `LabelColumn` vs `ValueColumn`
    fields. If yes (the more likely path), parameter bind stays bare
    id. If no, switch to label-only and rewrite the pushdown clause to
    match by parsing the parenthetical out — uglier but workable.

Tables stay untouched. AA.E.3 covers the browser e2e (per-renderer
verify: type a name fragment, the dropdown narrows; pick the option,
the underlying matview narrows to the right account_id).
