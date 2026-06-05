# CF.3.f.b — adversarial-review follow-ups (deferred from v0.1)

Workflow `wd9l6lvvj` (3 parallel lenses: correctness, regressions, UX/accessibility) flagged 21 findings against CF.3.f v0.1. The MUST-FIX cluster shipped in v0.1 (the same commit as this file). This file is the punch list for v0.2 / CF.3.f.b.

## Already shipped in v0.1 (post-review)

| # | Lens | Sev | Fix |
|---|------|-----|-----|
| 1 | A — Correctness | HIGH | `transfer_key` no longer rendered as Python tuple repr — `, `.join instead of `str(tuple)`. Verified: header now reads `merchant_id, settlement_period` not `('merchant_id', 'settlement_period')`. |
| 2 | A + B + C | CRITICAL | JS `_stripCompass` helper strips trailing `:e/:w/:n/:s` port-compass suffix before prefix matching. `_idFromTitle` + `_kindFromTitle` now work on port-anchored edges. |
| 3 | B + C | HIGH | JS `_edgeKind` recognizes role↔template-port edges as `rail_bundle` (not `chain`); the dead `template_member` arm is gone (CF.3.f deletes those edges). Coverage CSS + edge-kind toggles now classify correctly. |
| 4 | B — Regressions | MEDIUM | `rails_in_clusters` populated only for IN-FOCUS rails. Composite template's leg rows are focus-filtered too. Matches pre-CF.3.f focus behavior. |

## Deferred — CF.3.f.b backlog

### CRITICAL / HIGH

| # | Lens | Sev | Where | What |
|---|------|-----|-------|------|
| 5 | B | CRITICAL | `coverage.py:163-165`, `trainer.py:118-148`, `diagram.js:435-451`, `:522-550` | Template-resident rails lose their `g.node[data-id="rail__<name>"]` element. Coverage + trainer overlays can never tint them. Fix: stamp `<TD>` data attributes server-side and walk composite child polygons in JS, OR re-emit invisible standalone rail nodes for tint-only purposes. |
| 6 | C | HIGH | `topology.py:193-208` (palette) | Rail-edge palette fails WCAG SC 1.4.11 (3:1 non-text contrast) on multiple fill backgrounds (Credit teal #1b9e77 → 2.31:1 / 2.71:1 / 2.74:1; Debit orange → 2.57:1; Variable purple → 2.42:1 / 2.81:1). Fix: bump edge stroke `penwidth` to 3pt+ OR switch to IBM color-blind-safe categorical palette. |
| 7 | C | HIGH | `diagram.js:435-451` + `topology.py:435-454` | Coverage CSS `> polygon` selector re-fills every cell of a template composite, including header — destroys composite design. Fix: narrow selector to `g.node:not([data-id^="tmpl__"]) > polygon` and add separate composite-specific overlay rules. |

### MEDIUM

| # | Lens | Where | What |
|---|------|-------|------|
| 8 | A / B / C | `topology.py:403-411` (`_leg_port`) | Rail-name sanitization is non-injective: `R:1` and `R 1` collide on `leg_R_1`. No current fixture trips it; add a loud-fail or hex-encode for safety. |
| 9 | A | `topology.py:1300-1317` + focus-set adjacency | Chain endpoint focus check uses `rail__R` for template-resident rails; if owning template is not in focus, dot synthesizes phantom nodes from dangling port refs. Fix: skip port-suffixed edges when owning tmpl is out-of-focus. |
| 10 | A | `topology.py:1105-1108` | A rail in two TransferTemplates routes through whichever was iterated last. Add a validator rejecting this OR change `rail_to_template` to `dict[Identifier, list[Identifier]]` with per-template edge emit. |
| 11 | C | `topology.py:194-195` (XOR fill) | `_TEMPLATE_XOR_FILL=#ffe1c2` vs `_TEMPLATE_LEG_FILL=#fff2e0` have ΔE=10.8 — borderline indistinguishable. Pick higher-saturation XOR fill or add a left-border indicator. |
| 12 | C | `topology.py:1319-1362` | XOR matched-edge styling promised by spec (`penwidth` per XOR group) is unimplemented. The shape-side encoding exists; edge-side encoding doesn't. |
| 13 | C | `topology.py:436-447` (`_rail_leg_marker`) | TwoLegRail leg-row marker is always `↔` — no direction signal. Pick a per-leg glyph derived from source/dest roles. |
| 14 | C | `topology.py:429-433` (`_rail_node_attrs`) | Aggregating signal (cylinder+peripheries=2) wins over leg-count signal — aggregating SingleLeg and aggregating TwoLeg render identically. Compose orthogonally. |
| 15 | C | `topology.py:1202-1235` (Phase E) | Spec's "Multi-leg-template rail edge — label kept" promise unimplemented. Add `label="debit"/"credit"/"var"` on edges into template ports. |

### LOW / NIT

| # | Where | What |
|---|-------|------|
| 16 | `topology.py:1164-1175` (Phase D bundle) | Bundle shape ignores `aggregating` flag — diverges from `_rail_node_attrs`. Aggregating rails are typically anchored so don't bundle; fix by adding aggregating rails to `anchored_rails` set. |
| 17 | `topology.py:1219-1235` + `:1271-1291` (Phase E + bundle SingleLeg) | Variable-direction SingleLegRail always emits `leg_role→rail` with `arrowhead=normal` — silhouette lies vs the purple color's "variable" semantic. Fix: `arrowhead="normalonormal"` or `dir="both"` for Variable. |
| 18 | `topology.py:382-395` (`_template_inner_label` / `_template_cluster_label`) | Dead code retained — typed-graph metadata still carries `cluster_label`. Either delete + drop the metadata key (with d3/JSON consumer update) or rename to `_template_header_label`. |
| 19 | `tests/unit/test_l2_topology_typed.py:355-389` | New CF.3.f test only asserts XOR fill + cluster absence. Add assertions for: port-id format `leg_<rail>`, chain edges docking `tmpl:leg_R:e/w`, Phase E rail edges routing via ports. |
| 20 | `diagram.js:366-373` (`_wireFocus`) | Clicking a leg row inside a composite navigates to the WHOLE template, not the leg. Either extend `_wireFocus` to handle TD clicks or document as accepted regression. |
| 21 | `topology.py:178-186` | `_INTERNAL_STYLE` fill (#dbe9f6) and `_TEMPLATE_STYLE` fill (#e8f0ff) have ΔE=3.73 — below the perceptual threshold. Silhouette difference (cylinder vs folder) carries the distinction at normal zoom but degrades at thumbnail. Low priority. |
| 22 | `diagram.js:219-307` (pan/zoom) | Heavy density renders 5152pt × 1198pt (4.3:1 aspect ratio) — initial fit-to-viewport squashes horizontally. Either better default zoom OR default to L2 for high-aspect graphs. |
| 23 | Self-loops (spec'd absorbed-into-shape, deferred) | `_template_inner_label` and Phase E still emit self-loop edges. Spec lock said "absorbed into shape via ↻ glyph"; this is future work. |

## Triage rationale

The MUST-FIX cluster (transfer_key repr + JS port-stripping + edge-kind fix + focus gate) shipped because:
- They produce visible-to-operator bugs the first time Studio opens the new diagram
- They're small surgical fixes (1-line, ~5-line JS additions)
- Topology unit tests still pass (28 / 28)

The deferred set is real but non-blocking:
- WCAG + composite CSS need a coordinated palette + CSS redesign (CF.3.f.b)
- `_leg_port` collision has no current trigger (no fixture has colliding rail names)
- Spec-deferred items (XOR edge style, self-loop absorption, multi-leg labels) were known gaps in v0.1
- LOW/NIT items are quality-of-life, can ship as a single hygiene pass alongside the palette rework
