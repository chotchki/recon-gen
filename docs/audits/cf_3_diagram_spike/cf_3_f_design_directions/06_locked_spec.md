# CF.3.f — locked spec (operator cold-read 2026-06-04)

## Operator's verdict on the 5 directions

> "the two to pull on together is the port-record for its exact connections and the silhouette-geometric for its distinctive visual feel. However ALL of these I think are trying to encode too much information. The value here is understanding HOW thing link together, the whole over the parts. If you want the details we already have everything ready to click down and filter to what has caught your attention."

Plus a hand-authored mock (`run/diagram_layers/proto_ports.png` — labels are placeholder, only the shape language matters).

## Synthesis lock

The five directions were over-instructed to maximize information density. The operator's principle is the opposite: **the diagram answers "how does this connect"; details come from clicking down to filtered views**. Strip the encoding ambition; keep the structural clarity.

Two contributions survive:
- **port-record** — exact connection docking: chain edges land on the specific leg-row of the destination template, not on a cluster boundary or a wrapper node. The composite HTML-table shape is the right vehicle.
- **silhouette-geometric** — distinct shape per type at a glance. Roles stay simple; templates get the new composite. Operators tell parts apart by silhouette, not by badges.

Everything else from the 5 directions — scope icons, fan-out indicators, aging chips, magnitude rings, sparklines, cardinality badges, monogram letter-glyphs, scorecard rings — **dropped**. They're solving the wrong problem.

## Visual language v0 (scope = templates only)

| Type                                  | Shape v0                                                                                  | Note                                              |
|---------------------------------------|--------------------------------------------------------------------------------------------|---------------------------------------------------|
| Role (internal / external / template) | rounded box (CF.3.a baseline)                                                              | Color discipline unchanged: blue / yellow / blue. |
| Standalone Rail (TwoLeg / SingleLeg)  | ellipse (CF.3.a baseline)                                                                  | Iterate later if needed.                          |
| Rail bundle                           | ellipse with `×N` label (CF.3.a baseline)                                                  | Iterate later if needed.                          |
| Aggregating Rail                      | ellipse (CF.3.a baseline)                                                                  | Iterate later if needed.                          |
| **TransferTemplate**                  | **HTML-`<table>` composite, two columns, one port-row per leg-rail.**                      | **This is the change.**                           |
| TransferTemplate XOR-group            | leg-rows for the group share a side-rail color or `XOR (1 of N)` row separator             | v0 = explicit `XOR` row marker; iterate.          |
| Chain edge                            | dashed orange, dock at `tmpl:leg_<name>:e` / `:w`. Label only on chain edges with metadata. | "chain (required)" / "chain (any of)" if fan-in.  |
| Control-parent edge                   | dashed gray (CF.3.a baseline)                                                              | Unchanged.                                        |
| LimitSchedule                         | not rendered as node                                                                       | Surface via click-down panel.                     |

## TransferTemplate shape v0

```
┌───────────────────────────┬────────────────────────────┐
│                           │  leg_rail_name_1  (Debit)  │  <- port="leg_<rail_1>"
│   TransferTemplate Name   ├────────────────────────────┤
│   (rowspan = N legs)      │  leg_rail_name_2  (Credit) │  <- port="leg_<rail_2>"
│                           ├────────────────────────────┤
│                           │  leg_rail_name_3  (Var)    │  <- port="leg_<rail_3>"
└───────────────────────────┴────────────────────────────┘
```

Edges:
- **chain (required)**: `tmpl_A:leg_<rail_X>:e -> tmpl_B:leg_<rail_Y>:w`, dashed orange, arrowhead=normal, label="chain (required)"
- **rail flow** (when one leg of a template-resident two-leg rail connects to a non-template role): `tmpl_A:leg_<rail_X>:e -> role__R` (or reverse), solid blue, arrowhead matches direction, label="debit" / "credit"

## What's deleted vs CF.3.a baseline

- `subgraph cluster_tmpl_<name>` boundary (the composite shape IS the boundary)
- `tmpl__<name>` inner component node (the table's left cell carries the name)
- All `template_member` edges, non-XOR (`topology.py:1029-1042`) AND XOR (`:1059-1072`) — rails-in-template become port rows, not separate nodes connected by dotted edges
- (The `constraint=false` from CF.3.a on those edges is moot now since the edges are gone — see the audit's "Even better — fix the template DOUBLE-RENDER" section, which predicted this supersession.)

## What's NOT touched in v0 (deferred until the template change reads right)

- Standalone (non-template) Rail visuals — stay as ellipses
- Rail bundle visual — stay as `×N` ellipse
- Aggregating Rail distinguisher — none yet
- Role badges/info — none, by design
- Chain edges between standalone rails (no template port to dock to) — keep current rail-node-to-rail-node shape
- JS click-handlers for cell-level data-id — port-cell clicks fall back to whole-shape click on the template; we will fix `_stripIdPrefix:471` / `_parseEdgeTitle:57` later if cell-level routing is needed

## Predicted impact (audit's "template recast" measurement)

The v13.1.1 audit measured this exact pattern on its demo L2 (115 nodes / 182 edges):
- baseline: 181 crossings, 2937pt height
- constraint=false (CF.3.a): 63 crossings, 2497pt height
- **template recast (this spec): 63 crossings, 1985pt (−32%), 91 nodes (−24), 124 edges (−58)**

For heavy_density_v1 (158 nodes / 223 edges after CF.3.a), expected:
- ~30 fewer nodes (one per template, after rail-port absorption gives more depending on rail count)
- ~60 fewer edges (membership edges dropped)
- Crossings: stays near CF.3.a's 542 or improves (port-anchored chain edges have fewer ambiguous endpoints)
- Height: should drop notably (no nested cluster + node both contributing to vertical demand)

We'll know once it lands. Spike harness reports the actuals into `docs/audits/cf_3_diagram_spike/heavy_density_v1_cf3f/metrics.json`.

## Operator refinement (2026-06-04, post-mock)

The mock was an example of the connection pattern, NOT the final shape choice. Locks for prototype v0.1:

- **Shapes get personality.** Roles, rails, templates each get DISTINCT silhouettes — not just rounded-rect / ellipse / record. Color, rounding, size are all variables for distinction. Operators tell parts apart at a glance from the silhouette, not the label.
- **Self-loops absorbed into the shape itself.** A role with a self-rail-loop shouldn't draw a separate looping edge — the shape carries a "↻" annotation or the port re-enters the same shape. CF.3.a still draws these as visible loops; that's noise to remove.
- **XOR groups: dual-encoded.** Side-rail color band on the XOR-member port rows (inside the template shape) AND matched line-style/weight across all chain edges into those XOR-group ports. Reader sees XOR from either the shape or the edges.
- **Port edges carry differentiation.** Edge color, weight, style, arrow type are all part of the language (Debit vs Credit, chain-required vs chain-XOR, control-parent).
- **Colors not locked.** The existing palette is a starting point only — pick visually distinct, AA-accessible colors. Avoid red-green-only encodings.

## Shape vocabulary v0.1 (final locked, smoke-tested)

Smoke test rendered + operator-confirmed: [`smoke_test_v3_locked.png`](smoke_test_v3_locked.png) (DOT source [here](smoke_test_v3_locked.dot)).

| Type                             | Graphviz shape / approach                                         | Notes                                                                                    |
|----------------------------------|--------------------------------------------------------------------|------------------------------------------------------------------------------------------|
| Internal Singleton Role          | `shape=cylinder`                                                   | Ledger/vault silhouette; institution-side balance-bearing                                |
| External Singleton Role          | `shape=note`                                                       | Folded-corner external metaphor                                                          |
| Account Template Role            | `shape=folder`                                                     | Kept — multiple runtime instances                                                        |
| Standalone SingleLegRail         | `shape=cds` (single-stroke chevron)                                | Direction baked into the chevron silhouette                                              |
| Standalone TwoLegRail            | `shape=cds, peripheries=2` (double-stroke chevron)                 | Doubled border = "two legs converge here" (operator: ideally right-only; full-perimeter is graphviz's nearest built-in. Custom polygon if needed later.) |
| Rail Bundle (collapsed parallel) | Same as underlying rail shape + `×N` in label                      | Stroke count stays the same; the `×N` label is the bundle signal                         |
| Aggregating Rail                 | `shape=cylinder, peripheries=2`                                    | Double-cylinder = sweep semantics                                                        |
| TransferTemplate                 | HTML-`<table>` composite — top-header row + leg-rail rows below   | Header `#ffcc99` (orange bold), leg rows `#fff2e0` (light orange), each `PORT="leg_<rail_name>"` for east/west edge docking |
| TransferTemplate XOR-group       | Side-rail color band on member port rows + matched edge-style      | Dual-encoded so reader sees XOR from either the shape or the chain edges                 |
| Chain edge (required)            | `style=dashed, color=#9e0142`, label `"chain (required)"`          | Distinct color from rail edges; label kept on chain edges per operator                   |
| Chain edge (any-of, XOR)         | Same color + matched `penwidth` across XOR sibling edges           | Edge-side signal of XOR grouping                                                          |
| Rail edge (Debit)                | `color=#d95f02`, NO label (arrow + color carry direction)          | Operator: drop labels on rail edges by default; details via click-down                   |
| Rail edge (Credit)               | `color=#1b9e77`, NO label                                          | "                                                                                        |
| Rail edge (Variable)             | `color=#7570b3`, NO label                                          | "                                                                                        |
| Multi-leg-template rail edge     | Label kept ("debit"/"credit"/"var")                                | Where a single TwoLegRail straddles two roles AND is in a template — needs disambiguation |
| Self-loop on role                | Absorb into role shape (`↻` glyph appended to label)               | DO NOT draw a separate looping edge                                                       |
| Control-parent edge              | dashed gray (unchanged)                                            | "                                                                                        |
| LimitSchedule                    | NOT rendered as node                                               | Surface via click-down panel                                                              |

**Color palette source:** ColorBrewer "Dark2" — accessibility-tested, color-blind-friendly. Existing #1f4e79 / #7f6000 / #e8f0ff fills (roles + template-role) kept as starting point; can refine post-render.

## Open question parked for after v0.1 lands

- Color palette refinement (AA-accessible, ideally color-blind-safe). v0.1 keeps existing palette as starting point; we re-palette after the SHAPES read right.
- Whether standalone single-leg rails should collapse to labeled edges entirely (no node) once two-leg rails are clearly readable as `cds` nodes.
