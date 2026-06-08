# CK.0 — Accessibility pass baseline (2026-06-05)

Baseline a11y findings against the live Studio (commit `4f816789`,
DuckDB + `sasquatch_pr`). Probe at `scripts/ck_a11y_probe.py`
(static regex over rendered HTML — covers the structural findings
the v13.1.1 axe-core run flagged; does NOT replace a real axe-core
gate, which is CK.6 pending operator OK).

## Scope vs charter

The PLAN CK phase lists 7 sub-cells. This audit decides which land
in the autonomous overnight branch and which wait for operator
sign-off:

| Cell | Plan | Decision |
|---|---|---|
| CK.0 | Replan + tracking artifact + axe-core gate decision | **This doc** |
| CK.1 | Landmarks + `<h1>` sweep | **Autonomous** — mechanical, objective |
| CK.2 | Training `nested-interactive` fix | **Defer** — needs visual inspection of the specific button-in-button shape |
| CK.3 | Entity-list `aria-allowed-role` sweep (~140 nodes) | **Autonomous** — likely one root cause |
| CK.4 | Picker `aria-label` sweep | **Autonomous** — `kind_label_*` helpers ready |
| CK.5 | Bounding-box overlap sweep | **Defer** — needs rendered geometry, not static |
| CK.6 | axe-core CI gate | **Operator sign-off** — adds a hard build dep |
| CK.7 | Cold-read v3 confirmation | **Final verification** — after the above land |

## Baseline static probe (commit `4f816789`, pre-CK.1)

Re-run after each cell:

```
.venv/bin/python scripts/ck_a11y_probe.py
```

### Missing `<main>` landmark (6 pages)

- `/` (home)
- `/diagram`
- `/etl/` (landing)
- `/etl/probe`
- `/etl/run`
- `/etl/triage`

### Missing `<h1>` (4 pages)

- `/diagram`
- `/etl/probe`
- `/etl/run`
- `/etl/triage`

### `<select>` without `aria-label` (3 pages, 5 unique selects)

- `/training/` — `bv-show-filter` (the trainer's show-filter
  picker, ~6 options for "all / planted / unplanted / …").
- `/l2_shape/account/new` + `/l2_shape/account/<id>/edit` —
  `field-scope` (internal/external) + `field-parent_role` (the
  CG.22 grouped optgroup).

### Surfaces that pass the static probe

- `/data` — 1 main, 1 h1.
- All 6 `/l2_shape/<list-kind>/` pages — 1 main, 1 h1 each (CG.6
  trainer-style header).
- `/l2_shape/theme/` — singleton form: 1 main, 1 h1.
- `/l2_shape/rail/new` — rail subtype picker: 1 main, 1 h1.
- `/l2_shape/persona/` — CG.20 unknown-kind chrome: 1 main, 1 h1
  (4xx body, probe captures via HTTPError fallback).

## CK.1 — landmarks + h1 sweep — work list

Add `<main>` + ensure exactly one `<h1>` on each of the 6 pages
above. The `<h1>` is already implied by the trainer-style header
strip pattern used elsewhere (CG.6 / CG.20); the work is wrapping
the body content in `<main>` and naming the missing h1s.

- `/` (`_studio_routes.py::_studio_l2_shape_root_page`): already
  has h1 ("L2 Editor"); wrap `<section id="home-entities">` +
  surrounding content in `<main>`.
- `/diagram` (`_studio_routes.py::_render_diagram_page`): adds h1
  *and* `<main>`. CG.10 documented the no-header-strip exemption
  for vertical-budget reasons — the fix here uses a
  `class="sr-only"` h1 so screen readers get the landmark without
  stealing canvas pixels.
- `/etl/` + sub-pages: each has a header-strip h1 ALREADY in some
  but not all (probe says all 3 sub-pages are missing it).
  Re-check per page; add where missing; wrap body in `<main>`.

Pattern (locked):

```html
<header class="px-8 py-4 border-b border-surface-border bg-white">
  <h1 class="text-xl font-semibold m-0">...</h1>
</header>
<main class="...">
  <!-- page body -->
</main>
```

## CK.3 — `aria-allowed-role` root cause hypothesis

The v13.1.1 audit reported ~140 nodes flagged on entity lists. The
list view's card-grid is rendered by `_render_list_page` +
`_render_read_card`. Static inspection of the rendered HTML for
`/l2_shape/rail/` shows the card uses `<article id="entity-..."
data-entity-id="..."` with no explicit `role=` — those should pass
`aria-allowed-role` by default. The 140-node finding likely traces
to the per-section `<details data-kind="...">` accordion on `/`
(home page) — those are list-kind sections × multiple cards each
× the CF.4-era card layout. Verify by running the audit's axe-core
config when CK.6 lands; for the autonomous pass I'll touch the
known-issue spots (any `role="..."` attribute in the entity-list
render path) but won't blind-fix without an axe-core reproducer.

## CK.4 — picker aria-label — work list

Add `aria-label` to 5 selects:

1. `bv-show-filter` in trainer landing — label: "Show plant kinds:".
2. `field-scope` in account create/edit — label: "Account scope".
3. `field-parent_role` in account create/edit — label: "Account
   parent role" (the optgroups already carry their own labels via
   CG.22; this is the wrapper select's own label).

For `field-*` selects, the existing `<label for="field-...">`
pattern provides implicit labelling for sighted operators, but
explicit `aria-label` matches the v13.1.1 audit's `select-name`
finding. The Single `select-name` axe finding probably is on
`bv-show-filter` (no label at all).

## Deferred to operator review

- **CK.2** Training `nested-interactive`: need to find the
  button-inside-clickable-card pattern. Likely the planted-badge
  hover affordances on plant cards.
- **CK.5** Bounding-box overlaps: 67 reported, 1 cleared by CF
  audit followup, ~66 remain. Many likely auto-clear from CG list
  primitive + CH table primitive landing. Re-probe after CK.1/3/4
  + CH ships.
- **CK.6** axe-core CI gate: adds an npm-ish build dep (axe-core
  is a JS package). Per operator's Rust-tool preference, would
  spike alternatives (e.g. axe-core via Python's selenium binding,
  or wave-cli) before locking the CI dep choice.
- **CK.7** Cold-read v3: queued for after CK.1/3/4 land.

## Sign-off contract for the autonomous branch

The overnight pass on `cg-autonomous-CK` will land CK.1, CK.3, CK.4
with unit tests pinning each fix + extend the probe to assert "no
new findings." CK.0 doc updates AS the probe re-runs clean.
CK.2/5/6/7 stay open in the phase plan.
