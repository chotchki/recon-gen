# DL.0 — Cross-sheet drill content + picker-value guardrail (design lock)

**Date:** 2026-06-15
**Phase:** DL.0
**Status:** Locked. Operator-confirmed in PLAN.md `## Phase DL` block on 2026-06-15. Ready for DL.1.

## Problem statement

Operator observation 2026-06-15 — jumping from the L1 Drift "leaf account drift" cell to the Daily Statement sheet doesn't pre-populate the destination's account picker. The drill's `account_id` reaches the destination URL / parameter store, but the picker control on Daily Statement doesn't bind to it; analysts read the destination as "any account" when the upstream drill said "this account".

The reported bug is real, but the cross-cutting observation matters more: **there is no guardrail today asserting that any cross-sheet drill actually lands on a populated destination.** Every drill — and there are several per app, across four apps and two renderers — is currently covered by an operator manually clicking it during a cold-read. A regression in any drill's wiring stays silent until someone happens to walk that flow.

DL closes the gap by:

1. Programmatically enumerating every `Drill` in every app's tree (no hand-maintained allowlist).
2. Parametrizing an e2e test over both renderers (`[qs, app2]`) and every enumerated drill.
3. Per parametrize call, asserting BOTH that the destination renders content AND that the destination's filter picker for the drilled column reflects the drilled value.

The user-reported drift→daily-statement bug lands as one of the parametrized failures DL.2 surfaces; DL.3 triages and fixes it at the seam. Future drill regressions get caught at chain time, not at operator-cold-read time.

## Locks (operator-confirmed 2026-06-15)

- **Enumeration shape: tree-walk every `Drill`** (operator-confirmed via AskUserQuestion 2026-06-15 — programmatic over hand-listed). The walker visits every `analysis.sheets` entry, every visual on it, every drill action on the visual, and yields a tuple `(src_sheet, src_visual, drill, dst_sheet)` for every drill where `drill.target_sheet.sheet_id != src_sheet.sheet_id` (cross-sheet only — same-sheet walk-the-flow drills are covered by existing per-sheet tests). Lives in `tests/e2e/_helpers/drill_enumeration.py` so unit + e2e tests can both call it.
- **Two assertions per drill: content renders AND picker shows drilled value** (operator-confirmed via AskUserQuestion 2026-06-15 — both over content-only). The content assertion (`wait_loaded(target_visual)` on a representative destination visual) catches the broader blank-destination class. The picker-value assertion (the destination's filter picker for the drilled column matches what the drill wrote) catches the specific user-reported bug — value-mismatch where the destination renders but the picker disagrees. Without the second assertion, the test might pass on a destination that renders the WRONG account's data with no warning.
- **Both renderers** (`[qs, app2]` parametrize per existing E2E test convention). Cross-sheet drill mechanics differ across renderers (QS writes to the parameter store via DrillWrite; App2 threads via URL `?param_<name>=<value>` into the destination's filter form initial state). Both paths need coverage.
- **POLICY 2 structured-triple for renderer-specific permanent gaps.** If a drill's picker-value assertion can never pass on one renderer because of a documented capability gap (e.g. [[project_qs_url_parameter_no_control_sync]] — QS URL parameter doesn't sync sheet controls; the data filters but the picker shows "All"), DL.3 applies the POLICY 2 structured triple: `NotImplementedError` in the driver verb with a comment cross-linking the memory entry; an entry in `docs/reference/quicksight-quirks.md`; a memory file under `project_<renderer>_<gap>.md`. Bare skip / xfail is not an option per POLICY 2.
- **One commit per leaf** (bisect-friendly); release ships as part of v14.5.0.

## Why tree-walk enumeration over a hand-listed allowlist

A hand-listed allowlist captures the drills the test author knew about at write time. New drills added by future tree authors silently bypass the guardrail until somebody updates the allowlist — which nobody does, because the drill works locally and there's no signal that the allowlist is stale.

The tree-walk enumerates from the same source-of-truth the production renderer reads: `App.analysis.sheets[*].visuals[*].drill_actions`. Adding a new drill in a tree builder automatically extends the parametrize set. Removing a drill removes the corresponding parametrize. The guardrail's coverage tracks the tree's coverage by construction.

Cost: parametrize count grows with the tree. Today across the four apps (Investigation, Executives, L1 Dashboard, L2 Flow Tracing) the drill count is in the low double-digits per app. Times two renderers, that's roughly 50-100 parametrized tests. Each one drives a real browser through a navigate + drill + load sequence — wall-time will be measurable but bounded by the existing chain's qs_browser layer budget.

## Why both assertions (content AND picker)

A content-only assertion (`wait_loaded(target_visual)` passes) verifies that the destination doesn't 500 / time out / render a SQL error overlay. It does NOT verify the destination is showing the data the operator expected. The drift→daily-statement bug the user surfaced is exactly this case: the destination renders, the table populates, but the picker is wrong and the analyst can't tell what they're looking at.

The picker-value assertion closes that loop: after the drill, read the destination's picker control via `driver.filter_options(picker_label)` (or `driver.picked_value(picker_label)` — DL.2 picks the verb that exists on both drivers), assert it contains the drilled value.

Edge case: a drill that writes multiple params (e.g. drift→daily-statement might write `account_id` AND `business_day`). DL.2 asserts ALL of them; missing one is a regression worth catching.

## Rejected alternatives

| Option | Pro | Con | Verdict |
|---|---|---|---|
| Hand-listed allowlist | Faster to author; lower wall-time. | Misses drills the test author didn't know about; goes stale as tree changes. | **Rejected.** Misses the whole point of "guardrail" — the bug the user surfaced is on a drill that already exists. |
| Content-only assertion | Faster per-test; less coupling to picker driver verbs. | Doesn't catch the specific bug type the user surfaced (value-mismatch with content rendering correctly). | **Rejected.** The phase's reason-for-being is the drift→daily-statement case, which is a value-mismatch. |
| Static analysis (lint the tree at unit time) | No browser cost; very fast. | Can't actually test the drill — only checks the wiring shape exists. The bug is in runtime parameter passing, not the wiring shape. | **Rejected.** Unit-level smoke tests already exist for drill wiring; the gap is the live behavior. |
| QS-only or App2-only | Half the wall-time; half the parametrize count. | The two renderers fail differently. Single-renderer coverage doesn't catch cross-renderer divergence (which IS the failure shape: same drill, picker syncs on App2, doesn't on QS). | **Rejected.** [[project_app2_parity_for_offline_iteration]] — both renderers matter; divergence is itself the bug class to catch. |

## QS URL-param-no-control-sync interaction

[[project_qs_url_parameter_no_control_sync]] (recorded 2026-04-05, confirmed permanent) — QS's URL parameter doesn't sync sheet controls. The drill action writes to the parameter STORE (good — data filters narrow) but NOT the bound control widget (bad — picker shows "All"). This is a known permanent capability gap, not a bug to fix.

When DL.2's parametrize runs against a QS drill that lands in this case, the picker-value assertion will fail. DL.3 applies the POLICY 2 structured triple:

1. **Driver verb side**: `QsEmbedDriver.picked_value(picker_label)` (or equivalent) raises `NotImplementedError` with a comment naming the gap and cross-linking the memory entry. The parametrize call's QS branch becomes a skip-with-reason, not a silent xfail.
2. **Quirks log entry** in `docs/reference/quicksight-quirks.md` (already present; DL.0 cross-links it).
3. **Memory file** `project_qs_url_parameter_no_control_sync.md` (already present; DL.0 cross-links it).

Caveat: the picker DOES sync for some drill types on QS — specifically when the drill writes via the `MappedDataSetParameters` shape (which fires on parameter-write actions, just not URL-initial-load actions). Verification per-drill is the work, not a blanket QS skip.

## Test impact analysis

| Surface | Impact | Mitigation |
|---|---|---|
| Chain qs_browser layer wall-time | ~50-100 new parametrized tests at ~5-15s each | Profile in DL.2; if wall-time threatens watchdog, split into a dedicated `tests/e2e/drill_guardrail/` subdir with its own xdist-group |
| Per-drill driver verb coverage | DL.2 may need `picked_value()` / `picker_options()` verbs that don't exist on one or both drivers | Build the missing verb per [[feedback_build_verbs_not_skip]]; structured triple only if the gap is truly permanent |
| Drill enumeration helper unit tests | Unit-test against a hand-built tree fixture so a Drill model change surfaces at unit time, not at chain time | DL.1 adds the unit test alongside the helper |
| Existing drill tests | Per-app cold-read tests + same-sheet drill tests stay — DL covers the cross-sheet gap, not the surface those tests cover | DL.2 explicitly skips same-sheet drills (`drill.target_sheet.sheet_id == src_sheet.sheet_id`) so coverage doesn't overlap |

## Risks + open questions

- **Drill source-row uniqueness.** Some drills write multiple parameters. The source row's value for each param needs to be readable from the source visual's table cells. DL.2 should assert each drilled param against the corresponding source-row cell — adding driver verbs if needed. Falling back to "click row 0, assume first-row values" works only if the source visual surfaces all drilled columns visibly.
- **Renderer driver-verb coverage gaps.** DL.2 may discover that `picked_value()` or `picker_options()` isn't implemented on one renderer for some control kind. Per [[feedback_build_verbs_not_skip]], the default is to build the verb. If the verb genuinely can't exist (QS URL-param case), apply structured triple.
- **DL.4 surface size.** The reported drift→daily-statement bug is the visible tip; the iceberg below may include several drills that have been silently broken. DL.4 fixes are intentionally one-per-commit so bisect history stays clean if we need to revert any.
- **Phase wall-time.** DL.2 adds parametrized browser tests. If wall-time pushes the chain past comfortable bounds, consider gating the guardrail behind a dedicated `--drill-guardrail` flag for fast-iteration loops while keeping it on for CI's full chain.

## Cross-references

- Implementation tasks: PLAN.md `## Phase DL` block, leaves DL.0 through DL.5 (committed 2026-06-15).
- Related: [[project_qs_url_parameter_no_control_sync]] — the QS URL-parameter-no-control-sync quirk that one or more drills will hit.
- Related: [[feedback_build_verbs_not_skip]] — default to building a missing driver verb, not skipping the param. Structured-triple only when the gap is permanent.
- Related: `docs/reference/quicksight-quirks.md` — append-only QS quirks log; DL.3 may add an entry per drill that hits a permanent gap.
- Related: [[feedback_verify_at_user_facing_renderer]] — verify fixes at the renderer the user sees. The drift→daily-statement bug was reported on the user-facing path; DL.2 covers both renderers so the fix lands at the right seam.
- Related: PLAN.md `## Phase DK` — DK.10 added Flatpickr date-picker max-date clamp; DK.11 added entity-leak browser scanner. DL extends the "browser drivers catch UX bugs the operator would otherwise miss" pattern to drill UX.
