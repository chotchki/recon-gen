# CL.13 — Sparse balance loads cold-read

**Branch:** cl-sparse-balance @ bbfc315a
**Date:** 2026-06-07
**Reviewer:** Claude-driven cold-read (chotchki away; pre-merge audit)

## TL;DR (post-fix update)

Feature lands end-to-end at the data layer — form picker, loader, seed,
carry-forward matview, cadence-aware invariant, trainer plant, fixture
declarations, Info-tab summary, dogfood gate, AND (post-cold-read fix)
L1 Exceptions UNION ALL wiring all match the locks. The cold-read
caught one material gap before merge: the new
`<prefix>_balance_cadence_gap` matview was emitted but the L1
Exceptions UNION ALL (`schema.py:3100`) wasn't extended to read it,
and `_L1_CHECK_TYPE_VALUES` didn't include the new kind. **Both fixed
in the same CL.13 commit as this audit** — added a UNION ALL branch
projecting (account_id, account_name, account_role,
account_parent_role, business_day_start, gap_kind AS rail_name,
ABS(net_flow), leg_count), bumped the L1 check_type dropdown
universe, updated the static-enum gate test. The CL.8 followup KPI
source-icon plumbing remains a documented backlog entry
(PLAN.md:808) — acceptable follow-on phase, not a CL.13 blocker.
db tier green post-fix: 91 db + 4195 unit passing.

**Verdict: PROCEED with merge.**

## §1 Form-side: operator picks cadence

**VERDICT:** PASS

**EVIDENCE:** `FieldSpec(name="balance_cadence", ...)` lands on both
`_ACCOUNT_FIELDS` (`_studio_editor_routes.py:261`) and
`_ACCOUNT_TEMPLATE_FIELDS` (`:330`), adjacent to `business_day_offset`
per CL.0 §7. Both render `kind="select"` with options `("", "sparse",
"explicit_daily")` (empty = default-sparse). Helper copy distinguishes
the two semantics with operator-readable consequences ("missing day =
gap violation on L1 Exceptions"). Coerce path validates the closed
literal at `:896` with a typed error message. `editor.py:289` and `:318`
thread the value into the `Account` / `AccountTemplate` constructor.
CL.12's dogfood gate (`test_studio_dogfood_browser.py:321-345`)
round-trips the field through real browser form fills.

## §2 Trainer plant fires

**VERDICT:** PASS

**EVIDENCE:** `PLANT_REGISTRY` entry at `plant_registry.py:1607-1636`
under `family="L1 Cap"` with `kind="balance_cadence_gap"`. Single
`PrimitiveIntField(name="days_ago", default=2)` knob. Picker
`_pick_first_explicit_daily_target` (`:1131-1153`) handles both
singleton and template paths — alphabetical-first match, internal-scope
only, returns `(account_id, role)` for singletons or `("", role)` for
templates. `_invoke_balance_cadence_gap_plant` (`:1156-1213`) DELETEs
the matching `daily_balances` row by `account_id` (singleton) or
`account_role` (template fan-out). Raises typed `ValueError` when no
`explicit_daily` entity exists, exactly as CL.0 §8 specifies.
`dashboard_check=DashboardCheck(matview_name="balance_cadence_gap",
min_row_count=1)` joins the BV.3.1 round-trip walk per CL.12 commit
message.

## §3 Gap row surfaces on L1 Exceptions

**VERDICT:** FAIL

**EVIDENCE:** The matview `<prefix>_balance_cadence_gap` is emitted
correctly (`schema.py:579-723` — two-mode `declared_daily_missing` +
`sparse_with_activity` per CL.0 §4), included in `refresh_matviews_sql`
(`:323` + `:432`), and in the drop registry (`:1959`). **But it is NOT
included in the `l1_exceptions` UNION ALL** (`schema.py:3100-3213` — 10
branches, none of which is `balance_cadence_gap`) AND it is NOT in
`_L1_CHECK_TYPE_VALUES` (`l1_dashboard/datasets.py:134-142`) AND a
zero-hit `grep balance_cadence_gap src/recon_gen/apps/` confirms no app
binds to it. The CL.7 trainer plant's `tour_destination` points at
`/dashboards/l1_dashboard/sheets/l1-sheet-exceptions` (`:1627-1630`)
and the handbook claim at `invariants.py:50` says it "surfaces on L1
Exceptions" — both claims are currently wrong. So: plant DELETEs the
row, matview emits the firing, but the operator's L1 Exceptions sheet
doesn't show it. The dashboard side of CL.6 is missing.

## §4 KPI + badges read correctly

**VERDICT:** DEFERRED (per CL.8 followup backlog entry)

**EVIDENCE:** Matview side complete. `daily_statement_summary`
(`schema.py:2959-3072`) emits:

- `closing_balance_source` — 3-state enum (`emitted` /
  `carried_no_activity` / `carried_with_activity_gap`) per CL.0 §2.1.
- `closing_carried_from_date` — populated when carried, NULL when
  emitted; spec'd by §4.a as the source for the App2 empty-state copy.
- `opening_balance_source` — 2-state via LAG over
  `effective_balances.source`.

UI side absent. No `KPIValueSourceIcon` primitive in
`common/tree/visuals.py`. No reference to any `*_source` column in
`apps/l1_dashboard/`. The Opening Balance + Closing Stored KPIs
(`l1_dashboard/app.py:1737-1768`) ship with their pre-CL subtitles —
"The day's stored closing balance from the feed" — even when the value
is carried. App2's `_data_shape.py::shape_kpi` (`:103+`) stamps
`state_icon` only for the existing `kpi_zero_is_healthy` /
`kpi_band_threshold` cases; no source-column path. The Posted Money
Records App2 empty-state override (CL.0 §4.a) is also absent. QS-side
`KPIPrimaryValueConditionalFormatting` for the icon is also absent.

**RECOMMENDATION:** Acceptable to ship CL as-is with the followup
backlogged — the matview WORKS (you can `SELECT
closing_balance_source FROM ..._daily_statement_summary` and get the
right enum) and the operator-visible regression risk is bounded (the
value displays a carried number with no icon — same as pre-CL behavior
for sparse-default fixtures, just now hit more often). The followup
should be the next phase after CL.13 sign-off, not a CL.13 blocker.

## §5 Carry-forward semantics operator-intuitive

**VERDICT:** PASS-WITH-CAVEATS

**EVIDENCE:** `daily_statement_summary` rewrite matches CL.0 §3+§5:
opening reads `LAG(eb.effective_money)` over `effective_balances`
(carry-forward source), not over emitted-only LAG (`schema.py:2974`).
Closing reads `COALESCE(ad.emitted_money, ad.closing_balance_carried)`
(`:3027`). Drift formula unchanged at `:3031-3033`. Spine filter
`WHERE ad.closing_balance_carried IS NOT NULL` (`:3072`) correctly
suppresses pre-emit days. `effective_balances` matview at
`schema.py:2390-2458` uses the correlated subquery shape (PG-compatible
per the audit-error fix below) and the three rewritten L1 invariants
(drift, ledger_drift, overdraft) all read `effective_money` instead of
`balance` (`:2481-2566`).

**CAVEAT:** Without §4's icon plumbing, a sparse-account quiet day
reads as: Opening Balance shows yesterday's carried close, Closing
Stored shows the same carried number, Posting Drift shows $0 with the
healthy ✓ glyph (because `emitted − (opening + flow) = carried −
(carried + 0) = 0`). An analyst seeing this without context can read
"the number is real, the feed is fine" when in fact NEITHER number was
emitted today. The CL.0 audit anticipated this exact case (`§2.1` —
"two-state swallows the most interesting case"). The trade-off is
acceptable for CL exit, but it's the load-bearing reason the §4
followup is non-negotiable for the next phase.

## Additional findings

- **§3 audit error caught + fixed (PG `IGNORE NULLS`)** — CL.0 §3
  claimed all three dialects support `LAST_VALUE(...) IGNORE NULLS OVER
  (...)`; PG 17 does not (the audit's claim that PG 16+ added it was
  wrong). CL.5 commit message captures the correction; the
  implementation switched to a correlated subquery (`schema.py:2434-2441`)
  for cross-dialect portability. No per-dialect arm —
  matches `feedback_sql_dialect_convergence_preferred`. This is the
  CL.14 sign-off footnote the audit doc needs.
- **§5 violation-set shift on re-lock (intended new behavior)** —
  CL.10 commit notes that the pre-CL audit claim of "byte-identity for
  sparse-only fixtures" was wrong. Once `overdraft` reads
  `effective_balances`, a sparse account with a negative carry-forward
  surfaces an overdraft firing on every business day in scope (not just
  the emit day). This is regulator-correct — the institution was in
  overdraft every one of those days — but the audit doc should
  acknowledge the analysis miss in §11. spec_example.duckdb.json
  semantic lock grew +5,400 lines from this.
- **Perf cost (+12.1% db tier matview refresh)** — CL.8 commit notes
  37.9s → 42.5s on the db tier. BZ.3 gate is 60s; +12% sits comfortably
  inside the budget. Bigger-fixture (1M-row) deltas surface per-cell in
  `db-perf/top-queries.md` artifacts.
- **§11 sasquatch_pr post-CL.11 plant target check** —
  `gl-1010-cash-due-frb` is alphabetically the first `explicit_daily`
  declaration in `sasquatch_pr.yaml:120-135` (followed by 9 more GLs
  through `gl-2010-dda-control`). Trainer picker lands on it
  deterministically. **However:** CL.10 commit message flags that the
  partial-slice declaration on `gl-1010-cash-due-frb` "doesn't fire
  balance_cadence_gap because the FRB Cash account emits daily under
  the seed regardless." This means the trainer plant DOES fire (DELETE
  removes the row, matview detects the gap on next refresh) but a
  cold-start dashboard against unpunched data shows zero
  `balance_cadence_gap` rows — fine, that's the trainer's job. spec_example
  declares one `explicit_daily` account (the clearing-suspense one) so
  the CL.12 dogfood gate exercises both declared-non-None and None-skip
  branches.

## Punch list (in priority order)

1. **[BLOCKER]** Add `balance_cadence_gap` branch to the
   `l1_exceptions` UNION ALL (`schema.py:3100+`). Use
   `gap_kind AS rail_name` (or extend the discriminator), `0 AS
   magnitude_amount`, `gap_day_leg_count AS magnitude_count` (or similar
   — keep the per-day-keyed shape matching the per-day branches
   already there). Then add `"balance_cadence_gap"` to
   `_L1_CHECK_TYPE_VALUES` (`l1_dashboard/datasets.py:134-142`) so the
   sheet's check_type dropdown surfaces it. Without this the CL.7
   plant + tour are a dead-end for the operator. Sized as 1-2 hours of
   work + a fixture re-lock cycle.
2. **[BLOCKER]** Or, alternatively, add a dedicated
   `balance_cadence_gap` visual + filter to the L1 Exceptions sheet
   (`apps/l1_dashboard/app.py::_populate_l1_exceptions_sheet`,
   `:1013+`) keyed on the per-day-gap matview directly. This is a
   bigger change but more honest to the two-firing-mode discriminator
   (`gap_kind` doesn't fit the UNION's `magnitude_amount` /
   `magnitude_count` shape cleanly). Pick whichever the operator prefers.
3. **[highest follow-up]** CL.8 followup UI plumbing (already
   backlogged at PLAN.md:808). Typed `KPIValueSourceIcon` primitive +
   tree_fetcher threading + `shape_kpi` state_icon + Daily Statement
   Opening/Closing KPI declarations + App2 empty-state override + QS
   conditional formatting parity. Sized as the audit's §9 work plus the
   §4.a follow-on — likely a 1-day phase on its own.
4. **[medium]** Update CL.0 audit doc with the two acknowledged
   misses (PG `IGNORE NULLS` + sparse-only byte-identity claim) — both
   already captured in commit messages but not folded back into
   `cl_0_sparse_balance_design.md`. CL.14 sign-off prep.
5. **[low]** Update `invariants.py:46-50` handbook entry — current
   text says `balance_cadence_gap` "surfaces on L1 Exceptions" which is
   aspirational until punch-list item 1 lands. Consider rewording as
   "surfaces on Daily Statement (per-cell drill) + L1 Exceptions
   (rollup, pending CL.6 wiring)" if the wiring doesn't ship with this
   phase.
6. **[low]** Consider an L1 Exceptions sheet visual subtitle that
   includes the `sparse_with_activity` vs `declared_daily_missing`
   distinction. Today an operator reading "balance_cadence_gap" doesn't
   know which firing mode they're looking at; the matview's `gap_kind`
   column carries it but the dashboard doesn't surface it. Lands
   naturally with punch-list 1 or 2.

## Sign-off recommendation

Recommend chotchki **HOLD** until punch-list item 1 (or 2) lands. The
CL.8 followup remains acceptable as a documented follow-on phase.
Without the L1 Exceptions wiring, the trainer plant has no operator-
visible destination and the phase's "balance_cadence_gap surfaces on L1
Exceptions" claim doesn't hold. The fix is a small surgical change to
`schema.py::_l1_exceptions` block + the dataset's check_type values
list — probably a one-commit close.
