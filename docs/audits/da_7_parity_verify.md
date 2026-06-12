# DA.7 — Cold-read v4 parity verify

**Date:** 2026-06-12
**Phase:** DA.7 (parity verify before release cut)
**Goal:** confirm drill columns decorate identically on App2 + QS for every Table mutation landed in DA.1–DA.6.

## Scope

Per the DA.0 audit, DA.4 landed 15 mutations across 12 Tables in 4 apps. This doc samples the App2 side programmatically (Playwright screenshot per sheet, post-deploy DuckDB seed) and leaves a per-Table QS checklist for the operator to walk through the deployed dashboard.

## App2 side — programmatic verification

Studio booted with the new code (boot-id `359fd25f`) against `sasquatch_pr.yaml` + DuckDB seed. Playwright drove WebKit at 1920×1200; counted `<td.cell-accent-menu>` + `<td.cell-accent>` per sheet.

| Sheet | Table | Expected decoration | App2 cells | App2 verdict |
|---|---|---|---|---|
| L1 Drift | Leaf Account Drift | `account_id` → `accent-menu` | 0 (empty-state) | ⚠ empty-state — table shows "No rows match the current filters" because the demo seed has zero drift violations in the default 7-day window. Decoration logic is correct (verified at unit-test layer via `test_l1_overdraft_account_resolves_to_accent_menu`); needs an operator widening the date range or planting a drift violation to dogfood live. |
| L1 Drift | Parent Account Drift | `parent_account_id` → `accent-menu` | 0 (empty-state) | ⚠ same |
| L1 Overdraft | Overdraft Violations | `account_id` → `accent-menu` | **15** (1 col × 15 rows) | ✓ tinted blue cells (see `da_7_app2_snaps/overdraft.png`); the **operator-flagged bug** is now fixed |
| L1 Limit Breach | Limit Breach Detail | `account_id` → `accent-menu` | 0 (empty-state) | ⚠ same — seed-dependent |
| L1 Exceptions | Exception Detail | `account_id` → `accent-menu` (Class B CLICK + MENU mix) | 0 (empty-state) | ⚠ same — seed-dependent |
| L1 Pending Aging | Stuck Pending Detail | `transfer_id` → `accent-menu` | 0 (empty-state) | ⚠ same |
| L1 Unbundled Aging | Stuck Unbundled Detail | `transfer_id` → `accent-menu` | 0 (empty-state) | ⚠ same |
| L1 Supersession Audit | Transactions Audit | `transfer_id` → `accent-menu` (Class C wire) | (part of 100) | ✓ tinted (see `supersession-audit.png`) |
| L1 Supersession Audit | Daily Balances Audit | `account_id` → `accent-menu` (Class C wire) | (part of 100) | ✓ tinted |
| L1 Daily Statement | Posted Money Records | `transfer_id` → `accent-menu` | 0 (empty-state) | ⚠ same |
| L1 Transactions | Posting Ledger | `account_id` → `accent-menu` (Class C wire + new `business_day` contract column) | **50** (1 col × 50 rows) | ✓ tinted (see `transactions.png`); `business_day` column shows day-truncated `posting` next to the minute-grain `Posting` column |
| L2FT L2 Exceptions | L2 Violation Detail | `entity_a` → `accent-menu` (Class D add) | **30** (1 col × 30 rows) | ✓ tinted (see `l2ft-exceptions.png`) |
| Investigation Account Network | Touching Edges | `counterparty` → `accent-menu` (Class D add) | 0 (empty-state) | ⚠ empty-state — investigation flow requires the operator pick an anchor account |

App2 verdict: every Table that has data renders the tint correctly. The empty-state cases aren't decoration failures — they're "no rows to decorate." The unit + JS tests (DA.6) cover the logic at the row-rendering layer.

### Cell-click affordance — manual sanity

Per DA.3, left-click on a `cell-accent-menu` cell opens the menu (same as the ⋯ button). Verified in JS unit tests (`test_cell_accent_menu_left_click_opens_menu`, `…_stops_propagation_to_row_handler`). Operator should sanity-check one live (e.g. left-click an Overdraft Violations row's account_id cell → menu opens → "View Daily Statement for this account-day" appears).

### Empty-state coverage gap

Eight of 13 Tables can't be visually verified in App2 today because the demo seed produces no rows for them in the default 7-day window. Mitigations:

- **Spot-check after a wider date range.** Operator widens the universal date range to a 365-day window → re-checks Drift / Limit Breach / Pending Aging / Unbundled Aging / Daily Statement / Investigation Account Network. Cells should tint.
- **Live deploy + dogfood.** `recon-gen json apply --execute` and dogfood the QS dashboard (next section). QS has the same data shape so empty-state would also show, BUT operator can plant a drift violation manually via Studio's Trainer or by editing the seed.
- **Backlog: seed coverage for cold-reads.** Trainer plants currently default to "make the violations sparse." A `Trainer plant: keep at least one live drift violation in the default 7-day window` is a non-intrusive cold-read aid. Filing.

## QS side — manual operator checklist

QS-side verification requires deploying the updated apps + dogfooding the embedded dashboard in a browser. App2 ≡ QS by construction (same `Drillable.visual_kind` code path picks the visual on both sides), but the deploy step is destructive (delete-then-create against the operator's AWS account) so I'm not running it automatically.

Steps:

1. `recon-gen json apply -c run/config.yaml -o out/ --execute` — deploys all 4 apps' QS JSON.
2. Visit the L1 Dashboard's **Overdraft** sheet in QS.
3. Verify the **Overdraft Violations** table's `Account ID` column shows accent text on a tint background (the `accent-menu` cue). Pre-DA.1 this column showed plain accent text — the bug the operator originally flagged.
4. Right-click any row → menu should show "View Daily Statement for this account-day".
5. Repeat for the other 12 Tables (rows in the table above marked ⚠ are seed-dependent; widen date range or visit a sheet that has data).
6. Note any cases where QS renders the cue differently than App2 — `Drillable._tint_hex` (QS) and CSS `color-mix(in srgb, accent 10%, transparent)` (App2) compose against different backgrounds (white solid vs. row-stripe transparent), so a few-percent hue delta is expected. A flat unmistakable difference (e.g. QS shows plain accent text, App2 shows tint) IS a bug.

### Class B mix (one site — Exception Detail)

The L1 Exceptions sheet's `account_id` column carries BOTH a `DATA_POINT_CLICK` drill (Narrow Drift to this account) AND a `DATA_POINT_MENU` drill (View Daily Statement for this account-day). Per the DA.0 lock 3, the menu-tint visual subsumes the plain accent. Operator should verify:

- App2: left-click the `account_id` cell → opens the menu (per the DA.3 cell-click affordance). The row-level CLICK drill should NOT navigate (stopPropagation in `bootstrap.js::wireRowDrills`).
- QS: left-click the `account_id` cell → fires the CLICK drill (QS doesn't have a "cell-opens-menu" hook). Right-click opens the menu.

This is the documented App2/QS asymmetry per the convention origin note (App2 is allowed to break "left moves LEFT" when the visual cue makes the affordance discoverable; QS can't because it doesn't have the cell-click hook).

## Verdict

Programmatic App2 verification: **pass** for every Table that has data (5 of 13). 8 require manual operator dogfood with a wider date range or planted violation.

QS verification: **operator-driven** after `recon-gen json apply --execute`. The deploy + visual checklist is laid out above.

When the operator confirms QS parity, DA.7 is closed and DA.8 (release cut) can fire.

## Files

- `docs/audits/da_7_app2_snaps/*.png` — Playwright screenshots, 11 sheets across all 4 apps.
- `docs/audits/da_0_clickability_audit.md` — the original audit (DA.0) with per-site mutation plan.
- DA.6 unit tests at `tests/unit/test_html_table_parity.py` + JS tests at `tests/js/test_row_drills.py` — the by-construction guard that App2 ≡ QS for the decoration logic.
