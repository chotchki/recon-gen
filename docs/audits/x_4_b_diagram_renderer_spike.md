---
title: X.4.b — Diagram renderer spike judgment
---

# X.4.b — Diagram renderer spike judgment

**Status:** locked, 2026-05-13.
**Decision:** graphviz `dot`, with rails as first-class nodes.
**Rejected:** d3 + d3-force.

## TL;DR

Both arms got built and wired into Studio at `/diagram` and `/diagram/d3`.
The d3 arm hit a tuning treadmill — every dataset wanted different force
parameters; the spike accumulated 28 chrome sliders without converging.
Graphviz arm originally had a model problem (rails-as-edge-labels couldn't
be referenced by chains/templates without duplication), but a single model
pivot — promote rails to first-class nodes — let dot's deterministic rank
algorithm produce the user's mental "roles → rails → roles" reading with
zero knobs. That's the win.

The model insight is renderer-independent: the d3 arm had also surfaced it
(`to_d3_per_rail_json`). What separated the renderers was that dot's rank
algorithm could *consume* that model cleanly, while d3-force still required
per-knob tuning to make the layered reading appear.

## Criteria the spike was judged against

From `SPEC_studio.md` X.4.b.4:

1. Legible on `sasquatch_pr` without manual tuning.
2. All four entity-type toggles work (Roles / Rails / Chains / Templates).
3. Click-to-focus works.
4. Coverage tint hook exists (real data wires at X.4.c.5).

Per-rail/dot passes 1 (deterministic), 2 (CSS-class toggles via
post-processed `data-kind` attrs), 3 (server filter + URL navigation —
re-renders the focused subgraph rather than dimming), 4 (mode-stub overlay
already wired; real fetcher is X.4.c.5/6 work).

d3-force passed 2-4 but not 1 — it always *could* be tuned legible but
never *was* without manual knob-twiddling per dataset.

## What killed d3-force for this surface

**It's a tuning tool, not a layout tool.** The d3-force spike accumulated:

- 12 per-role-subkind force knobs (parent / child / standalone × Y / X /
  charge / collide).
- 4 rail force knobs.
- 4 template force knobs.
- 8 link-bound min/max knobs (per edge kind).
- A viewport-clamp custom force.
- A bounded-link custom force.
- Rail-bundling toggle + size-to-fit + multi-line label rendering.
- Band-hint stripes (off by default because no alignment was reliable).

Even with all that, picking up a different L2 instance (or reseeding the
positions) gave a different layout. The user named it directly: "we keep
adding knobs and not making good progress."

Force-directed is the right tool when the graph has no natural ranking
(arbitrary social network, etc.). L2 topology has a strong natural
ranking — money flows through accounts in directions — and dot is
purpose-built for that.

## What made dot work

**Rails as first-class nodes** — the model pivot, not the renderer choice.
With rails as nodes, every flow becomes a 3-rank chain:

```
src_role → rail → dst_role
```

Dot ranks `src` left, `rail` middle, `dst` right. Ten rails between two
roles become 10 nodes in the middle rank — but bundling collapses
pure-connectivity rails (those not referenced by any chain or template)
into one bundle node per topological key, so a real-world (5-rail Customer
DDA → External) bundle reads as one bold-bordered node with a multi-line
label. Anchored rails (chain endpoints / template leg-rails) stay
individual since the chain/template edges need stable identity.

Templates render as graphviz `cluster_*` subgraphs. Their leg-rail nodes
live inside the cluster but their `rail → role` endpoint edges still cross
the cluster boundary, so dot lays out the cluster around the rails AND
routes the connectivity through. The user sees "this template groups these
rails" + "those rails connect to these roles" simultaneously.

Chain edges connect the canonical rail/template nodes (no more dangling
plaintext labels). Control-parent edges (subledger → control role) are
dashed gray with `arrowhead="onormal"` to read as structural, not flow.

Compactness: `nodesep=0.15`, `ranksep=0.35`, `mclimit=2.0` (more
crossing-reduction iterations), `concentrate=true` (merge parallel edges).
Trades CPU for visual density; sasquatch_pr lays out under 100ms.

## Click-to-focus: rerender, not dim

The first cut focused via CSS classes (`.dim` / `.focus` / `.focused`
opacity). The user's feedback: that's "look harder," not "zoom in." The
final shape navigates to `?focus=<node_id>`; the server filters the typed
graph (or per-rail emit) to the focus subset and re-emits a smaller DOT
that dot re-lays out cleanly within the canvas. Click-empty-canvas / Esc /
the Reset button drop the param.

Smart-default hops by node kind: roles and templates default to 2 graph
hops (so focusing on a role traverses through one rail to surface the
other-side role); rails and bundles default to 1 (just the endpoints).
`_smart_focus_hops(focus_node_id)` lives in `common/l2/topology.py`.

## Tradeoffs accepted

- **Re-render on focus is a full DOM swap**, not a transform. Visually
  more disruptive than the dim approach. Acceptable because dot's layout
  on the focused subgraph is genuinely better than the dim'd full graph.
- **Bundle node IDs are positional** (`rail__bundle_0`, `rail__bundle_1`,
  …) computed deterministically from `instance.rails` iteration. Clicking
  a bundle navigates to `?focus=rail__bundle_N`; the server uses the same
  computation so the ID resolves correctly. Stable as long as the
  L2Instance ordering doesn't shift between full-render and focused-render
  (it doesn't — the focus filter operates AFTER bundle assignment over the
  full graph).
- **Templates point only to rails** — the template_role helper edges
  (template → role "uses" hints) were dropped. The per-rail layout makes
  template-touches-role visible naturally (template is a cluster around
  the rails; rails have edges to roles). The bundled view still had the
  helpers, which the user called out as confusing. Both views drop them
  now; per-rail is the only emit that ships.

## What got deleted (X.4.b cleanup)

After the lock:

- `/diagram/d3` route + `_render_d3_diagram_page`.
- `_studio_assets/diagram_d3.{js,css}` (~600 lines + 28-knob chrome).
- `to_d3_force_json`, `to_d3_per_rail_json` in `common/l2/topology.py`.
- The bundled `?model=` toggle + `_render_to_graphviz` + `build_topology_graph`
  + `render_topology` + `filter_topology_graph_focus` + `_filter_orphan_role_nodes`.
- `tests/unit/test_l2_topology.py` (legacy `render_topology` tests) +
  the d3-emit tests in `tests/unit/test_l2_topology_typed.py`.
- Dead CSS for `.dim` / `.focus` / `.focused` classes.

The d3 vendored asset stays — Investigation's Sankey + ForceGraph use it.

## Surviving artifacts

- `common/l2/topology.py::build_topology_graph_per_rail(instance, *, bundle_parallel_rails, focus_node_id, focus_hops)` — the only renderer.
- `common/l2/topology.py::topology_graph_for(instance) → TopologyGraph` —
  the typed projection. Per-rail emit reuses it for role-node iteration;
  Studio chrome reads it for entity counts.
- `common/l2/topology.py::_smart_focus_hops(focus_node_id) → int` — kind-aware BFS depth for focus-as-rerender.
- `common/html/_studio_assets/diagram.{js,css}` — wasm-graphviz render +
  `data-kind` post-processing + chrome wiring. JS shim is small enough that
  the ALL-CSS-class approach for visibility toggles + the URL-navigation
  approach for focus together replace what was a much larger interactive
  layer.

## Lessons that generalize

- **Tuning tools accumulate knobs; layout tools converge.** When a spike
  is adding chrome instead of removing it, the wrong tool is doing the
  work.
- **The model insight is often renderer-independent.** Promoting rails to
  first-class nodes was the breakthrough; both renderers benefited from
  it. The renderer choice was about which tool could *consume* the model
  cleanly.
- **Focus-as-rerender beats focus-as-dim** for graph diagrams where the
  layout itself is the interesting bit. Server-side filter + client
  navigation is a small implementation; the win is the focused layout
  using the full canvas.
- **Smart defaults beat explicit knobs** when the heuristic captures the
  user's mental model. "Roles want 2 hops, rails want 1" matches the
  intent ("show me what's connected to me") without exposing a
  `?focus_hops=` parameter.
