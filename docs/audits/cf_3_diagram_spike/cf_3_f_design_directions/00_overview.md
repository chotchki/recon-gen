# CF.3.f — 5 design direction candidates (operator cold-read)

Workflow `w23hp0731` produced these 5 directions on 2026-06-04 (6 agents, 416k tokens, ~10 min). Each is a *complete* visual vocabulary for every L2 type in [the taxonomy](cf_3_f_design_directions/taxonomy.json), with ASCII mockups, rail-collapse + XOR treatments, and tradeoffs.

## Quick comparison

| # | Direction | Pitch | Complexity |
|---|---|---|---|
| 1 | [pictogram-icons](01_pictogram-icons.md) | Every L2 entity becomes a labeled SF-Symbols-style silhouette card — a 24px glyph slot encodes the type, a fixed badge gutter e… | `medium` |
| 2 | [monogram](02_monogram.md) | Every entity collapses to a 24×24pt typographic monogram — one bold capital letter wearing four corner slots and a left scope-s… | `medium` |
| 3 | [port-record](03_port-record.md) | Every Template is a single HTML-table glyph with one row per leg-port, chain edges dock at exact ports, and every other type is… | `high` |
| 4 | [silhouette-geometric](04_silhouette-geometric.md) | Each entity is one bold geometric primitive whose outline you recognize before you read the label — pills are accounts, chevron… | `low` |
| 5 | [scorecard-glyphs](05_scorecard-glyphs.md) | Every shape is a HTML-table mini-dashboard with a fixed left "spine strip" (color = kind, glyph = subtype, swatch = scope) and … | `high` |

## How to read these

Open each direction file and skim the ASCII mockups + the information_density_demo. The mockups will feel cartoonish (text), but they capture the *vocabulary*: shape primitives, badge slots, color rails, etc. The Phase 2 prototype renders the winner against `heavy_density_v1` for real PNG comparison.

Things to weigh per direction:

- **Information density** — how many facts can you read at a glance per shape?
- **Distinguishability** — could you tell Role / Rail / Template / Chain endpoint apart from 10 feet away?
- **Rail collapse + XOR feel** — does the bundled-N or exactly-one-fires idiom carry meaning, or feel bolted on?
- **Honesty of tradeoffs** — what do you give up? (label truncation, requires JS work, custom SVG assets, etc.)
- **Pairs with operator instincts** — does the design family say 'accounting topology' to someone who knows the domain, or does it feel generic?

Pick one (or pick best ideas to graft from runners-up). I'll then implement the chosen vocabulary against the heavy fixture + run the spike harness for the real metrics + PNG diff.

## Taxonomy reference

All 13 L2 type-categories the directions cover are in [taxonomy.json](cf_3_f_design_directions/taxonomy.json): Role (Account-scope), Role (AccountTemplate-scope), Rail (TwoLegRail), Rail (SingleLegRail), Rail Bundle (collapsed-parallel), Aggregating Rail (sweeper), TransferTemplate (cluster + inner node), TransferTemplate XOR-group (nested sub-cluster), Chain (edge between rail/template), Control-parent edge (subledger → control), LimitSchedule (cap, NOT rendered as node/edge today), Self-loop (single-leg rail on its own leg_role), Undeclared / orphan role (data-quality).

Heavy-fixture cardinality (what scales each design has to handle):

- **542** × l3_crossings_post_cf3a
- **223** × l3_edges_rendered
- **158** × l3_nodes_rendered
- **103** × rails_total
- **54** × single_leg_rails
- **49** × two_leg_rails
- **31** × transfer_templates
- **28** × accounts
- **20** × external_accounts
- **12** × chains
- **10** × parent_role_relations
- **8** × internal_accounts
- **6** × limit_schedules
- **4** × account_templates
- **4** × outbound_limit_schedules
- **2** × aggregating_rails
- **2** × inbound_limit_schedules
- **1** × templates_with_xor_groups
