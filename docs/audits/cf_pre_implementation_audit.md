# Phase CF — Pre-Implementation Audit

**Date:** 2026-06-04
**Source:** workflow `wrhj521xk` — 9 parallel investigations (CF.0 through CF.7 + cross-cutting infra). 1.35M tokens, 1222 tool calls, ~92 minutes wall.
**Cold-read inputs:** `docs/audits/v12_0_0_feedback.md` + `docs/audits/v13_1_1_feedback.md`

---

## TL;DR — Findings that reshape the plan

Five investigations turned up surprises that change what each CF task actually is:

1. **CF.0 is NOT a DuckDB regression — reviewer confirmed picker fragility.** `git diff ac855db8 HEAD -- src/recon_gen/common/spine/ src/recon_gen/common/l2/` is empty. The drift / limit_breach_outbound plant code is byte-identical to the v13.1.1 wheel the reviewer tested. **Reviewer follow-up at `docs/audits/v13_1_1_repro.md` (2026-06-04) confirmed the exact failure shape:** `ValueError: drift plant: no 2-leg Rail with destination matching the template role declared in this L2.` — raised before any SQL, dialect-independent. Root cause is `auto_scenario._pick_template` taking the alphabetically-first AccountTemplate role without checking it supports the requested plant; on the reviewer's L2 that role lacks an inbound 2-leg rail while three sibling roles DO support drift. `limit_breach_outbound` is a separate "this L2 declares no matching outbound LimitSchedule" gap on every role — picker fix won't help it; surface "this L2 cannot demo" pre-Apply instead. Two upstream-actionable fixes: (A) picker robustness — choose template that maximizes materializable plants OR fall back across templates; (B) surface per-kind failure reason in Studio danger banner (already captured in `trainer_failed_plants` kv).

2. **CF.1 is a 5-line fix.** Apply status contradicts because `_studio_training_v3.py:189-191` hard-codes `redirect_url='/training/?status=Apply+done.'`. Truth IS already persisted per-kind in `read_failed_kinds` / `read_applied_state` at `v_overlay.py:407-411`. Drop the unconditional redirect and read truth from the kv rows. The harder work is the *unified* banner copy + counter shape.

3. **CF.3 is a discoverability problem, not a missing feature.** Focus / neighborhood mode already exists (`topology.py::_focus_set` + `?focus=` URL state, shipped X.4.b 2026-05-13). Click-to-focus also works. The cold-read judges never landed in focus mode because the chrome doesn't foreground it. Also: the "self-loops off by default" demand is a category error — the per-rail renderer emits ZERO graphviz self-loops; the existing "Self-loops" checkbox toggles nothing visible. The real fix is hiding SingleLegRail nodes (which the judges visually misread as self-loops).

4. **CF.5 fix is one line.** The Templates Sankey is the only Sankey in the codebase without `items_limit`. Investigation's three Sankeys all cap at 50. The cyclic-edge banner already exists (BS.3 follow-up, 2026-05-30). The fix: add `items_limit=30`, change the Template dropdown default from "all" to first-declared, and re-frame the cycle banner copy on this sheet.

5. **CF.4 entity-count is unknown.** Cold-read measured 40,000px for one list, but sasquatch_pr has only ~7 rails. The reviewer ran against a much larger L2 (fuzz seed? customer L2?). Sub-1000 cards is client-side filterable; >1000 needs server-side pagination. Implementer must confirm the bracket before sizing.

The infrastructure-sweep findings (CF.X-infra) also matter: a shared `render_banner()` + `KPIValueThresholdBanding` primitive + `render_searchable_list()` helper would shrink CF.1 / CF.2 / CF.4 / CF.6 / CF.7 to consumers of shared infra rather than per-task re-implementations.

---

## Dependency graph

```
CF.0  (blocks everything — without working drift plant, can't re-verify CF.1/2/7 magnitude items)
  ↓
  ├──→ CF.1 (Apply status — needs working failing plant to exercise mixed-result branch)
  ├──→ CF.2 (Exec rollup — needs reliable exception signals from working plants)
  ├──→ CF.3 (L2 diagram — independent, but CF.0 takes priority)
  ├──→ CF.4 (Editor lists — independent, but CF.0 takes priority)
  ├──→ CF.5 (Templates Sankey — independent, but CF.0 takes priority; BLOCKS CF.8)
  ├──→ CF.6 (Empty states — independent, but CF.0 takes priority)
  ├──→ CF.7 (Minor sweep + U5/U11 re-probes — U5/U11 need working drift plant)
  └──→ CF.8 (v13.2.0 cut + re-cold-read)

CF.X-infra (shared banner + KPI banding + searchable-list)
  ├──→ CF.1 consumes render_apply_status_banner
  ├──→ CF.2 consumes KPIValueThresholdBanding
  ├──→ CF.4 consumes render_searchable_list
  ├──→ CF.6 consumes render_empty_state_card + Sheet.empty_state_when_unset
  └──→ CF.7 consumes render_banner (matview-staleness caption)
```

**Recommended sequencing:** CF.0 first (unblocker + discovery), then CF.X-infra subtasks land alongside their consumers (don't extract speculatively), then CF.1/CF.2/CF.4/CF.6 land bottom-up, with CF.3/CF.5/CF.7 in parallel where independent. CF.8 last.

---

## Per-task findings

### CF.0 — DuckDB plant regression (`drift` + `limit_breach_outbound` no-op)

**Effort:** M. **Blocks:** all other CF tasks. **Blocked by:** nothing.

**Current state.** `PLANT_REGISTRY` (`src/recon_gen/common/l2/plant_registry.py`) exposes 21 plant kinds; `_invoke_drift_plant` at line 381 calls `_pick_inbound_2leg_rail` + `_pick_external_counter_for_rail` then constructs `DriftPlant` → `DriftGenerator.emit` (`spine/drift.py:230`). `_invoke_limit_breach_outbound_plant` at line 515 calls `_pick_breach_inputs` + `_cap_breach_amount` → `LimitBreachGenerator.emit` (`spine/limit_breach.py:185`). `apply_plants` (`v_overlay.py:337`) wraps each plant call in try/except → records failures into `<base>_v_config_kv` under key `trainer_failed_plants`. Trainer landing renders danger banner at `_studio_training_v3.py:140-151`. Trainer dogfood tests walk every walkable kind across [du, pg, or].

**Gap.** The v13.1.1 reviewer reports "Apply reports '2 plant(s) failed: drift, limit_breach_outbound' ... observed on duckdb, suggesting a duckdb-path plant bug." But: zero src/ commits between `ac855db8 v13.1.1` and HEAD — the wheel and current main are identical. CI green on DuckDB sasquatch_pr at default density. The failing plants' picker functions (`_pick_external_counter_for_rail`, `_pick_breach_inputs`) raise `ValueError` when the L2 graph lacks the required topology; the chain plants don't depend on these pickers. **High likelihood the root cause is L2-shape, not DuckDB.**

**Recommended approach.**
1. Surface the actual exception text from `<base>_v_config_kv WHERE key='trainer_failed_plants'` — that determines which hypothesis branch we're in (L2-shape vs sequence-collision vs picker bug).
2. If the reviewer's L2 yaml is available, run `auto_scenario.default_scenario_for(load_instance(<reviewer-l2>))` locally and inspect `report.omitted`.
3. If L2 unavailable, add a fuzz regression: 20 seeds × DuckDB sweep of `random_l2_yaml(seed)` through trainer dogfood. Catches L2-shape footguns on synthetic shapes.
4. **UX fix regardless of root cause**: extend the trainer landing's danger banner to include first-line of each error message inline so future cold-reads capture exception text, not just kind names.
5. Verify DuckDB sequence-collision is NOT the bug via a unit test that clones populated base → v overlay then runs every L1-invariant plant; assert no PK violations.
6. If confirmed L2-shape: add `pre_apply_diagnosis` to plant cards that runs the picker dry and surfaces "this plant requires: X, Y — your L2 declares X but not Y" BEFORE Apply.

**Files touched.** `plant_registry.py`, `auto_scenario.py`, `v_overlay.py`, `plant_adapter.py`, `_studio_training_v3.py`, `test_bv33_trainer_dogfood.py`, `test_v_overlay_sequence_collision.py` (new).

**Key risks.** Without the reviewer's L2, we're guessing. Fuzz harness adds property-test infrastructure burden larger than the stated MED estimate.

**Open questions for the operator:**
- Is the reviewer's L2 yaml available? Did the cold-read run leave a DuckDB tempfile behind we can SELECT the error text out of?
- Done-when bar: "all 6 default-enabled plants pass on the reviewer's L2" or "all 21 plants pass on a fuzz-sweep of 50 random L2s"?
- If L2-shape, broaden the picker (silently pick a less-ideal counter) OR surface the requirement in UI BEFORE Apply?

---

### CF.1 — Apply status reflects per-plant truth

**Effort:** M. **Blocked by:** CF.0.

**Current state.** Three independent truth sources stack visually with no cross-reconciliation:
1. Client-side hardcoded `?status=Apply+done.` query param (set by the `training-apply-finished` JS handler at `_studio_training_v3.py:360` — no knowledge of the result dict at redirect time).
2. `trainer_failed_plants` kv row (read fresh on every landing render).
3. `trainer_applied_plants` kv row (drives `enabled_set` + checkbox state + "currently planted" pill).

`apply_plants` (`v_overlay.py:337-499`) ALREADY correctly populates both kv rows per-kind. The bug is purely in the render-decision logic.

**Gap.** Operator checks `drift`, hits Apply, drift's plant_function raises → page shows: green "✓ Apply done." + red "✗ 1 plant(s) failed: drift" + drift checkbox blank + red error-badge + missing "currently planted" pill. Five contradicting signals from three independent reads.

**Recommended approach.**
1. Change `apply_plants` to return a typed `ApplyResult(succeeded, failed, attempted_kinds, path)` instead of None.
2. Extend `_training_apply_state` to capture the post-run summary: `last_attempted`, `last_succeeded`, `last_failed_kinds`, `last_started_at`, `last_finished_at`.
3. Drop the hardcoded `redirect_url='/training/?status=Apply+done.'`. Replace with a no-op redirect that just reloads `/training/`. Post-Apply banner is now derived entirely from kv rows + in-memory summary.
4. New unified banner `render_apply_status_banner(succeeded, failed)`: green when failed=0, amber when partial, red when zero succeeded. Single `data-test-training-apply-result-banner`.
5. Keep `session_status` for Session Start + Cleanup paths (where there's no per-row outcome).
6. Fix the global header counter: show BOTH applied AND attempted (`4 applied / 1 failed of 22 total`).

**Surprising finding.** Silent no-op plants (return None / empty SQL) currently claim success in the ledger but do nothing. `_FAILED_STATE_KEY` only captures *exceptional* failures. CF.0 should consider whether silent-no-op plants need a third "no_effect" bucket.

**Open questions for the operator:**
- Color for partial-failure: amber or red? (Recommend amber — operator's selection mostly applied.)
- Should the unified banner persist across navigations within /training/* or clear on first non-Apply interaction?
- On Studio restart (in-memory summary lost, kv rows survive): render the banner from kv-only or skip until next Apply?

---

### CF.2 — Exec app program-health rollup tile

**Effort:** M. **Blocked by:** CF.0.

**Current state.** Executives app ships 4 content sheets (Getting Started / Account Coverage / Transaction Volume / Money Moved) + App Info. Zero escalation-signal datasets. The unified L1 exceptions matview already exists (`<prefix>_l1_exceptions`, schema.py:2737-2856) — UNIONs all 10 invariant kinds with `check_type` discriminator + `magnitude_amount`/`magnitude_count` split. L1 dashboard already wraps it.

Threshold-banding primitives are **binary only**: `KPIValueZeroIndicator` + `KPIValueSignIndicator` emit 2-state ConditionalFormattingOptions, and `_KPI_INDICATOR_AGG_FN` rejects `count` aggregation (visuals.py:270-275). Drill primitives are CROSS-SHEET only — **cross-app drill is structurally impossible in QuickSight** per K.4.7 (memory: `project_qs_url_parameter_no_control_sync`).

**Gap.** Cold-read demands (1) rolled-up open-exception count, (2) breach/drift/overdraft signals, (3) THREE-STATE threshold banding, (4) deep-link drill. The codebase provides the data + binary indicators + same-analysis drill. Missing: per-app Exec dataset reading `<prefix>_l1_exceptions` (each app owns its own datasets), three-state `KPIValueThresholdBands` primitive, `count` aggregation acceptance, and cross-app drill substitute.

**Recommended approach.**
1. Add `DS_EXEC_HEALTH_ROLLUP` / `DS_EXEC_HEALTH_BY_TYPE` / `DS_EXEC_HEALTH_DETAIL` reading `<prefix>_l1_exceptions`.
2. Add new `KPIValueThresholdBands(amber_at, red_at, healthy_when_low=True)` primitive — emits 3 ConditionalFormattingOptions with non-overlapping AND-clauses. Use CHECKMARK/EXCLAMATION_MARK/X icons (with 2-band fallback if QS rejects EXCLAMATION_MARK at deploy time).
3. Extend `_KPI_INDICATOR_AGG_FN` to accept `count`.
4. Mirror in App2 via `shape_kpi`'s `threshold_bands` kwarg → `state_icon='✓'/'⚠'/'✗'` + `state_color='success'/'warning'/'danger'`.
5. New `SHEET_EXEC_PROGRAM_HEALTH` between Getting Started and Account Coverage.
6. **Cross-app drill substitute**: replicate L1 Exception detail table inside Exec analysis (since real cross-app drill is impossible). Sheet description carries prose URL pointer.
7. Default thresholds: amber=1, red=20 (operator-tunable v0, defer L2-instance configurability to follow-up).

**Surprising findings.** Two reshape the task: (a) cross-app drill literally cannot be a clickable navigation in QS — the substitute must be in-sheet detail table; (b) extending `_KPI_INDICATOR_AGG_FN` to accept `count` is a primitive-level change CF.2 ends up driving across all three indicators.

**Open questions for the operator:**
- Substitute for cross-app drill: in-sheet detail table replica, sheet-description URL pointer, or both?
- Default thresholds for "Open Exceptions" (amber=1 / red=20 v0)?
- Should the count obey the Exec date picker (30-day window) or use a fixed "last business day" frame?

---

### CF.3 — L2 diagram readability

**Effort:** M. **Blocked by:** nothing (only CF.0 priority).

**Current state.** L2 diagram already ships substantial focus/neighborhood infra: `topology.py::build_topology_graph_per_rail` accepts `focus_node_id=`, `_focus_set` (line 615) computes "direct neighbors + complete rail", `diagram.js::_wireFocus` (line 353-383) does click-to-navigate-to-`?focus=<id>` with Esc to clear. Three-step layer chrome lets the user step up from L1 (roles + structure) to L3 (+chains & templates — the "hairball"). Chrome has nine show/hide checkboxes including "Self-loops (N)".

**Gap.** Cold-read demands entity-focus mode, self-loops off by default, explain grey supernode aggregates, tame the hairball. Actual gaps:
- Focus mode EXISTS but is undiscoverable — only entry path is "click a node in the hairball." Judges never landed in focus.
- "Self-loops" checkbox is a **category error**: the per-rail renderer emits ZERO graphviz self-loops. The CSS only affects edge labels on a non-existent edge kind. Cold-read's "~half edges are self-loops" is a visual mis-read of dense SingleLegRail node clusters.
- "Show: Templates" off only hides the inner badge, not the dashed `<g class="cluster">` boundary (CSS gap at `diagram-svg.css:44`).
- Grey bundle ellipses + dashed template clusters carry no tooltip / no legend / no explanation.
- L3 layer link has no scale warning.

**Recommended approach.**
1. Add a discoverable "Focus on…" `<select>` chrome control populated from the typed projection, grouped via `<optgroup>` by node kind. Navigates to `?focus={value}` — promotes focus from click-to-discover to top-level navigation.
2. Split SingleLegRail from TwoLegRail rendering: add `data-rail-kind="singleleg"|"twoleg"` attr; new chrome row "Single-leg rails (N)" defaults UNCHECKED. CSS rule `.hide-rail-singleleg g.node[data-rail-kind="singleleg"]`. This addresses "self-loops off by default" against the actual renderer.
3. Drop/repurpose the misleading "Self-loops" chrome checkbox.
4. Add explanatory `tooltip=` attributes to bundle nodes + template clusters (graphviz emits as SVG `<title>` for native hover).
5. Collapsed `<details>` Legend block above the canvas explaining each shape/color.
6. Post-process `<g class="cluster">` in `diagram.js` to truly hide template-cluster boundaries when toggled off.
7. Node-count badge on the L3 layer link.

**Surprising finding.** The "focus / neighborhood mode" the cold-read suggests was ALREADY built in X.4.b polish — just lacks discoverability beyond click-a-node-in-the-hairball.

**Open questions for the operator:**
- Selector with `<optgroup>` (simple, 90+ flat options) or typeahead (more JS)?
- SingleLegRail nodes: hide via CSS or server-side re-emit? Recommend CSS.
- Default layer L1 (clean, minimal) or L2 (rails — the connectivity story)?

---

### CF.4 — Editor entity-list search/sort/filter

**Effort:** M. **Blocked by:** CF.0 (priority only).

**Current state.** `_render_list_page` (`_studio_editor_routes.py:3709-3775`) renders every entity as a full read card via `_render_read_card` (1825-1934). Rail has ~18 fields per card; transfer_template ~10. Cards stuffed into 1/2/3-column grid. Six list-capable kinds. Zero search/sort/filter/pagination infrastructure exists. Cards already carry stable `data-kind`/`data-entity-id`/`data-focus-node` attributes.

**Gap.** Demands "search/sort + collapse-by-default" for a 40,000px scroll wall.

**Recommended approach.**
1. `_render_list_toolbar(kind, count)` helper: search `<input>`, sort `<select>` (Default / A-Z / Z-A; composite kinds get "By parent"), Collapse/Expand toggle.
2. Wrap `<dl>` in `<details>` (collapse-by-default for rail + transfer_template via `_COLLAPSE_BY_DEFAULT_KINDS` frozenset).
3. `data-search-text` attribute on each `<article>` (lowercase concatenation of entity_id + all `<dd>` text) for client-side substring match.
4. Inline `<script>` scoped to `[data-list-root]`: wire search input + sort select + collapse buttons.
5. JS-only filter (no server round-trip) since entity counts bound by L2, not transactions.

**Surprising finding.** Cold-read measured "40,000px tall" but sasquatch_pr has ~7 rails + ~3 templates. Implementer needs to confirm the reviewer's L2 entity-count bracket before sizing.

**Open questions for the operator:**
- What L2 was the cold-read at? sasquatch_pr or larger (customer/fuzz)?
- Carry the toolbar in the home-page embed (`?embed=1`) too, or only on the dedicated page?
- Per-card collapse-by-default for rail + transfer_template only, or universal?

---

### CF.5 — Transfer-Templates Sankey legibility

**Effort:** M. **Blocked by:** CF.0 (priority only). **Blocks:** CF.8.

**Current state.** L2FT Templates sheet renders a Sankey via `sheet.layout.row(...).add_sankey(...)` at `apps/l2_flow_tracing/app.py:1054`. Filter bar has 6 controls, all defaulting to "match all". The cyclic-edge stripping function `_stripSankeyCycles` (`bootstrap.js:1210-1252`) + the cycle banner already exist (added BS.3 follow-up 2026-05-30). Banner reads: "Note: N cyclic edges hidden so this Sankey can render. The L2 declares closed-loop flows here — use Money Trail or Account Network for the full directed-graph view."

**CRITICAL FINDING:** `add_sankey(...)` at line 1054 does NOT pass `items_limit` — **the Templates Sankey is the ONLY Sankey in the codebase without an items_limit**. Investigation's three Sankeys all carry `_SANKEY_NODE_CAP=50`. Single-line omission, not missing capability.

**Gap.** Cold-read flags "admits 11 cyclic edges" + "filtering / highlight-on-hover, or different layout above N flows." Today: (a) uncapped Sankey; (b) "match all" default fans across all templates; (c) cycle banner reads as defect-warning ("use Money Trail or Account Network") not as workable view; (d) no hover-highlight.

**Recommended approach.**
1. Pick `_SANKEY_NODE_CAP=30` (tighter than Investigation's 50 because of orphan-suffix node multiplier).
2. Pass `items_limit=_SANKEY_NODE_CAP` to `add_sankey(...)` at app.py:1054 — single-line change.
3. Change `pL2ftTtTemplate` default from `L2FT_ALL_SENTINEL` to `declared_template_names(l2_instance)[0]` (first declared). Wire via new optional `default_value` parameter on `_populate_pushdown_dropdown`.
4. Update Sankey subtitle: "Showing one template at a time — pick a different Template above to swap, or clear the dropdown to overlay all declared templates (cycles in closed-loop topologies will collapse)."
5. Sheet-specific banner copy via `data-sankey-cycle-hint='pick-template'` attribute → `renderSankey` swaps to "Closed-loop topology detected. Pick a single Template above to see its flow isolated; use Money Trail / Account Network for cross-template directed-graph views."
6. Add `items_limit` plumbing to App2's `shape_sankey` — parity gate requires both renderers cap identically.

**Surprising finding.** Cycle handling is fully built. Cold-read's "admits 11 cyclic edges" is a complaint that the banner copy reads as defect confession, not workable view. Fix is partly UX-copy + partly default-narrowing — NOT new cycle infra.

**Open questions for the operator:**
- Default Template: first declared (deterministic, ordering-dependent) or highest-volume in window (data-driven, extra dataset)?
- Cap N = 30 OK or different value?
- Does cap+default sufficiently address, or also need small-multiples? Recommend cap+default in CF.5; if v13.2.0 cold-read still flags, file CG follow-up.

---

### CF.6 — Empty-state prompts on picker-driven sheets

**Effort:** M. **Blocked by:** CF.0 (priority only).

**Current state.** Three sheets land blank: Daily Statement (`P_L1_DS_ACCOUNT` default = sentinel), Money Trail (`P_INV_MONEY_TRAIL_ROOT` default = sentinel), Account Network (`P_INV_ANETWORK_ANCHOR` default = sentinel). App2 has per-visual empty-state copy on every renderer (added BO.3/BQ.1/BQ.2) — generic "No rows match the current filters. Try widening the date range or clearing the dropdown filters above." Sheet descriptions on Money Trail + Account Network already say "Pick a chain root from the dropdown..." but those render only on App2; QS's `SheetDefinition.Description` is metadata-only.

**Gap.** Two distinct gaps:
- **Gap A — wrong copy.** Existing "widen the date range" misleads when nothing exists to widen. User who reads it tries the date picker, finds no relief, concludes broken.
- **Gap B — QS still bare on canvas.** Per-visual App2 banners don't reach QS. Sheet description is invisible on QS canvas.

**Recommended approach.**
1. Add a top-of-sheet `TextBox` on each of the three affected sheets via `sheet.layout.row(...).add_text_box(...)`. Renders identically on QS (as `SheetTextBox`) and App2 (as grid slot). Closes Gap B.
2. Add `Sheet.empty_state_hint: str | None = None` field on `common/tree/structure.py::Sheet`. Tree-level single source of truth.
3. Wire App2 server-side: `render.py::_render_sheet` reads `sheet.empty_state_hint`, stamps `data-empty-state-hint` attribute on each visual's `<section>`. `bootstrap.js` per-kind renderers read the attr and use it as the empty-state body when present. Closes Gap A on App2.
4. Add unit test walking each app tree, identifying sheets with sentinel-default picker params, asserting `empty_state_hint` is set AND a top-row TextBox carries the same string. Encodes the invariant at construction time.

**Surprising finding.** Existing in-flight per-visual empty-state work (BO.3 / BQ.1 / BQ.2) HURTS the picker-sentinel case — it replaces the blank canvas with copy that's actively misleading. The in-flight pattern needs picker-state awareness OR a sheet-level prompt that overrides it.

**Open questions for the operator:**
- Should the TextBox auto-hide once a non-sentinel picker value is committed, or stay always-visible? Recommend always-visible.
- Daily Statement: "Select an account to begin" enough, or mention role/date too? Recommend terse.
- Enforce `Sheet.empty_state_hint → top-row TextBox` invariant in `__post_init__` or stay loose with a unit test? Recommend loose for now.

---

### CF.7 — Minor sweep + v12 U5/U11 re-probes

**Effort:** M. **Blocked by:** CF.0. **Blocks:** CF.8.

Five sub-items:

**(a) ETL "5-step" ↔ "Three steps" copy mismatch.** `_studio_routes.py:909` reads "Three steps" subhead over 3 cards; `<summary>` at `:793` reads "Show the 5-step checklist" over 5-item tutorial. Test `test_studio_etl_landing.py:236` asserts `body.count('<li class="...">') == 5`. Fix: change `:909` to "Three core surfaces" + rename `<summary>` to "Show the full ETL workflow checklist (5 steps)".

**(b) App Info matview-staleness + business-day caption.** Add `latest_date_basis` literal column to `_matview_status_sql` (label per spec: `business_day_end` / `business_day_start` / `posting`). Extend subtitle: "Matviews keyed on `business_day_end` (drift / ledger_drift / overdraft) intentionally read one calendar day past the source — the rollup boundary, not stale data. Stale = source `posting` MAX is >24h ahead of the matview's date."

**(c) Net Money tolerance band.** Smallest delta — extend KPI subtitle at `apps/executives/app.py:574` with derived band rule: "Tolerance band: |net| ≤ 1% of gross_amount = green (in-range); 1–5% = amber (review); >5% = red (investigate)." ALSO add sibling "Net / Gross %" KPI reading `100.0 * SUM(net_amount) / SUM(gross_amount)` with `KPIValueZeroIndicator`. Don't add new visuals.py primitive — CF.7 is sweep scope.

**(d) U5 plant magnitude re-probe (BLOCKED BY CF.0).** Per-row magnitude IS labeled per-row in L1 Exceptions detail table. Drift Timelines KPIs are `MAX(SUM(abs_drift))` per (day, role) — already titled "Largest Leaf Drift Day (peak business day)" and Y-axis says "Σ |drift|". Likely v12 mis-read of day-rollup. Plant `delta_money='75.00'` post-CF.0, verify Exceptions detail row reads exactly $75.00, verify Drift "Largest Leaf Drift" KPI reads $75.00, verify Drift Timelines KPI reads ≥$75.00.

**(e) U11 drift→leg drill re-probe (BLOCKED BY CF.0).** Detail table currently shows ALL legs (including Failed) with no running balance, no per-status subtotal. Accountant can't trace from table alone. Fix shape options: (i) "Posting Status" badge coloring Failed rows red + a "Σ Excluded" subtotal KPI tile (cheapest); (ii) split into side-by-side "Posted Legs" + "Reconciling Items" tables with subtotals (operator-clearest but may need new tree primitives); (iii) running balance SQL column.

**Surprising findings.** v12 U13's Probe `01/01/1900` sentinel is already fixed. v12 U13's "No row match the current filter" grammar bug needs `rg` to confirm in source — if found, fold into sweep; if QS native chrome, file AWS-side note.

**Open questions for the operator:**
- U11 fix shape: status-badge + subtotal (cheapest) or split-table (clearest)?
- Net Money tolerance threshold (1% / 5%): industry-standard fiat or derive from L2 yaml `expected_net_tolerance_pct`?
- App Info `latest_date_basis`: column-name literal or operator-friendly label ("end-of-day")?

---

### CF.X-infra — Cross-cutting infrastructure

**Effort:** L. **Blocked by:** CF.0. **Blocks:** CF.1/CF.2/CF.4/CF.6/CF.7.

Five shared primitives multiple CF tasks would benefit from:

1. **Banner system** (CF.1 + CF.7) — `common/html/_components.py::render_banner(level, text, *, test_attr)` + `render_apply_status_banner(succeeded, failed)`. CF.1's compound "N succeeded, M failed" semantic doesn't exist today; each banner site re-implements the Tailwind classes.

2. **KPI threshold/banding** (CF.2) — `KPIValueThresholdBanding` typed primitive. Today's `KPIValueZeroIndicator` + `KPIValueSignIndicator` are BINARY only; `_KPI_INDICATOR_AGG_FN` rejects `count`. CF.2 needs 3-state amber/red bands.

3. **Empty-state for picker-driven sheets** (CF.6) — `Sheet.empty_state_when_unset: ParameterName | None` field + `render_empty_state_card(title, body, test_attr)`. Today's per-visual `empty: True` sentinel isn't picker-aware.

4. **List-with-search** (CF.4) — extract `_studio_training_v3.py:269-288`'s "Show: + JS filter + #bv-empty-state" pattern into `render_searchable_list(items, key_fn, label_fn, group_fn=None)`. Reuse in `_render_list_page`.

5. **Diagram focus + Sankey** (CF.3 + CF.5) — focus already works; CF.3's actual gap is discoverability + supernode legend. CF.5's gap is items_limit + cycle-banner copy.

**Recommended sequencing.** Don't extract speculatively. Land each shared primitive alongside its first consumer; subsequent consumers refactor to use it. Avoids over-design.

**Open questions for the operator:**
- New `common/html/_components.py` module vs add to `render.py`? Recommend new module.
- CF.4 client-side JS filter (works to N≈500) vs htmx server-search (works to ∞)? Decide from CF.4's L2 entity-count answer.
- CF.7 tolerance band: KPI dataclass field (typed) vs subtitle copy (mutable)? Typed aligns with `[feedback_invariants_in_types]` but copy may suffice.

---

## Cross-cutting open questions for the operator

The investigation surfaced several decisions the implementer can't make alone:

1. **CF.0 reviewer L2 access** — is the v13.1.1 cold-read reviewer's L2 yaml available? Without it, CF.0 is guesswork.
2. **CF.0 done-when bar** — reviewer's L2 only, or all-21-plants × fuzz-50-L2s sweep?
3. **CF.2 cross-app drill substitute** — in-sheet detail table replica, prose URL pointer, or both?
4. **CF.2 default thresholds** — amber=1 / red=20 v0, or operator-tuned from the start?
5. **CF.4 reviewer L2 entity count** — sub-1000 (client-side filterable) or thousands (need pagination)?
6. **CF.5 default template choice** — first declared (deterministic) or highest-volume in window (data-driven)?
7. **CF.5 Sankey cap N** — 30 (proposed) or different?
8. **CF.7 U11 fix shape** — status-badge + subtotal (cheap) or split-table (clearest)?
9. **CF.X-infra extraction policy** — extract speculatively before consumers land, or alongside first consumer? Recommend alongside.

---

## Effort estimate summary

| Task | Effort | Notes |
|---|:---:|---|
| CF.0 | M | M assumes L2-shape root cause; could be S if reviewer's L2 is available + bug surfaces immediately, or L if fuzz harness lands |
| CF.1 | M | Core fix is 5 lines; the unified banner + counter shape is the work |
| CF.2 | M | Includes new typed primitive (`KPIValueThresholdBanding`) + indicator agg-guard extension |
| CF.3 | M | Mostly chrome additions + CSS rule extensions; focus mode already exists |
| CF.4 | M | Sized for client-side filter (sub-1000 cards). Goes to L if entity-count answer pushes to server-side pagination |
| CF.5 | S–M | Single-line items_limit + dropdown default + cycle banner copy re-frame; M if items_limit App2 parity work is also in-scope |
| CF.6 | M | Three sheets × (TextBox + Sheet field + render plumbing) |
| CF.7 | M | Five sub-items, biggest is U11 drift→leg drill |
| CF.X-infra | L | Spread across CF.1/2/4/6/7 consumers; don't time-box independently |
| CF.8 | S | v13.2.0 cut + 6-persona re-cold-read |

**Phase total:** ~3-4 weeks of focused work if CF.0 root cause is the reviewer's L2 (the lightweight case), longer if it's a real DuckDB-path bug requiring spine-level changes.

---

## Source

`/private/tmp/claude-501/-Users-chotchki-workspace-quicksight/f8aeb512-2a8a-4e68-8be8-93045efb5be2/tasks/wrhj521xk.output` — full JSON findings from 9 parallel investigations.
