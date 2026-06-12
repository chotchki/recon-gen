# QS Browser Skip Triage

**Date:** 2026-06-11
**Backlog:** BV-post.qs-browser-skips
**Run reference:** `runs/20260611T154547Z-97f91d77/qs_browser/stdout.log`
**Headline:** 76 passed, 117 skipped, 2 xfailed, 2 xpassed (~60% skip rate); plus 12 skipped in the second pytest invocation (`test_audit_dashboard_agreement.py`).

## Methodology

1. Parsed the most recent qs_browser run's `stdout.log` for the headline counts.
2. Collected every `-m browser` test (`202/462 collected (260 deselected)`) under the same env (`RECON_GEN_E2E=1`, `RECON_GEN_CONFIG=run/config.postgres.yaml`, `RECON_GEN_TEST_L2_INSTANCE=tests/l2/sasquatch_pr.yaml`, `AWS_PROFILE=recon-gen-local`).
3. Static-analyzed every `pytest.skip(...)`, `@pytest.mark.skip`, `@pytest.mark.skipif`, and `@pytest.mark.xfail` in `tests/e2e/` plus `tests/e2e/_drivers/` (the `skips_if_unsupported` bridge).
4. Cross-referenced parametrize cardinality (`[qs, app2]`, `[postgres, oracle]`, `ALL_L1_INVARIANTS`, sheet/picker spec lists) against the gating predicates to estimate skip counts per source. Counts marked **(est)** are static-analysis derived; the next push-driven run can confirm with `-rs` reporting.
5. Bucketed by cause + recoverability.

**Why static-only:** the qs_browser cell takes ~30 min end-to-end (db + app2 + deploy + qs_api + qs_browser layers chained); the latest run finished 2 h ago and the dot-output (`s.s.s.s`) is enough for counts but not per-test reasons. Static analysis hits both: every skip path is in the source. (To get per-test reasons reproducibly, drop `-q` for `-rs` in the runner's `cmd_up_to` for one investigation cycle.)

## Skip counts by bucket

| Bucket | Count (est) | Recoverable? | Lever |
|---|---|---|---|
| **A. Per-cell dialect-cfg mismatch** (`[oracle-*]` on the PG cell) | ~30 | No — by design | sibling `sp_or_lo` cell runs them |
| **B. Deprecated test still in tree** (CB.5 supersession) | 14 | Yes (delete) | 1 PR to delete two files |
| **C. Seed-data-shape skips** (anchor row empty, dataset empty, no fanout) | ~50 | Partial (seed-side fix) | enrich `sasquatch_pr.yaml` seed |
| **D. `skips_if_unsupported` verb gaps** (cross-renderer NotImplementedError) | ~5 | Yes (implement verb) | App2-only metadata popup on QS; QS typeahead_filter for non-empty query |
| **E. Per-renderer skip on QS unavailability** (`qs_driver is None`) | ~3 | No — degradation pattern | sentinel artifact already written |
| **F. Missing operator scaffolding** (`run/sasquatch_pr.yaml`, docker daemon, `quicksight-gen` bin) | ~3 | Conditional | depends on operator setup |
| **G. Module-level RECON_GEN_E2E gate** (re-asserted post `pytest_collection_modifyitems`) | 0 | N/A | always satisfied when `RECON_GEN_E2E=1` |
| **H. `pytest.xfail` (Sasquatch L1 render flake; QS context-menu)** | 2 xfailed + 2 xpassed | Partial | backlog #466 (BL.3 follow-on); QS DATA_POINT_MENU stale wiring |
| **TOTAL skips** | 117 | | |

### Bucket A — Per-cell dialect-cfg mismatch (~30 skips)

**Source:** `tests/e2e/_agreement_helpers.py::load_dialect_cfg` — every test that parametrizes `[postgres, oracle]` skips the wrong dialect when the runner cell injects `RECON_GEN_CONFIG=run/config.postgres.yaml`.

Files driving this bucket:
- `tests/e2e/qs_browser/test_audit_invariants_qs.py` — `[postgres, oracle] × 6 invariants` = 12 tests, 6 skip on PG cell (oracle leg).
- `tests/e2e/app2/test_audit_invariants_app2.py` — same shape = 12 tests, 6 skip.
- `tests/e2e/test_audit_dashboard_agreement.py` — 6 invariants × 2 dialects = 12 tests, ALL 12 skip (deprecated; see bucket B — overlaps).

**Verdict: intentional.** The sibling `sp_or_lo` cell runs the oracle leg. Cleanup would require either splitting into separate test files per dialect (loses parity-driven design) or moving the dialect parametrize up to a runner-cell axis (already done — that's why it's `sp_pg_lo` vs `sp_or_lo`). **Leave as is.**

### Bucket B — Deprecated test still in tree (14 skips, fully recoverable)

**Source:** Two `@pytest.mark.skip` markers explicitly tagged "CB.5 stage 2: superseded by per-renderer producers + high-watermark validators. One commit transition: this skip stays for one commit so the operator can confirm the new shape passes; CB.5 follow-up deletes the file entirely."

- `tests/e2e/test_inv_dashboard_agreement.py::test_invariant_three_way_agreement[anomaly|money_trail]` — 2 skips.
- `tests/e2e/test_audit_dashboard_agreement.py::test_invariant_four_way_agreement` — runs as separate pytest invocation; 12 skips per dialect = 12 in current shape (postgres-only cell sees 6 oracle skips overlap with bucket A; 6 postgres skips are this bucket).

**Verdict: fully recoverable. Delete these two `@pytest.mark.skip` decorators + delete the file bodies entirely** (the new per-renderer producers under `tests/e2e/qs_browser/test_inv_anomaly_agreement.py`, `tests/e2e/qs_browser/test_inv_money_trail_agreement.py`, and `tests/e2e/qs_browser/test_audit_invariants_qs.py` already cover the same surface; CB.5 stage 2 was supposed to delete these after one "transition" commit and it didn't happen).

**Effort:** xs. **Recoverable:** 14 skips disappear from the count (not because they pass — because they're gone).

### Bucket C — Seed-data-shape skips (~50 skips, partial recoverability)

**Source:** Tests that walk the deployed L2's matviews/datasets and skip when the seed didn't plant the row shape they exercise.

The matched skip strings (all `pytest.skip(...)` calls in test bodies):
- `test_l1_additive_pickers.py` — three skip paths (`empty BEFORE any pick`, `dataset SQL returns 0 rows`, `anchor narrowing produced 0 rows`) × 7 sheets × 2 renderers = up to 42 potential skips; not all fire — only the sheets where `sasquatch_pr` seed underplants.
- `test_l2ft_additive_pickers.py::_anchor_or_skip` — `chain_instances` on `spec_example` is empty by design (auto_scenario plants `MerchantSettlementCycle`, declared chain is `ExternalReconciliationCycle`); skips fire when fuzz seed declares only one of (chains, templates).
- `test_l2ft_cross_sheet_drill.py` — two skip paths (no rail-targeted rows, no chain-targeted rows) — ~4 skips on cells with thin exception seed.
- `test_l2ft_exceptions.py` — `Unified L2 exceptions dataset is empty` — 2 skips.
- `test_inv_filters.py` — three skip paths (Flagged Pair-Windows starts at 0, Money Trail Hop-by-Hop empty, Recipient Fanout empty) — up to 6 skips × 2 renderers = 12 on lean fuzz seeds; sasquatch_pr typically plants ≥1.
- `test_parameter_anchored_sheets.py` — anchor visual empty + Money Trail Hop-by-Hop empty — up to 4 skips × 2 renderers.
- `test_l1_cross_sheet_drill_date_widening.py` — no stuck rows in seed — 1 skip × 2 renderers.

**Verdict: partially recoverable.** All of these are "seed reality vs. test expectation" — the right fix is in the seed, not the test (per `feedback_production_honest_invariants` and `feedback_spec_example_seed_thin_for_validation`). High-value seed-side fixes:
- Plant Limit Breach rows in `sasquatch_pr` auto_scenario (referenced BK.6 / Backlog #35) — would unblock ~6 L1 additive picker skips.
- Plant fanout rows above the default sigma threshold (referenced backlog #338 era) — would unblock ~4 Recipient Fanout / Flagged Pair-Windows skips.
- Plant multi-hop Money Trail edges for `spec_example`/`sasquatch_pr` — would unblock ~6 anchor / hop-by-hop skips.
- Plant rail-targeted + chain-targeted L2 exception rows — would unblock ~6 L2FT cross-sheet drill / exceptions skips.

**Effort per fix:** s-m (each seed-side fix touches `apps/<app>/datasets.py`, `common/l2/auto_scenario.py`, semantic-lock snapshot regen). **Recoverable:** ~22 if all four seed-side fixes land.

### Bucket D — `skips_if_unsupported` verb gaps (~5 skips, fully recoverable)

**Source:** `tests/e2e/_drivers/__init__.py::skips_if_unsupported` — a parametrized `[qs, app2]` test calls a verb only one renderer implements; the other's `NotImplementedError` becomes a skip.

Known gaps (grepped from `_drivers/qs.py` + `_drivers/app2.py`):
- QS metadata popup verbs (`open_metadata_panel`, `close_metadata_panel`, `metadata_panel_expand_all`, `metadata_panel_collapse_all`, `metadata_panel_text`, `metadata_panel_open_details_count`) — App2-only per operator lock 7. Only invoked by `tests/e2e/app2/test_cy_metadata_popup_app2.py` which is app2-tier (`app2/`), NOT under the qs_browser browser-marker matrix. **0 qs_browser skips** from this group (cross-checked: the app2 file's test is gated to App2Driver, not parametrized).
- QS `typeahead_filter` for non-empty query (line 453) — implemented as `NotImplementedError`. Fires from `test_cq_picker_search_and_find.py` when query is non-empty. ~4 skips × `[qs]` half of `[qs, app2]` param = ~4 skips.
- App2 `drill_from_first_row` raises NotImplementedError when the table declares no row-level drill (`<tr data-row-drill>`) — wire-shape consistency, not a real skip miss; the qs side of the parametrize covers it.

**Verdict: fully recoverable per `feedback_build_verbs_not_skip` ("build the missing renderer verb, don't skip the test param").** Implementing QS typeahead_filter (type into popover search input + re-read) per the existing comment is m-effort.

### Bucket E — Per-renderer skip on QS unavailability (~3 skips)

**Source:** `tests/e2e/qs_browser/test_inv_anomaly_qs.py::test_anomaly_qs_extract` + sibling `test_inv_money_trail_qs.py` + `test_audit_invariants_qs.py` when the isolated dashboard isn't deployed.

```python
if qs_driver is None:
    write_rendered_rows("qs_browser", "anomaly_qs_rows", [])
    write_rendered_rows("qs_browser", "anomaly_qs_meta", [{"qs_available": False, ...}])
    pytest.skip("QS unavailable — wrote sentinel; validator runs without the QS leg")
```

**Verdict: intentional degradation.** The validator runs the chain without the QS leg; the artifact + skip pair is the contract. Don't touch.

### Bucket F — Missing operator scaffolding (~3 skips)

**Source:** `tests/e2e/test_studio_deploy_browser.py` — three module-level `@pytest.mark.skipif`:
- `not docker_available()` — postgres-in-docker e2e
- `not SASQUATCH_YAML.exists()` — `sasquatch_pr.yaml` is gitignored operator config
- `not QUICKSIGHT_GEN_BIN.exists()` — need `recon-gen` installed in `.venv`

**Verdict: conditional.** On this machine `.venv/bin/recon-gen` exists, docker exists, `run/sasquatch_pr.yaml` likely exists. Most likely 0 of these skip on this machine; they fire on the WSL2 CI runner.

### Bucket H — `pytest.xfail` (2 xfailed, 2 xpassed)

- `test_l1_filters.py::test_date_range_filter_narrows_drift_sheet` — strict=False; Sasquatch L1 render flake (backlog #466). XPASS on app2 leg is OK (xfail strict=False tolerates).
- `test_l1_cross_sheet_drill_date_widening.py::test_pending_aging_drill_to_transactions_shows_target` — strict=False; QS DATA_POINT_MENU stale wiring; App2 leg xpasses.

Both should be investigated when the underlying flake/wiring issue is fixed; leaving xfail is the right posture per `feedback_no_xfail_to_sweep_under_rug` IF the root cause is documented (it is). Not action items for this triage.

## Recoverable tests (top buckets by leverage)

| # | Bucket | Action | Skips recovered | Effort |
|---|---|---|---|---|
| 1 | B — Delete deprecated `@pytest.mark.skip` files | Delete `test_inv_dashboard_agreement.py` + `test_audit_dashboard_agreement.py` bodies (CB.5 stage 2 follow-up) | 14 | xs |
| 2 | C — Plant Limit Breach rows in sasquatch_pr seed | Backlog #35 (BK.6) | ~6 | s |
| 3 | C — Plant multi-hop Money Trail edges | Per `feedback_spec_example_seed_thin_for_validation` | ~6 | s |
| 4 | C — Plant rail-targeted + chain-targeted L2 exception rows | seed-side fix | ~6 | m |
| 5 | C — Plant fanout rows above default sigma | seed-side fix | ~4 | s |
| 6 | D — Implement QS `typeahead_filter` for non-empty query | Per `feedback_build_verbs_not_skip` | ~4 | m |

**Top-of-list net effect:** ~40 of the 117 skips recoverable with bounded effort (1 xs delete + 4 seed-side plants + 1 QS verb impl).

## Genuine skips (intentional, do not touch)

- **Bucket A (~30 skips)** — per-cell dialect-cfg mismatch is the runner matrix's design; sibling cell handles the other dialect. Removing the parametrize would lose either the parity test or the per-cell isolation.
- **Bucket E (~3 skips)** — per-renderer degradation pattern; sentinel artifact + skip is the validator's contract.
- **Bucket F (~3 skips)** — operator scaffolding; correct behavior to skip when scaffolding absent.
- **Bucket H (4 xfail/xpass)** — backlog #466 + QS DATA_POINT_MENU stale wiring; documented root causes.

## Recommended next moves

1. **Land the CB.5 cleanup commit** — delete `test_inv_dashboard_agreement.py::test_invariant_three_way_agreement` + `test_audit_dashboard_agreement.py::test_invariant_four_way_agreement` (and unused fixtures). 14 skips disappear; one less "this is dead code" noise in the suite.
2. **Switch `cmd_up_to` from `-q` to `-rs` for one investigation cycle** so per-test skip reasons land in `stdout.log` (the current run's dot-output requires manual cross-reference). Cost: log size +~5KB.
3. **Pick one seed-side plant fix** — Limit Breach is the highest-leverage (BK.6 / Backlog #35 was already flagged before this triage). Once that lands, re-run + confirm L1 additive picker skips drop ~6.
4. **Implement QS `typeahead_filter` non-empty-query branch** — code comment already describes the pattern; ~m-effort and unblocks ~4 skips.
5. **Park the rest** (buckets A, E, F, H) as intentional. Re-check after each seed-side push: the skip count should monotonically drop, not creep back.

## Concerns

- **Counts are static-analysis derived, not parsed from a live `-rs` run.** A re-run with `pytest -rs` would tighten the buckets — particularly C (seed-data-shape skips) where the actual fire rate depends on `sasquatch_pr.yaml`'s plant density, which has shifted across recent BV/CB phases.
- **Bucket A (dialect-cfg mismatch) and bucket B (CB.5 deprecated) overlap on `test_audit_dashboard_agreement.py`** — the file's 12 deprecated skips include 6 oracle-cfg-mismatch skips that would skip anyway. Net delete: still 14 (the 12 from this file all skip, plus 2 from `test_inv_dashboard_agreement.py`).
- **`feedback_build_verbs_not_skip` posture** says build the verb on both renderers. Bucket D's QS `typeahead_filter` impl is the right move; `skips_if_unsupported` should shrink, not be the destination.
