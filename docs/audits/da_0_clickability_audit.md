# DA.0 — Clickability decoration audit

**Date:** 2026-06-12
**Phase:** DA.0 (audit + scope confirmation)
**Goal:** map every drillable Table column to its current `CellFormat` (if any) and the correct `CellFormat` per the Phase DA locks, so DA.1–DA.7 know exactly what to change.

## Methodology

Walked every `add_table(...)` block across `src/recon_gen/apps/` via AST-shaped regex (Python). For each block extracted:

- Table title.
- `actions=[...]` → drill name(s), trigger(s), and the (param, source) `writes` tuples.
- `conditional_formatting=[...]` → `CellAccentText` / `CellAccentMenu` callsites with their `on=` column.

13 `CellAccentText` callsites total (all in `apps/l1_dashboard/app.py`). Zero `CellAccentMenu` callsites anywhere. Two other apps (`l2_flow_tracing`, `investigation`) have drillable Tables with NO `conditional_formatting` at all — uncovered defect.

## The locks (operator-confirmed 2026-06-12, re-stated for traceability)

Per `PLAN.md::Phase DA` + operator's review of this doc:

1. **Collapse `CellAccentText` + `CellAccentMenu` → single `Drillable(on=Dim)` type** (operator: "is cell accent text really just a code smell that should be removed?" → yes). The visual cue (accent text vs accent + tint background) auto-derives from the trigger of the drill writing from the column at plan-build / emit time. Authors don't pick the cue — they declare the column is drillable, and the type system + renderer pick the visual from the drill triggers.
   - `Drillable.on` column has ≥1 `DATA_POINT_MENU` drill writing from it → menu-tint visual (accent text + accent-tint background).
   - `Drillable.on` column has ONLY `DATA_POINT_CLICK` drill(s) writing from it → accent-text-only visual.
   - `Drillable.on` column has NO drills writing from it → **`ValueError` at `Table.__post_init__`** (DA.4 type gate).
2. **App2 cell-click opens the menu on menu-decorated cells.** Per operator comment 5: "making the column clickable is fine... the row was the problem." The `<td.cell-accent-menu>` left-click → opens the menu (same code path as the ⋯ button click). Row left-click stays inert (the row-drill commit `279c52c8` contract holds). Right-click contextmenu on row still opens the menu. **Documents an exception to "left clicks move LEFT"**: a left-click on a menu-decorated cell goes right (opens the menu). Operator-accepted: the explicit visual cue makes the affordance discoverable.
3. **Tint hue:** auto-derived via `color-mix(in srgb, var(--color-accent) 10%, transparent)` (no new theme token).
4. **Class C resolution (per-site, per operator comment 2 — "wire if possible, strip if not"):**
   - **Transactions Audit / `tx_id_col` (line 1671):** WIRE → "Find this transaction in Posting Ledger" (`DATA_POINT_MENU`, writes `tx_id_col` to a new `_DP_TX_TXN_ID` param on the Transactions sheet's posting-ledger filter).
   - **Daily Balances Audit / `db_account_col` (line 1698):** WIRE → "View Daily Statement for this account-day" (same shape as Drift sheets).
   - **Posting Ledger / `account_col` (line 1747):** WIRE → "View Daily Statement for this account-day".
   - **Posting Ledger / `transfer_col` (line 1748):** **STRIP** — natural target is the Transactions sheet, which IS the sheet hosting Posting Ledger (self-drill).
5. **Class D:** decorate both L2FT Violation Detail + Investigation Account Network with `Drillable`.

### Convention origin note (operator clarification)

> "left vs right click all stems from crappy quicksight limitations"

The "left moves LEFT / right moves RIGHT" rule is a QuickSight-side workaround (the QS analysis layer only has `DATA_POINT_CLICK` + `DATA_POINT_MENU` triggers + a single click-direction convention to keep them tellable apart at the user level). It is NOT a deep design principle — it's a pragmatic split forced by QS's gesture vocabulary. App2 inherits the convention for parity, but is free to break from it when a better affordance exists for the App2 surface (lock 2 is one such break). Don't over-defend the rule in future design discussions.

## Findings — per `add_table` block

### A. MENU-only drills with WRONG `CellAccentText` (must swap → `CellAccentMenu`)

These are the operator-flagged shape: cell looks accent-text (left-click cue) but the drill is only MENU. Seven sites in L1.

| Sheet (fn) | Table title | Line | `on=` | Drill name | Trigger | Action |
|---|---|---|---|---|---|---|
| `_populate_drift_sheet` | Leaf Account Drift | 821 | `leaf_account_col` | View Daily Statement for this account-day | `DATA_POINT_MENU` | **Swap** → `CellAccentMenu` |
| `_populate_drift_sheet` | Parent Account Drift | 865 | `parent_account_col` | View Daily Statement for this account-day | `DATA_POINT_MENU` | **Swap** → `CellAccentMenu` |
| `_populate_overdraft_sheet` | Overdraft Violations | 1043 | `account_col` | View Daily Statement for this account-day | `DATA_POINT_MENU` | **Swap** → `CellAccentMenu` ← operator-flagged |
| `_populate_limit_breach_sheet` | Limit Breach Detail | 1317 | `account_col` | View Daily Statement for this account-day | `DATA_POINT_MENU` | **Swap** → `CellAccentMenu` |
| `_populate_pending_aging_sheet` | Stuck Pending Detail | 1430 | `transfer_col` | View Transactions for this transfer | `DATA_POINT_MENU` | **Swap** → `CellAccentMenu` |
| `_populate_unbundled_aging_sheet` | Stuck Unbundled Detail | 1554 | `transfer_col` | View Transactions for this transfer | `DATA_POINT_MENU` | **Swap** → `CellAccentMenu` |
| `_populate_daily_statement_sheet` | Posted Money Records | 1871 | `transfer_col` | View Transactions for this transfer | `DATA_POINT_MENU` | **Swap** → `CellAccentMenu` |

### B. CLICK + MENU on the same column (`CellAccentMenu` wins per lock 3)

One site. The column has BOTH a left-click drill AND a right-click menu drill writing from it.

| Sheet | Table title | Line | `on=` | Drills | Current | Per lock |
|---|---|---|---|---|---|---|
| `_populate_l1_exceptions_sheet` | Exception Detail | 1189 | `account_col` | `DATA_POINT_CLICK` ("Narrow Drift to this account") + `DATA_POINT_MENU` ("View Daily Statement for this account-day") both write from `account_col` | `CellAccentText` | **Swap** → `CellAccentMenu` |

**Operator-confirm lock 3:** is `CellAccentMenu` wins (tint subsumes accent) the right semantic? Alternative reading: render BOTH cues (accent text + tint background) — but `CellAccentMenu` already emits both colors so it's the same visual. Locking `CellAccentMenu` wins.

### C. `CellAccentText` with NO drill from the column (cue is a lie)

Five sites. Cells render in accent text but the operator clicks and nothing happens.

| Sheet | Table title | Line | `on=` | Has drill? | Recommended action |
|---|---|---|---|---|---|
| `_populate_supersession_audit_sheet` | Transactions Audit | 1671 | `tx_id_col` | No `actions=` at all | **Decide**: (a) remove `CellAccentText` to stop suggesting a non-existent click affordance, or (b) wire a drill that the cue is supposed to advertise |
| `_populate_supersession_audit_sheet` | Daily Balances Audit | 1698 | `db_account_col` | No `actions=` at all | Same |
| `_populate_transactions_sheet` | Posting Ledger | 1747 | `account_col` | No `actions=` at all | Same |
| `_populate_transactions_sheet` | Posting Ledger | 1748 | `transfer_col` | No `actions=` at all | Same |

**Operator-confirm edge case C:** keep the `CellAccentText` and add the missing drill, or strip the format? My read of the original intent (these are detail tables where the analyst is already at the bottom of the navigation tree) is they're vestigial — no drill was ever planned. Recommend: **strip** in DA.3 unless you flag otherwise. This is the kind of cleanup the type-system gate in DA.4 will permanently prevent.

### D. Drillable column with NO `CellFormat` at all (decoration missing)

Two sites in other apps. These tables expose drills but never advertise visually — even on the QS side, no analyst will discover the drill without hovering.

| App | Sheet (fn) | Table title | Line | Drill column | Triggers | Recommended action |
|---|---|---|---|---|---|---|
| `l2_flow_tracing` | (sheet body) | L2 Violation Detail | 1311 | `entity_a_col` (BOTH drills write from it) | `DATA_POINT_MENU` × 2 (View in Rails / View in Chains) | **Add** `CellAccentMenu(on=entity_a_col, ...)` |
| `investigation` | (sheet body) | Account Network — Touching Edges | 993 | (need to inspect — likely the account column) | `DATA_POINT_MENU` | **Add** `CellAccentMenu(on=<account col>, ...)` |

- Comment: I'd rather fix these to show visually.

### E. Executives — no drills (consistent with role)

Audit found ZERO `add_table(...)` blocks with `actions=` in `apps/executives/`. Expected: Executives is the high-level summary surface; drilling deeper belongs to other apps. No action needed.

## Summary by class

| Class | Sites | Action |
|---|---|---|
| A — MENU drill wrong format | 7 | Swap `CellAccentText` → `CellAccentMenu` |
| B — CLICK + MENU mix | 1 | Swap `CellAccentText` → `CellAccentMenu` (lock 3) |
| C — Format without drill | 5 (4 columns × 2 sheets + 1 extra) | **Strip** (DA.3 — operator can override) |
| D — Drill without format | 2 | Add `CellAccentMenu` |
| **Total mutations DA.3 will land** | **15 sites across 12 tables, 4 apps** | |

## Open questions for operator (please confirm before DA.1 fires)

1. **Lock 3 — CLICK + MENU mix.** Site B (Exception Detail) has a `CellAccentText` on `account_col` even though there's both a CLICK and a MENU drill writing from it. The Phase DA draft says "`CellAccentMenu` wins (tint subsumes accent-text cue)". Confirm: ✕ swap to `CellAccentMenu` / ☐ keep `CellAccentText` and represent the MENU drill differently / ☐ allow both formats stacked (currently rejected — `CellFormat` is one-format-per-column-per-table).
 - Comment: Menu wins in my mind. We should make those mutually exclusive OR is cell accent text really just a code smell that should be removed?

2. **Class C — accent text without drill.** Five sites currently visually suggest a click affordance that doesn't exist. Two reasonable resolutions:
   - **Strip** the `CellAccentText` (recommended — type-gate in DA.4 will permanently prevent re-introducing one without a matching drill). The cell renders as plain text alongside its peers.
   - **Wire the missing drill.** Each of these columns COULD have been planned as a drill source (e.g. `tx_id` → "find this txn in its bundle"); was the formatting an unfinished feature? If yes, DA.3 should also stage the drill wiring. Estimate: +4 hours.
   - Comment: Wire if its possible, strip if not.

3. **Class D — uncovered decoration.** L2FT Violation Detail + Investigation Account Network — Touching Edges are currently undecorated. DA.3 will add `CellAccentMenu`. Confirm: any L2FT / Investigation sheet you'd also like decorated, or just these two? (My grep found these two; nothing else in those apps carries `actions=` on a table.)
  - Comment: Decorate.

4. **Tint hue.** `CellAccentMenu` emits both a text color and a background color. The L1 sheets use `accent` (the theme accent token) for text. What background hue?
   - Tinted accent (e.g. `color-mix(in srgb, accent 10%, transparent)`) — auto-derived, single source of truth, default per `[[project_design_north_stars]]`.
   - A separate `accent-tint` theme token — explicit but doubles the theme contract.
   - Recommend tinted accent unless you want palette control.
   - Comment: tinted accept

5. **App2 cursor/hover on `cell-accent-menu` cells.** Per the row-drill MENU contract (`commit 279c52c8`), MENU drills don't make `<tr>` left-clickable. Should the `<td>` itself become left-clickable on the menu-decorated column (would fire the menu directly without needing the ⋯ button — a "click the highlighted cell to open the menu" affordance) or stay non-clickable (menu opens only via ⋯ button + right-click contextmenu)?
   - Stay non-clickable — strict adherence to "left clicks move LEFT, right clicks move RIGHT".
   - Cell-click opens menu — friendly affordance; arguably violates the rule.
   - Recommend: **stay non-clickable** for consistency with the convention.
  - Comment: making the column clickable is fine... the row was the problem.

## Type-system gate (DA.4 preview)

Once the swaps land in DA.3, the gate at `Table.__post_init__` (in `common/tree/visuals.py`) will:

```python
# For each CellFormat in self.conditional_formatting:
#   For its on.column:
#     Find every Drill in self.actions where the writes tuple references
#     a Dim with the same column.
#     If CellAccentText → require ≥1 such drill with DATA_POINT_CLICK trigger.
#     If CellAccentMenu → require ≥1 such drill with DATA_POINT_MENU trigger.
#     Mismatch → raise ValueError at construction with table.title + offending
#                format + drill triggers.
```

This permanently prevents Classes A, B, C, D from recurring. The unit test that constructs a mismatched Table + asserts the raise is part of DA.4.
