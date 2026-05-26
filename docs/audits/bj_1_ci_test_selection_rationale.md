# BJ.1 — Why do `e2e.yml` + `release.yml` hand-list test files?

**Answer**: historical artifact. The workflows USED to hand-list because
that was the convention; a 2025-spring migration to runner-dispatch
wrappers (the same convention `ci.yml` integration jobs use) was
attempted and REVERTED when shared local/CI AWS infrastructure made
the cutover too risky. The unblocker (ephemeral AWS infra via
`Y.2.gate.l`) has since shipped, so the migration can resume.

## Timeline (commits in order)

1. **`8a7ea104` (v8.8.0a16, Y.2.gate.k.1.thin-wrapper)** — migrated
   `e2e.yml`'s 3 AWS-touching jobs to runner-dispatch thin wrappers,
   mirroring `ci.yml`'s integration jobs. Intent: ONE invocation path
   (the runner) used both locally and in CI. Eliminates the
   hand-list-drift bug class (BG.7 surfaced this exact class —
   `test_l1_account_filters.py` + `test_l2ft_exceptions.py` missing
   from both workflows).

2. **`9d9f46f3`** — hotfix: the runner-driven jobs pulled in the
   runner's full unit layer which needed `--extra serve` for
   aiosqlite + starlette deps. Added to all 3 e2e jobs.

3. **`f3a824da` (revert of 8a7ea10 + 9d9f46f)** — reverted because:
   - Runner-driven jobs surfaced more dependency gaps that needed
     iteration against AWS, but local cluster + CI cluster were the
     SAME resources (`feedback_ephemeral_aws_infra`'s "Split CI-AWS
     from local-AWS" hadn't shipped yet)
   - "Both lifecycles stepped on each other" (v8.8.0a16's
     `e2e-pg-api` Aurora-paused failure)
   - **Revert note**: "Resume after gate.l: cherry-pick those commits
     + apply the playwright-install + serve-extras fixes discovered
     today."

4. **`8d60f722` through `b632b4e7` (Y.2.gate.l.* — shipped between
   v8.8.0a17 and v8.8.0a18)** — the ephemeral AWS infra revert was
   waiting on. Includes `aws_rds_running` probe, RdsStatus enum
   widening, resilient RDS bring-up, per-release-disjoint L2.

5. **`5d72af23` (X.2.u.6)** — reconciled the hand-list with the
   parametrized suite (added test_l2ft_dropdowns + test_dashboard_driver
   to e2e.yml). Surfaced the missing-file pattern at the time.

6. **`2b484e8b` (BG.7, 2026-05-25)** — caught the same drift class
   again: 4 files missing from BOTH workflows (test_l1_account_filters,
   test_l1_additive_pickers, test_l2ft_additive_pickers,
   test_l2ft_exceptions). Fixed by adding the missing files to BOTH
   workflows. Flagged the maintenance pattern itself as the underlying
   bug → Phase BJ.

## What CI does today (post-BG.7)

Both `e2e.yml::e2e-pg-browser` and `release.yml::e2e-against-testpypi`
hand-list 17 test files each:

```yaml
.venv/bin/pytest \
  tests/e2e/test_l1_dashboard_renders.py \
  tests/e2e/test_l1_sheet_visuals.py \
  tests/e2e/test_l1_filters.py \
  tests/e2e/test_l1_account_filters.py \      # BG.7 add
  tests/e2e/test_l1_additive_pickers.py \     # BG.7 add
  tests/e2e/test_inv_dashboard_renders.py \
  tests/e2e/test_inv_sheet_visuals.py \
  tests/e2e/test_inv_filters.py \
  tests/e2e/test_inv_drilldown.py \
  tests/e2e/test_exec_dashboard_renders.py \
  tests/e2e/test_exec_sheet_visuals.py \
  tests/e2e/test_l2ft_rails_dropdowns.py \
  tests/e2e/test_l2ft_chains_dropdowns.py \
  tests/e2e/test_l2ft_templates_dropdowns.py \
  tests/e2e/test_l2ft_additive_pickers.py \   # BG.7 add
  tests/e2e/test_l2ft_exceptions.py \         # BG.7 add
  tests/e2e/test_dashboard_driver.py \
  tests/e2e/test_l1_cross_sheet_drill_date_widening.py \
  tests/e2e/test_l2ft_metadata_cascade.py \
  -m browser -v -n 2 \
  --cov=recon_gen
```

The `-m browser` is the marker. Per `_dev/runner.py:783`, the local
runner uses `pytest tests/e2e/ -m browser` (auto-collects every
browser-marked file). So the file list is RECONCILABLE — every entry
in the hand-list IS a browser-marker file, and removing the hand-list
in favor of `tests/e2e/` selection would give the same set (plus the
`test_audit_dashboard_agreement.py` carve-out, which has its own job).

## Why not (b) extract-to-composite-action?

Per `feedback_ci_release_workflow_parity` the requirement is:
"ci.yml and release.yml Tests jobs must stay in parity." A composite
action satisfies the letter (one source of truth for the file list)
but the spirit is "the bug class shouldn't be reachable." Marker-
selection makes the bug class structurally unreachable — adding a new
browser-marked test file auto-includes it without any workflow edit.
A composite action would still require editing the central list when
a file is added.

## Why not (c) AST-lint enforcement?

The AST-lint approach (assert every browser-marked file appears in
BOTH workflow YAMLs) would CATCH the drift but doesn't FIX the root
cause — operators still have to maintain two file lists. Marker-
selection drops the lists entirely. AST-lint stays in scope as a
safety net IF the hand-list pattern is kept for some other reason.

## Decision for BJ.2: option (a) — marker-selection

Both workflows collapse to:

```yaml
.venv/bin/pytest tests/e2e/ \
  -m browser \
  --ignore=tests/e2e/test_audit_dashboard_agreement.py \
  -v -n 2 --cov=recon_gen
```

The `--ignore` carves out the 4-way agreement test that already runs
in its dedicated step (re-seeds spec_example schema; can't share a
worker pool with the rest).

## Resume path

The original migration (`8a7ea104`) attempted a richer change: full
`./run_tests.sh up_to=browser` runner-dispatch. That brings per-cell
artifacts, timings, hash-locked drift detection — but at the cost of
the runner's full machinery + the unit-layer-prerequisite chain. The
BJ.2 fix takes the SIMPLER path (bare marker-pytest) because:

- It directly closes the BG.7 bug class (no hand-list to drift from)
- It doesn't pull in the unit-layer / aiosqlite / serve-extras shape
  that originally caused the migration revert
- The runner can still be the local-dev iteration path; CI just
  uses the equivalent marker-pytest invocation

The richer runner-dispatch migration stays as a future option once
the team has appetite for the full runner-in-CI shape (per-cell
artifacts, etc.).
