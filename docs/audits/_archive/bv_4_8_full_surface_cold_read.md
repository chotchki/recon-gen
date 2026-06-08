# BV.4.8 — Full-surface cold-read of the dual-prefix Trainer

> **Scope.** Read BV.4.0 → BV.4.6 end-to-end against `bv_5_dual_prefix_spike.md`
> (v5 LOCKED, 15 design locks). Four screenshots in `/tmp/bv4_full_coldread/`
> (`01_landing_with_status.png`, `02_filter_only_enabled.png`,
> `03_after_top_select_all.png`, `04_after_family_chip_l2_triage.png`) +
> `src/recon_gen/common/html/_studio_training_v3.py` +
> `src/recon_gen/common/l2/v_overlay.py` + the Studio route handlers in
> `src/recon_gen/common/html/_studio_routes.py` + the dashboard server in
> `src/recon_gen/common/html/server.py` +
> `src/recon_gen/common/html/_tree_fetcher.py`.

---

## Headline verdict

**SHIP-WITH-FIXES → P1.1 + P1.3 RESOLVED 2026-05-31** — see
"Post-cold-read landing" at the bottom. P1.2 still needs a live WebKit
poke to confirm (screenshot-evidence ambiguous). The original verdict
called out one core correctness bug + two UX shapes; the correctness
bug + the dead-state empty-filter shape are now fixed, gated, and
live-verified end-to-end. The rest of the surface is in good shape:
25 cards × 8 families lay out cleanly, bulk-toggles + density badges
behave correctly on the Select All path, the staleness / failure
banners are well-shaped, and the Session Start orchestration matches
DL.10 exactly.

The headline P1 is **DL.7 is half-shipped**: the Tour links *carry* the
right `?prefix=` URL fragment, and there's even an anti-drift test that
asserts the link href is correct
(`tests/unit/test_bv4_training_v3.py::test_tour_links_carry_prefix_query_param`),
but **no `/dashboards/<app>/sheets/<sheet>` route handler reads
`request.query_params.get("prefix")`** — so the dashboard data fetcher
silently falls back to the cfg-bound prefix on every request and Clean /
Violation render identical data. The operator's Tour comparison teaches
nothing because the renderer can't distinguish the two URLs.

---

## §1 What's strong

- **Landing-page shape matches DL.8 exactly.** Cards carry the four
  inputs the lock mandates: (1) title + kind qualifier + monospace
  registry id; (2) enable/disable checkbox; (3) inline tunable form
  fields (`form_<kind>_<primitive>` collision-safe naming); (4)
  Clean / Violation Tour links + "What to do about it" disclosure.
  `01_landing_with_status.png` shows L1 Conservation open by default
  with all three cards rendering the full primitive set (days_ago +
  drift_amount on the first two; days_ago + stored_balance on
  Non-negative balance). Per-family `[all]` / `[none]` chips +
  per-family density badge sit on the summary line; top-level
  `[Select all]` / `[None]` + `0/25 plants enabled` + `Show:` filter
  sit on the parent toolbar. The 8 families render in
  `_FAMILY_ORDER` (`_studio_training_v3.py:32-41`) which matches the
  spike §0.5 matrix.

- **Bulk-toggle math is correct on the Select-all path.**
  `03_after_top_select_all.png` confirms top badge → `25/25`, every
  family badge updates to `(3/3 enabled)` / `(2/2 enabled)` /
  `(7/7 enabled)` / `(4/4 enabled)` correctly, and the cards that
  ARE rendered (L1 Conservation) all show checked checkboxes. The
  density tally code in `_BV_LANDING_JS::updateDensity` walks the
  full checkbox set + per-family bodies, which is what makes this
  work regardless of which families are expanded.

- **Session controls follow DL.10 exactly.**
  `_render_session_controls` (`_studio_training_v3.py:218-268`) emits
  three buttons post-Session-Start: `▶ Session Start (re-fetch)`
  (full /etl/run + clone), `↻ Re-clone from base` (skip /etl/run),
  `🗑 Cleanup` (drop v overlay). Each carries a `title=` tooltip
  spelling out the lifecycle cost. Pre-Session-Start renders only
  Session Start. The `disabled` attribute on the Apply button +
  "Click Session Start first" hint when no v overlay exists matches
  DL.11's "v overlay must exist before plants apply" invariant.

- **Session Start orchestration is correct.**
  `v_overlay.session_start` (`v_overlay.py:114-233`) chains
  `run_deploy_pipeline(... overlays=TRAINER_CLEAN)` (which writes
  the base prefix's fresh data) → drop-then-create v schema → clone
  data tables → refresh v matviews → wipe the
  `trainer_applied_plants` / `trainer_failed_plants` rows in
  `<v>_config_kv`. The `refresh_base=False` path (Re-clone) skips
  step 1 — exactly the DL.10 split. The "wipe stale applied
  state" step at lines 209-225 is the bit that protects DL.11's
  "zero plants enabled after Session Start" claim against a
  previous-session-with-applied-plants state.

- **Failed-plant + staleness banners render to spec.** The banner
  block (`_studio_training_v3.py:110-140`) accumulates success +
  warning + failure banners in priority order (success at top,
  failure at bottom). The failure banner truncates at 5 kinds with a
  `+N more` overflow + tells the operator to hover the card's badge
  for the underlying message. Colors are `bg-success/10` /
  `bg-warning/10` / `bg-danger/10` with matching `text-*` chrome —
  matches the Tailwind theme tokens the rest of Studio uses. The
  failed-plant card carries `bg-danger/5` tint + a `data-error="1"`
  attribute the `Show: Only with errors` filter keys off.
  L2-staleness banner (DL.14) reads `<v>_config_kv`'s
  `trainer_l2_yaml_mtime` row + diffs against current
  `os.path.getmtime` with a 1s tolerance (`_studio_routes.py:4267-4275`)
  — the tolerance is the right move for same-second-rewrite cases.

- **Diff-only Apply state ledger lives on the right axis.**
  `applied_state_write_sql` / `read_applied_state` persist
  `{kind: {form_field: value}}` on `<v>_config_kv` keyed by
  `__bv__` / `trainer_applied_plants`. DELETE+INSERT shape works on
  PG / Oracle / sqlite without ON CONFLICT. That's the substrate
  DL.9 diff-only Apply will consume when BV.4.4-promotion lands —
  the schema's already correct even though the consumer side is
  still naive clone-and-replay.

- **L2FT Hygiene tour-URL** confirmed live at
  `plant_registry.py:1974-1979` →
  `/dashboards/l2_flow_tracing/sheets/l2ft-sheet-l2-exceptions`
  (per cold-read note from the brief).

---

## §2 P1 — fix before operator declares BV.4 done

### P1.1 — `?prefix=` URL param is NOT consumed by dashboard routes

**Severity.** Core Tour comparison promise (DL.6, DL.7) fails to
deliver. Operator clicks Violation, sees the same data as Clean,
concludes "the dashboard doesn't surface this violation" — but the
violation IS planted in `<base>_v_*`; it just never made it to the
SQL because the prefix bound at `build_all_datasets(cfg)` time is
the cfg-prefix, not the URL param.

**Evidence.**
- `_studio_training_v3.py:351-358` — Tour links emit
  `href="...?prefix={base_prefix}"` (Clean) and
  `href="...?prefix={v_prefix}"` (Violation). The escape + concat
  is correct.
- `tests/unit/test_bv4_training_v3.py::test_tour_links_carry_prefix_query_param`
  asserts the link HREFs are correct. Passes — and is sufficient
  for the LINK side.
- `common/html/server.py:442-484` (`dashboard_view`) and `:486-529`
  (`sheet_view`) — neither reads `request.query_params.get("prefix")`.
  Both go straight to `served.data_fetcher` which is built once at
  startup by `make_tree_db_fetcher(tree_app, cfg, pool=pool)` in
  `cli/_html_serve.py:151`.
- `common/html/_tree_fetcher.py` — the fetcher resolves
  `get_sql(ds_id)` from the registry; the SQL is baked at
  `build_all_datasets(cfg)` time using `cfg.db_table_prefix`. No
  prefix kwarg flows through `make_tree_db_fetcher` →
  `execute_visual_sql_async`. There is NO runtime prefix substitution
  hook.
- The only place a `?prefix=` URL param IS consumed today is
  `/etl/triage` (`_studio_routes.py:4144-4161`), which calls
  `_render_etl_triage_page(..., prefix_override=effective_prefix)`.
  That's the BV.4.2 vertical slice mentioned in the comment; it
  never spread to the dashboard mount.

**Fix shape.** Two viable routes:

1. **Per-request fetcher rebuild** — `sheet_view` reads
   `request.query_params.get("prefix")`; if present and != the
   default, build a one-shot fetcher targeting the alt prefix
   (re-running `build_all_datasets` with a fresh prefix, populating
   a per-request SQL registry). Heavyweight per request; the
   right shape is to register the prefix-bound dataset registry
   once per known prefix and look it up.

2. **Push prefix into the executor** — `get_sql(ds_id)` returns SQL
   with a placeholder like `<<$prefix>>`; the SQL executor
   substitutes at fetch time. Smaller diff, but requires every
   dataset SQL to be rewritten + the executor to learn prefix
   substitution.

Either way, the **dashboard / sheet / visual_data routes need to
read `?prefix=` AND thread it through to the fetcher**. Add the
counterpart anti-drift test:

```python
def test_dashboard_route_consumes_prefix_query_param() -> None:
    """The Tour links promise prefix-bound rendering. If the dashboard
    route silently drops the URL param, Clean / Violation render
    identical data and the comparison teaches nothing."""
    # GET /dashboards/.../sheets/...?prefix=<v_prefix>
    # → assert the SQL that fires against the pool references <v_prefix>_transactions
```

The bug is fixable without invalidating the BV.4 architecture — the
prefix is a runtime parameter that needs one more pipe. But it MUST
land before the operator "Tours" the comparison, because the wrong
shape silently misleads.

### P1.2 — Family-chip toggle may not update density badge (screenshot 04 evidence is ambiguous)

**Severity.** If real, breaks "the chips do what they say" UX promise.
If a screenshot artifact, no-op.

**Evidence.**
- `04_after_family_chip_l2_triage.png` is captioned "after clicking
  the L2 Triage gaps family's `[all]` chip". The expected post-state:
  L2 Triage badge → `(3/3 enabled)`, top badge → `3/25 plants
  enabled`. The screenshot shows top badge → `0/25` and L2 Triage
  badge → `(0/3 enabled)`. L2 Triage + L2 Coverage rows do show a
  subtle background tint (possibly hover state from the click).
- The JS in `_BV_LANDING_JS::_bvToggleFamily` (`_studio_training_v3.py:499-504`)
  selects `[data-family-body="${familyId}"]`, ticks the checkboxes
  inside, calls `updateDensity()`. The family-id passed in is the
  `family.replace(" ", "_")` form ("L2_Triage_gaps"), which matches
  the `data-family-body` attribute the body div emits
  (`_studio_training_v3.py:318`). Selectors look right.
- One latent concern: `<details>` accordions in collapsed state DO
  keep their children in the DOM (per HTML spec — `display:none` is
  the closed shape, not detachment), so the `querySelector` should
  find them. But this is dialect-of-browser-dependent — worth a
  live poke against WebKit (the e2e harness's browser) AND Chrome
  (a typical operator browser) to confirm.

**Fix shape.** Live-repro against WebKit + Chrome + Safari:
1. Land on `/training/`, run Session Start.
2. Without expanding any family, click L2 Triage `[all]`.
3. Inspect: are the 3 underlying checkboxes ticked
   (`document.querySelectorAll('[data-family-body="L2_Triage_gaps"] input:checked')` → 3)?
4. Is the badge text `(3/3 enabled)`?

If the checkboxes ARE checked but the badge text says 0/3 → the bug
is in `updateDensity()`'s badge selector. If neither updates → the
selector for `[data-family-body=...]` is broken. If both are correct
in live testing → screenshot 04 was captured pre-click-fire (the
operator clicked, screenshotted before JS settled) and this is a
non-bug.

The fact that screenshot 03 (top Select All) DOES update both top
and per-family badges correctly is reassuring — it means the
density-recompute machinery works in at least one path. So this
is more likely a `_bvToggleFamily`-specific issue OR a screenshot-
timing artifact than a fundamental `updateDensity` defect.

### P1.3 — `Show: Only enabled` with zero enabled plants is a confusing dead state

**Severity.** First-time operator hits this exactly: lands → flips
filter to "Only enabled" before enabling anything → sees a blank
page with just the Apply button. No copy explains why.

**Evidence.** `02_filter_only_enabled.png` shows the post-filter
state: top-level `0/25 plants enabled` toolbar + the sticky Apply
button alone. Every family is `display: none` because
`_bvApplyFilter('enabled')` hides families whose cards have no
checked checkbox (`_studio_training_v3.py:519-520`). No empty-state
message renders.

**Fix shape.** In `_bvApplyFilter`, when no families are shown
(track `anyFamilyShown` across the loop), inject an empty-state
hint above `#bv-families`:

> "No plants enabled — flip Show: All or click [Select all] to start
> a session."

Or simpler: lock the `Show:` select to "All" until at least one
plant is enabled; switch the select to "Only enabled" automatically
post-Apply (a nice progressive disclosure).

---

## §3 P2 — next polish cycle

- **DL.13 Info-sheet prefix mirror is not wired.**
  `common/sheets/app_info.py:334,389` displays
  `prefix: {cfg.deployment_name}` — that's the cfg-time deployment
  name, NOT the runtime `?prefix=` value. DL.13's "the operator
  can always confirm which dataset they're hitting" guarantee
  only lights up post-P1.1 (once `?prefix=` actually steers the
  fetcher); at that point the Info sheet should read the actual
  active prefix off the request. Track as polish — has zero teaching
  cost while P1.1 is unfixed (today the displayed prefix matches
  the actual prefix because there IS only one).

- **L2 Triage gaps family display order vs operator expectation.**
  `_FAMILY_ORDER` puts L1 families first (the four-level L1 ladder),
  then L2 Triage gaps, L2 Coverage gaps, L2FT Hygiene last. That
  matches the spike §0.5 matrix exactly. Worth confirming with
  the operator that this matches their teaching flow (L1 first =
  "this is what's broken at the totals level," then walk down to
  L2 fidelity = "this is what we measure at the per-rail level").
  Non-blocking; mention it next standup.

- **Re-clone button copy / icon vs Session Start.** Both use a green
  `bg-accent` button with a similar weight. The tooltip
  differentiates them well, but at-a-glance two greens of the same
  shape look like the same action. Consider giving Re-clone a
  lighter visual weight (outline button?) — Session Start is the
  load-bearing 10-minute action; Re-clone is the fast reset. Visual
  hierarchy should reflect that.

- **L2 stale banner copy is dense.** "Your L2 yaml has changed since
  this Session Start (2026-05-31T14:23:17). Click Session Start
  (re-fetch) to pick up the new schema + reseed the base +
  re-clone the v overlay." Three concepts in one sentence
  (new schema / reseed base / re-clone v). Consider splitting:

  > "Your L2 yaml changed since Session Start (HH:MM)."
  > "Re-run Session Start to pick up the new schema."

  Two lines, each one concept.

- **First-sentence trimming on card subtitles.** `_first_sentence` at
  line 450 picks the first sentence by splitting on ". ". For
  one-sentence short-statements that end without a period (some
  registry entries don't) the rule appends a period. Cards with
  multi-sentence statements get truncated — operator may miss
  context. Worth either showing the full statement OR adding a
  `[…]` ellipsis when truncated so the operator knows there's more
  in "What to do about it."

- **`Show: Only with errors`** is the wrong shape pre-Apply. Before
  any Apply has fired, `failed_kinds` is empty, so flipping to
  "Only with errors" yields the same blank-page UX as P1.3.
  Same fix.

---

## §3.5 P3 — backlog

- **Status banner is success-only colored.** The session-status
  banner unconditionally renders `bg-success/10` (green). Any
  status message — `Cleanup done`, `Re-cloned from base`, `Applied
  5 plant(s).` — comes in green. That's fine for the happy path,
  but if /training/apply ever 500s the operator currently sees no
  banner at all (the redirect carries no `?status=` for failure).
  Consider adding error-status branch in the redirect on Apply
  failure + branching the banner color.

- **Sticky Apply button at z-10 vs accordion summaries.** The
  Apply bar uses `sticky bottom-0 ... z-10`. With many families
  expanded, scrolling DOES keep Apply visible. Fine. But long
  cards on small screens may push Apply on top of a card's
  Violation link. Consider a small `mb-4` on the cards container
  OR `pb-20` on `<main>` so the last card's links are always
  fully readable.

- **Anti-drift gap: no test asserts staleness banner appears when
  L2 yaml mtime drifts.** The render-side test
  (`test_renders_l2_stale_banner_when_l2_stale_flag_set`) only checks
  that passing `l2_stale=True` to the render function emits the
  banner; it doesn't exercise the route handler's
  `current_mtime - stored_mtime > 1.0` comparison in
  `_studio_routes.py:4267-4275`. A small integration test would
  catch a regression in the 1-second tolerance / the mtime read.

- **`PlantCategory` + `Iterable` imports are dead-code-suppressed at
  module bottom** (`_studio_training_v3.py:463-464`). Once BV.4.5's
  per-category error-band lands they go live; until then the
  underscore-discards work but are a smell. No-op now; flag for the
  BV.4.5 final pass.

- **Form-value persistence across Apply.** When the operator types
  `count=7` on the `phantom_rail` card, hits Apply, the v overlay
  stores `{phantom_rail: {count: "7"}}`. On the next render the form
  value reads back from `applied[kind]` and pre-fills the input.
  Good. But unmade-apply edits ARE NOT persisted — if the operator
  edits `count=3→5`, navigates away to `/etl/run`, then comes back,
  the input shows the registry default `3` again. Minor papercut;
  fix by attaching a `localStorage` write on field-change OR a
  background `/training/draft-state` POST. Backlog.

---

## §4 Deferred-by-design confirmation

- **BV.4.3 streaming progress page** — Session Start blocks for
  ~10s on sqlite / ~20s on PG-post-/etl/run. No progress page. The
  green "Session started — v overlay ready" banner lands on
  redirect-back. Consistent with the brief's "deferred / polish
  only" carve-out. NOT FLAGGED.

- **DL.9 diff-only Apply** — `apply_plants` re-clones base → v then
  replays every enabled plant (`v_overlay.py:296-356`). The
  `<v>_config_kv` state ledger IS populated correctly so a future
  DL.9 conversion has the right substrate. Brief explicitly carved
  this out. NOT FLAGGED.

- **BV.4.7 multi-dialect** — covered transitively by BV.3.1 per
  the brief. NOT FLAGGED. (Worth a separate cold-read pass against
  Oracle + PG once BV.4.7 promotes, but that's a different audit.)

- **BV.6 incremental matview refresh** — parallel phase; out of
  scope for this cold-read. NOT FLAGGED.

---

## Recommended landing

P1.1 (dashboard route doesn't read `?prefix=`) is the only finding
that breaks the operator's teaching session — fix it before the
operator declares BV.4 done. P1.2 needs a live poke to confirm
real-vs-screenshot-timing. P1.3 is cheap to ship alongside P1.1.

P2 / P3 are polish; queue against a BV.4.9 / BV.5 polish phase.
The architecture is sound; the BV.4.0 vertical-slice's ARCHITECTURE-
PASS holds at full scale.

---

## Post-cold-read landing (2026-05-31, autonomous BV.4 finish)

### P1.1 — FIXED (`?prefix=` consumed end-to-end)

Two-prong fix landed:
- `common/html/server.py::dashboard_view` + `sheet_view` read
  `request.query_params.get("prefix")` and pass through to
  `emit_html(prefix_override=...)`.
- `common/html/render.py::_render_filter_form` accepts the kwarg
  and emits a hidden `<input type="hidden" name="prefix" value="..">`
  at the top of `#filter-form` so every
  `hx-include="#filter-form"` visual fetch serializes it.
- `common/html/_tree_fetcher.py::make_tree_db_fetcher` reads
  `params["prefix"]` in the fetcher closure; if alt_prefix !=
  cfg.db_table_prefix, substitutes `f"{base}_"` → `f"{alt}_"` in
  the pre-resolved SQL string at fetch time so every table /
  matview reference re-targets together.

Anti-drift test added at
`tests/unit/test_html_tree_fetcher.py::test_make_tree_db_fetcher_retargets_to_alt_prefix`
(seeds two SQLite tables `test_t` + `test_v_t`, registers SQL
against `test_t`, asserts no-prefix-URL ⇒ base rows, `?prefix=test_v`
⇒ v-overlay rows, `?prefix=test` echo ⇒ base no-op).

End-to-end live-verified against `qsgen_sqlite` Studio: inserted
100 synthetic rows into `qsgen_sqlite_v_overdraft` only, then hit
the L1 Overdraft Violations Table visual with both prefixes:
- base ⇒ `total_rows=401` (matches direct DB)
- `?prefix=qsgen_sqlite_v` ⇒ `total_rows=501` (matches +100 delta)
- `?prefix=ZZ_DOESNT_EXIST` ⇒ SQL retargets at non-existent table,
  request 500s (proves the SQL was actually re-targeted, not a
  no-op).

Note on string-replace fragility: substitution operates on the
trailing-underscore token `f"{base}_"` so prefix-substring collisions
("recon" vs "recon-test") don't mis-fire. A string-literal containing
the prefix would still get corrupted — e.g. `WHERE col = 'recon-test_active'`
in a dataset SQL would be rewritten. Production datasets don't
have such literals today; flag P3 if one ever appears.

### P1.3 — FIXED (empty-state hint for zero-match filter)

`_studio_training_v3.py::render_training_v3_landing` now emits a
`#bv-empty-state` block (CSS-hidden by default) inside `#bv-families`.
`_bvApplyFilter` JS tracks an `anyFamilyShown` flag across the per-
family pass; when no family shows, removes `.hidden` from the
empty-state to reveal the actionable copy ("No plants match this
filter. Switch the **Show:** selector back to *All*, or click
**[Select all]** on a family below to start a teaching session").
Anti-drift test pinned at
`test_renders_empty_state_for_zero_match_filter`.

### P1.2 — RESOLVED (operator-confirmed non-bug)

Operator live-poked 2026-05-31 — family-chip density badge DOES
update on click. Screenshot 04 was a timing artifact (captured
before the JS settled). No code change needed.

### P1.4 — FIXED (card text rendered as raw markdown source)

Operator-spotted post-merge: trainer → L1 Audit → Supersession Audit
card was rendering its `short_statement` with literal backticks +
`**asterisks**` instead of `<code>` / `<strong>`. Root: `_render_card`
in `_studio_training_v3.py:400,407` was piping `section.short_statement`
+ `section.what_to_do` straight through `escape()`, which preserves
the markdown source as displayed text. Fix: route both through the
existing XSS-safe `_render_inline_markdown` helper (escapes-then-
markdown). `what_to_do`'s wrapper switched `<p>` → `<div>` so
multi-paragraph copy works without nesting `<p>` tags.

Anti-drift test pinned at
`test_card_short_statement_renders_markdown` — keys on `<strong>not</strong>`
+ `<code>_supersession_*</code>` against the supersession card.

### P2 / P3 — queued

Per the cold-read recommendation; tracked as backlog (BTb / BV
polish cycle). Includes DL.13 Info-sheet prefix mirror (only lights
up post-P1.1; useful follow-on), card subtitle ellipsis on
truncated `_first_sentence`, sticky-Apply z-index polish, and the
form-value localStorage scratchpad.

### Live ship gates (operator pre-review)

- 14 anti-drift unit tests on the v3 landing (BV.4.6) — all green.
- 1 anti-drift unit test on the prefix-routing pipe (BV.4.8.P1.1) — green.
- 1 anti-drift unit test on the empty-state filter (BV.4.8.P1.3) — green.
- Full unit suite green except `limit_breach_inbound` plant (pre-existing
  BV.3.2 backlog item; not caused by these changes).
- Live end-to-end prefix substitution proven against Studio +
  qsgen_sqlite v overlay with a synthetic-row probe.

The architecture is sound; the BV.4.0 vertical-slice's ARCHITECTURE-
PASS holds at full scale. Operator review of the v_overlay diff-only
Apply (DL.9, deferred), streaming progress (BV.4.3, deferred), and
the screen-recording of P1.2's live behavior is the remaining gate
before BV.4 ⇒ phase exit.
