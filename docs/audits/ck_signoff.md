# CK phase — autonomous overnight sign-off

Branch: `cg-autonomous-CK`. Base: `4f816789` (main pre-branch).
Commits 3 → `cg-autonomous-CK` HEAD.

## Shipped

- **CK.0** (`e009f40f`) — `scripts/ck_a11y_probe.py` static
  probe + `docs/audits/ck_0_a11y_baseline.md` tracking artifact.
  19 routes crawled; baseline reported 9 pages with findings.
- **CK.1** (`53305f93`) — landmarks + h1 sweep. `<main>` added
  to 6 pages (home, diagram, ETL landing, 3 ETL sub-pages);
  `<h1>` added to 4 pages (diagram via sr-only per CG.10
  vertical-budget exemption; 3 ETL sub-pages via the standard
  trainer-style header strip). 31 unit tests pin exactly-one
  `<main>` + exactly-one `<h1>` across 17 surfaces.
- **CK.4** (`53875f1e`) — every `<select>` ships an aria-label.
  `_render_field` for kind="select" sources aria-label from
  `FieldSpec.label` so the visible label and the accessible
  name stay coupled by construction. Trainer's bv-show-filter
  and 5 rail-reconciler sub-form selects got hand-written
  aria-labels. 19 unit tests pin no-unlabeled-`<select>` across
  all 17 surfaces.

**Static probe post-CK.1/.4: 0 pages with findings across all 19
Studio routes.**

## Deferred + flagged for operator review

- **CK.2 — training `nested-interactive`** — needs visual repro
  to identify the specific button-inside-clickable shape; the
  trainer landing has cards that toggle plant kinds and could
  carry inner buttons that would trigger the axe rule, but
  there's no static regex that catches the misuse safely. File
  remains open.

- **CK.3 — entity-list `aria-allowed-role` (~140 nodes)** —
  static grep over `_studio_editor_routes.py` shows no
  `role="..."` misuse on entity-list cards (`role="alert"` on
  error blocks + `role="group"` on multi_select wrappers are
  both legitimate). The 140-node finding likely traces to an
  axe-specific check against the live DOM (possibly the
  `<details>` accordion pattern + htmx attribute interactions
  on cards). Needs an axe-core repro before fixing — blind
  hand-edits could regress correct semantics. File remains
  open.

- **CK.5 — bounding-box overlap sweep (67 → 66 remaining)** —
  needs rendered geometry; static regex can't surface these.
  Many likely auto-clear post-CG list primitive + CH table
  primitive. Re-probe needs a browser harness; deferred until
  axe-core toolchain lands.

- **CK.6 — axe-core CI gate** — adds a JS/npm build dep. Per
  `[[feedback_rust_influenced_tool_preferences]]`, prefer
  Rust-binary alternatives where possible. Suggest spike: try
  `axe-core` via Python's `selenium`/`playwright` (already in
  the test stack for browser-tier e2e), or evaluate `wave-cli`
  (Rust) as the CI gate. Operator OK required either way.

- **CK.7 — cold-read v3 a11y confirmation** — manual screen-
  reader spot-check + axe-core green. Both gated on CK.2/3/5/6
  closing.

## Verification done

- `.venv/bin/pyright src/recon_gen/common/html/_studio_routes.py
  src/recon_gen/common/html/_studio_editor_routes.py
  src/recon_gen/common/html/_studio_training_v3.py` — 0 errors.
- `.venv/bin/pytest tests/unit -k "studio or editor or cf3 or
  cf4 or cg or etl or ck" -p no:cacheprovider -q` — 1057
  passed, 4 skipped, 2352 deselected.
- `.venv/bin/python scripts/ck_a11y_probe.py` — `Pages with
  findings: 0/19`.

## Verification NOT done (flagged for morning)

- Layered runner (`./run_tests.sh up_to=db`) — branch only, no
  push, but a layered local sweep would catch any DuckDB-tier
  fallout I might have missed. The CK changes are pure HTML
  templating in studio handlers (no DB-tier code touched), so
  the unit-tier sweep should be sufficient, but a layered run
  is the canonical pre-merge gate per
  `[[feedback_local_api_layer_before_push.md]]` (deferred here
  because the CI box is off).
- Live browser dogfood — none. The probe + unit tests pin the
  markup contract; a screen-reader spot-check is still
  necessary to confirm the sr-only h1 on `/diagram` actually
  reaches NVDA / JAWS / VoiceOver via the announcement chain.

## Recommended next steps for morning review

1. Read this doc + `docs/audits/ck_0_a11y_baseline.md` for
   context.
2. Run `./run_tests.sh up_to=db` (or `up_to=api` if AWS creds
   handy) on this branch — sanity gate I couldn't run with CI
   box off.
3. Merge to main via fast-forward — no rebase needed, branch
   was created off `4f816789` and the only `main` advance since
   would be CG-bug commits which are already in this branch's
   base. If branch is behind, sync first.
4. Optionally invoke a quick browser-tier check on the affected
   surfaces (`/`, `/diagram`, `/etl/run`, `/training/`,
   `/l2_shape/account/new`) — none of the visible chrome
   changed except the new ETL h1 + blurb strips.
5. Continue CK.2/3/5/6/7 when axe-core toolchain lands.

## Self-assessment

Risk profile: low. All edits are markup-only (no logic changes;
no validator changes; no SQL or render-pipeline changes). The
two new helpers (`_htmx_head_block` from CG-bug.2 — unchanged on
this branch; `_render_field` aria-label sourcing) consume
existing FieldSpec.label values that already drive the visible
labels, so accessible-name divergence is structurally
impossible. Tests cover the contract end-to-end.

Confidence: high that nothing broke; medium that the v13.1.1
audit's full a11y picture closes (CK.2/3/5 deferred). The
shipped pieces address the audit's #7 highest-leverage systemic
fix (landmarks + h1s) cleanly.

Going to CH next — separate branch `cg-autonomous-CH`.
