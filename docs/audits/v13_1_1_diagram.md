# recon-gen — Studio L2 Diagram, Layer-3 Legibility (upstream)

The densest diagram view (layer 3 — roles+rails+templates+chains+control+self-loops) is the hard one; layers 1-2 are readable. This is how to make the **single L3 canvas** legible at **zero information loss**, staying on server-rendered graphviz `dot` (force-directed / non-dot engines were tried independently on this graph and went nowhere — it's a *density* problem, not a layout-engine one). Counts on the demo instance: L1 = 14 nodes / 6 edges, L2 = 91 / 108, **L3 = 115 / 182**.

## Headline — a 2-line, measured, zero-info-loss fix (independently reproduced)

**Add `constraint=false` to the 58 `template_member` edges** (the dotted template→leg_rail membership edges). Measured with `dot 14.1.5` on the live `build_topology_graph_per_rail(instance, layer=3)` output, reproduced independently:

| | crossings (`mincross best_cross`) | canvas height | NaN |
|---|---|---|---|
| baseline L3 | **181** | 2937pt | 0 |
| + `constraint=false` on the 58 membership edges | **63 (−65%)** | 2497pt (**−15%**) | 0 |

**Why:** dot treats the dotted membership edges as rank-driving DAG edges, so each template + all its leg-rails are forced into a vertical cascade that smears the cluster across the canvas and rakes its dotted edges through everything. They're *membership, not flow* — they shouldn't drive rank. Releasing them packs each of the 24 template clusters into a tight local block. **Cluster containment is preserved** (82/82 member rail nodes verified still inside their template bbox; `constraint=false` removes rank coupling, not subgraph membership). Every node/edge/label/direction/count is bit-for-bit unchanged — it's a layout-only attribute, invisible to the SVG `<title>` stream `diagram.js` parses, so `data-id`/`data-kind` and click-to-focus/coverage/trainer are untouched.

- **Touchpoints:** `topology.py:1029` (`sub.edge`, non-XOR member) + `:1059` (`xor_sub.edge`, XOR member) — add `constraint="false"`. ~2 lines.
- **Free add-on:** `mclimit` 2.0→10.0 (`topology.py:745-754`). **Optional polish (crossing-neutral):** `group="spine"` on pool/hub roles + `group="ext"` on external roles for a stronger left-to-right spine.
- **Ship this first** — it's the whole "make L3 legible" win, no JS.

### Even better — fix the template DOUBLE-RENDER (the deeper version)
Each TransferTemplate is currently drawn **three times**: as a graphviz **cluster** (dashed box + title), as a **`tmpl__<name>` component node** with the *identical* label inside that box, AND with the **dotted `template_member` edges** re-drawing membership the cluster boundary already shows. Represent each template **once** (the cluster); demote the component node to an invisible `shape=point` chain-anchor (`topology.py:996`) and drop the membership edges (`:1028-1035` + `:1059-1065`). Measured/reproduced:

| variant | crossings | height | nodes | edges |
|---|---|---|---|---|
| baseline | 181 | 2937pt | 115 | 182 |
| `constraint=false` (2-liner) | 63 | 2497pt | 115 | 182 |
| **template recast** | **63** | **1985pt (−32%)** | **91** | **124** |

Same crossing win **plus** −24 nodes / −58 edges / ~2× the height cut, and it removes the visual double-label. **Supersedes the 2-liner** (membership edges are gone, so `constraint=false` is moot) — the 2-liner is the ship-today interim, this is the better follow-on. Caveats: keep the invisible anchor so chain edges (`:522/570/581`) still route (verified: 124 edges incl. chains, no NaN) — optionally `lhead`/`ltail` to clip them at the cluster boundary; relocate `transfer_key` from the dropped node label into the cluster label/tooltip (`_template_cluster_label:374` is name-only today); re-point the `template_member` "Show templates"/edge-label toggle to cluster visibility. Focus adjacency (`:876/882/889`) uses `_template_id` — unchanged.

## Additive stack (after the lever)
- **Edge-kind sidecar (prereq, S):** emit a `source→target→kind` edges sidecar (mirror `role_meta` at `routes.py:2701-2707`) and read exact kinds in `diagram.js` instead of the `_edgeKind` heuristic (`:66-80`), which misclassifies role→rail single-leg edges as `chain` and conflates `control_parent` with `rail_bundle`. The per-kind edge toggles + the items below depend on correct kinds.
- **True-relayout onion (M):** split `show_chains_and_templates` (`topology.py:919-920`) into independent reveal categories; add `?show=` (`routes.py:4227-4231`, keep `layer` as a compat shim); rewire the 6 "Show:" category checkboxes (`diagram.js:313-326`) from CSS-hide-on-frozen-layout to navigate-and-relayout (reuse `_navigateToFocus`). Fixes the "frozen gappy layout" — category reveal becomes a true dot relayout. Keep the 4 edge-*label* toggles as CSS-hide.
- **Focus+context fisheye (M):** port `_focus_set` (`topology.py:615-652`) into ~15 lines of JS over the stamped `g.edge[data-source][data-target]`; on focus, scale the focus subgraph up and shrink the rest to label-less pins + fade off-path edges (**scale/opacity only, never `display:none`**, so every `data-id` stays addressable and coverage/trainer tint composes). An "ambient LOD" default pins degree≤2 nodes (the self-loop-rail blob). NB: click-to-focus already re-layouts and helps *some* — this adds the in-place context that focus alone lacks.
- **Template-as-PORT-node (the strongest version, L):** render each template as a single `Mrecord` / HTML-`<table>` node with **a port per leg-rail** (name + Debit/Credit/… leg cells); **edges dock at the exact leg port** (`role -> tmpl:debit`, chain `tmpl:credit -> tmpl:debit`), so the attachment conveys which leg + direction, edges stop free-floating, and each template + its rails collapses to one glyph (biggest reduction). Prototyped + rendered clean (no NaN); dot supports it via record/HTML labels. **Gated** on the JS-binding work: rails become `<td>` cells not `g.node[data-id]`, so use HTML-`<table>` labels with per-cell `HREF`/`data-id`, and fix `_stripIdPrefix:471` / `_parseEdgeTitle:57` for the `:port` suffix + proxy coverage/trainer tint (`_stampCoverage:435`) onto the visible cell. Highest payoff; do after the earlier phases.

## Empirically DISPROVEN — do NOT retry (measured)
`newrank=true` **+727**, `ordering=out` +42, `rank=same` on roles +98-108, `rank=min`/`rank=max` +293, and **stacking** control/chain `constraint=false` on top of the template fix **regresses 63→125** (single targeted change, not a kitchen sink). `concentrate=true` NaN-crashes at L3; `splines=ortho` measured-bad. Rails→edges breaks the `g.node[data-id]` JS bindings. Non-dot engines (neato/sfdp/fdp/d3-force) go nowhere on this density.

## Development / validation guidance — build against a LARGE fuzz seed
Every tier above was validated on the demo L2 (115 nodes / 182 edges), which is **mid-size**. The legibility techniques *and their failure modes* only stress at density — so **develop and validate the diagram against a large, high-density fuzz-generated L2** (many templates, rails, chains, and high-fan-out roles), not the demo instance. recon-gen already has the mechanism: `RECON_GEN_FUZZ_SEED` pins/repros a seed, and `RECON_GEN_AI_FUZZ_SAMPLE_N` drives the Studio-editor **dogfood fuzz axis** (default 5-seed pool, `tests/unit/test_studio_editor_driver.py`) — extend that pool and bias toward large instances. At scale, confirm: `constraint=false` still wins crossings; the template **port/record nodes don't overflow** (a template with many legs, or a role with high fan-out); small-multiples / matrix dimensions stay usable; graphviz layout time stays within budget; and the focus/LoD heuristics (e.g. degree≤2 ambient pinning) don't mis-pin at higher degree. Ship each tier only after it holds on a large fuzz seed.

## Open questions
- `group=` crossing-neutral in the onion's *intermediate* reveal states too, or only at full L3?
- Ambient-LOD degree≤2 also pins genuinely low-degree roles/templates — confirm none important.
- A `templates on / rails off` reveal leaves self-loop direction arrows undrawn — accept as "reachable via label + sidecar"?
- Confirm `@hpcc-js/wasm-graphviz` (`diagram.js:90`) honors `constraint=false`/`group=` identically to server `dot`.
