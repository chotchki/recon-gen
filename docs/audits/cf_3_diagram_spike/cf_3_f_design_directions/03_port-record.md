# Direction 3/5 — port-record

**Pitch.** Every Template is a single HTML-table glyph with one row per leg-port, chain edges dock at exact ports, and every other type is the same table-of-cells DNA scaled down — header bar + body rows + side rail are the shared primitives the eye learns once and reuses everywhere.

**Rendering complexity:** high

## Shared visual primitives

- Header bar: top stripe with type-icon glyph (left), entity name (center), cardinality/scope badge (right) — every shape has one and it always reads top-to-bottom.
- Body rows: 0+ stacked <td> rows below the header, each carrying ONE atomic fact (a leg, a cap, a child, a parent). Row prefix is a single-char glyph (►/◄/▼/⚖/⏲/⚓/N×) that names the row kind.
- Left color-rail: 4px-wide vertical stripe down the left edge of every glyph, painting the SCOPE/KIND axis — blue=internal, amber=external, orange=template, gray=rail, purple=chain-anchor, red=undeclared. This is the eye-magnet for at-a-glance type-recognition.
- Right meta-rail: 4px-wide vertical stripe down the right edge reserved for COVERAGE TINT (data-rows seen) — paintable surface that never competes with semantic color (which lives on the left rail).
- Per-row port handles: every body row carries a graphviz port name (matching the row's content key — e.g. port='leg_rail_03' or port='cap_outbound_rail_12'); chain edges, control-parent edges, and limit-cap arcs all dock at the exact port via tail:port/head:port. No more edges floating in the middle of a glyph.
- Per-cell data-id: every cell is an HTML <td HREF='...' TITLE='kind:id'> so SVG keeps a stable click target per fact — bundle members, cap entries, XOR siblings all addressable individually without splitting into separate nodes.
- Helvetica 10pt name / 8pt meta — same as today's defaults; the language scales by adding/removing rows, not by changing typography.

## Vocabulary (per L2 type)

### Role (Account-scope, internal singleton)

```
  ┌──────────────────────────────┐
  │▌ ◉  InternalRole_02     [1] ▐│  ← header: kind-glyph ◉, name, [1]=singleton scope badge
  │▌──────────────────────────  ▐│
  │▌ ⚓ ctrl  → ConcMaster    ▐│  ← parent_role row (port='ctrl'), absent if no parent
  │▌ ⚖ caps  → 4 (3⇡ 1⇣)      ▐│  ← LimitSchedule rollup row (port='caps'), absent if 0
  │▌ ⇆ deg   → 17 rails        ▐│  ← connectivity-degree row
  │▌ Σ eod   → declared        ▐│  ← expected_eod_balance presence (✓/—)
  │▌                            ▐│
  └──────────────────────────────┘
   ↑                            ↑
   left-rail = scope (BLUE)     right-rail = coverage tint (paintable)

  External variant differs only by left-rail color (AMBER) + kind-glyph (◯ open vs ◉ filled):
  ┌──────────────────────────────┐
  │░ ◯  ExternalRole_04     [1] ░│  ← AMBER left-rail, hollow circle
  │░──────────────────────────  ░│
  │░ ⚓ ctrl  → ExtPool         ░│
  └──────────────────────────────┘
```

**Facts encoded:**
- scope (internal/external) → left-rail color + kind-glyph (◉ filled = internal, ◯ hollow = external)
- singleton vs templated → header badge '[1]' (singleton) vs '[N]' (templated, see next type)
- parent_role → 'ctrl' row with port='ctrl'; control_parent edge docks here via head:ctrl
- is-control-parent → cardinality count on '⚓ ctrl-children' inverted-row (only present when role is target of >=1 control_parent edge)
- carries LimitSchedule(s) → '⚖ caps' row showing count + direction breakdown (⇡=Outbound, ⇣=Inbound)
- expected_eod_balance presence → 'Σ eod' row with ✓/— marker
- connectivity degree → '⇆ deg' row with rail-count (data-quality smell: hot hub vs leaf)
- is-subledger → presence of 'ctrl' row implies subledger status
- undeclared/orphan state → left-rail goes RED + header badge becomes '[!]' (see undeclared type)

**Rendering approach:** graphviz node, shape='plain', label=HTML <TABLE BORDER='0' CELLBORDER='0' CELLSPACING='0' CELLPADDING='2' BGCOLOR='#ffffff'> with leftmost+rightmost <TD WIDTH='4' BGCOLOR='<scope-color>'> spanning all rows via ROWSPAN. Header is one <TR><TD COLSPAN='3'>...</TD></TR>. Each fact row is <TR><TD PORT='<key>'>glyph</TD><TD>label</TD><TD>value</TD></TR>. Node-level data-id='role__<name>' on the graphviz node carries through to <g class='node'> in SVG. Per-row PORT names let control_parent edges dock at head:ctrl precisely. Cells with HREF get individual <a> wrappers in SVG so per-cap / per-cell click works.

### Role (AccountTemplate-scope, templated)

```
  ┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓   ← header BAR is DOUBLE-LINE (=templated)
  ┃▌ ⊟  TemplateRole_02     [N] ┃    ← kind-glyph ⊟ (stack-of-records), [N]=many-instances
  ┃▌──────────────────────────  ┃
  ┃▌ ⚓ ctrl  → ConcMaster    ┃    ← parent must be a singleton (validator)
  ┃▌ id_t    → {{instance_id}}  ┃    ← custom instance_id_template marker (✓/—)
  ┃▌ name_t  → default          ┃    ← instance_name_template marker
  ┃▌ Σ eod   → declared         ┃
  ┃▌ ⇆ deg   → 23 rails (hot)   ┃    ← high fan-out flag
  ┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
   ↑
   left-rail color still encodes scope (BLUE for internal, AMBER for external — fixes the
   current latent bug where all templates share the blue _TEMPLATE_STYLE)
```

**Facts encoded:**
- templated (vs singleton) → double-line border + kind-glyph ⊟ + header badge '[N]' (not '[1]')
- scope (internal/external) → left-rail color (currently bugged in source — this design forces correct scope binding because the left-rail painter reads n.scope, not n.templated)
- parent_role → 'ctrl' row identical to singleton
- instance_id_template custom vs default → 'id_t' row shows template literal (truncated) or 'default'
- instance_name_template custom vs default → 'name_t' row same shape
- expected_eod_balance presence → 'Σ eod' row
- fan-out degree → '⇆ deg' row with '(hot)' suffix when degree exceeds threshold (templated roles in heavy are hubs)
- acts as chain-anchor → if any leg-rail this role touches sits in a TransferTemplate, no extra row needed — that's the template's job

**Rendering approach:** Same plain-shape HTML-TABLE pattern as singleton Role, but header BGCOLOR is striped (CSS-like: BGCOLOR='#e8f0ff' on header + a second nested TR with HEIGHT='2' BGCOLOR=border-color, repeated to create the double-line effect since dot's TABLE doesn't honor CSS borders). data-id='role__<template_role_name>'. Header badge '[N]' is a plain text cell — no count needed (the diagram doesn't know runtime instance count); the bracket convention alone signals 'many-instances class'.

### Rail (TwoLegRail, individual / anchored)

```
  ┌─────────────────────────────────┐
  │▌ ═  Rail_42            [2leg]  ▐│  ← kind-glyph ═ (two-leg = double line), [2leg] badge
  │▌─────────────────────────────  ▐│
  │▌ src  ► InternalRole_02 (Int)  ▐│  ← src-leg row (port='src'); 'Int'=InternalInitiated origin
  │▌ dst  ◄ ConcMaster      (Int)  ▐│  ← dst-leg row (port='dst')
  │▌ ⚖ cap  → 1 Outbound ($25k)   ▐│  ← LimitSchedule subject row (absent if no cap)
  │▌ ⏲ age  → pend≤4h, unb≤24h    ▐│  ← aging-watch row (absent if both unset)
  │▌ $$$    → typical $1k-$50k    ▐│  ← amount_typical_range band (absent if unset)
  │▌ ƒ      → ~12/day             ▐│  ← firings_typical_per_period (absent if unset)
  │▌ ⚓ chain  parent of Rail_89   ▐│  ← chain-anchor marker (absent if standalone)
  └─────────────────────────────────┘
   ↑
   left-rail = GRAY for plain rails;
   BLUE-outlined-GRAY when chain-anchored; ORANGE-outlined-GRAY when template-anchored;
   PURPLE-outlined-GRAY when both
```

**Facts encoded:**
- 2-leg discriminant → kind-glyph ═ (double bar) + header badge '[2leg]'
- direction (source→destination) → 'src ►' / 'dst ◄' arrows in row prefix; ports 'src' and 'dst' let endpoint edges dock at the exact leg
- per-leg origin → '(Int)' / '(Ext)' / '(Agg)' / '(Cust)' suffix on each leg row — answers 'is this leg internally-initiated vs force-posted vs aggregated'
- anchoring state → left-rail OUTLINE color (blue=chain-anchored, orange=template-anchored, purple=both, none=standalone-not-bundled)
- LimitSchedule subject → '⚖ cap' row with count + dir-rollup + total magnitude
- aging watches → '⏲ age' row with both bounds (pend / unb)
- magnitude band → '$$$' row with range; row presence itself signals 'AB.5 declared'
- firing cadence → 'ƒ' row with /period; row presence signals 'AF declared'
- chain participation → '⚓ chain' row naming the chain role (parent/child) — only when chain-anchored
- metadata key count → tooltip on header (not a row — too low-value for body-row real estate)
- aggregating-ness → see separate Aggregating-Rail type (it claims its own glyph)

**Rendering approach:** graphviz node, shape='plain', HTML TABLE with PORT='src' on the source-leg row's left cell and PORT='dst' on the destination-leg row's left cell. Endpoint edges become role_node:e -> rail_node:src (head:src) and rail_node:dst:e -> role_node (tail:dst). data-id='rail__<rail_name>' on node. Rows are emitted conditionally — minimum is header + src + dst; everything else (⚖/⏲/$$$/ƒ/⚓) is present only when the underlying field is set. At small scale (sasquatch) rails collapse to header+src+dst (3 rows). At heavy density, rails-with-everything are taller but still bounded.

### Rail (SingleLegRail, individual / anchored)

```
  ┌─────────────────────────────────┐
  │▌ ─  Rail_64            [1leg]  ▐│  ← kind-glyph ─ (single bar), [1leg] badge
  │▌─────────────────────────────  ▐│
  │▌ leg  ▼ ExternalRole_07  Dr   ▐│  ← leg row (port='leg'); ▼=Debit, ▲=Credit, ◆=Variable
  │▌                       (Ext)  ▐│
  │▌ ⚓ recon  TransferTmpl_05    ▐│  ← reconciliation source (template OR aggregating rail)
  │▌ ⏲ age   pend≤2h              ▐│
  └─────────────────────────────────┘

  Variable-direction variant (XOR closing leg):
  ┌─────────────────────────────────┐
  │▌ ─  FuzzXorVariant_Auto [1leg] ▐│
  │▌─────────────────────────────  ▐│
  │▌ leg  ◆ InternalRole_05 Var   ▐│  ← ◆=Variable, distinct from ▼/▲
  │▌ ⚓ recon  TransferTmpl_FuzzXor ▐│
  │▌ ⚠ xor    closing leg, grp 1   ▐│  ← Variable-only diagnostic row
  └─────────────────────────────────┘
```

**Facts encoded:**
- 1-leg discriminant → kind-glyph ─ (single bar) + header badge '[1leg]'
- leg direction Debit/Credit/Variable → row prefix ▼/▲/◆ + suffix 'Dr'/'Cr'/'Var' (redundant on purpose; both color-blind and small-screen readable)
- leg port → port='leg' so endpoint edge docks at the exact row
- Variable-as-closing-leg-of-XOR → '⚠ xor' row appears with group index — operators triaging XOR templates see the closing-leg fact without leaving the rail glyph
- reconciliation source → '⚓ recon' row naming the template or aggregating rail; absent → 'UNCOVERED' RED row (data-quality)
- leg origin → '(Int)'/'(Ext)'/'(Agg)'/'(Cust)' on the leg row, identical to TwoLegRail
- anchoring → left-rail outline same scheme as TwoLegRail (blue/orange/purple/none)
- magnitude/cadence/aging/limits → same conditional rows as TwoLegRail

**Rendering approach:** Same plain-shape HTML-TABLE; only one leg row instead of src+dst. PORT='leg' on the single leg row. data-id='rail__<rail_name>'. Variable-direction uses a third arrow glyph ◆ (distinct from ▼/▲) plus the optional ⚠ xor diagnostic row. The 'self-loop' case (leg_role identical to an endpoint of another two-leg rail involving the same role) is handled at the edge layer — the rail glyph itself is unchanged; the edge that runs role:e -> rail:leg loops back, dot already handles this.

### Rail Bundle (collapsed-parallel)

```
  ┌─────────────────────────────────┐
  │▌ ═  ⊞ bundle__7        [N×17] ▐│  ← kind-glyph ═ + ⊞ overlay = bundle; N×17 badge
  │▌─────────────────────────────  ▐│
  │▌ src  ► InternalRole_02       ▐│  ← shared src/dst (bundle key)
  │▌ dst  ◄ ConcMaster            ▐│
  │▌─────────────────────────────  ▐│
  │▌ ▾ members (click to expand)  ▐│  ← expander row (port='expand')
  │▌   • Rail_12                  ▐│  ← first 3 members listed inline (port='mem_Rail_12')
  │▌   • Rail_19                  ▐│  ← each with its own data-id for trainer/coverage
  │▌   • Rail_27                  ▐│
  │▌   … +14 more                 ▐│  ← elision marker; click expander → sidebar list
  └─────────────────────────────────┘
   ↑
   left-rail = GRAY-with-DASHED-OUTLINE — bundle stays in the rail family but the
   dashed outline says 'collapsed; not load-bearing individually'

  At small N (2-3): elision row absent, all members listed:
  ┌─────────────────────────────────┐
  │▌ ═  ⊞ bundle__2         [N×2] ▐│
  │▌─────────────────────────────  ▐│
  │▌ src  ► InternalRole_05       ▐│
  │▌ dst  ◄ FundsPool             ▐│
  │▌ ▾ members                    ▐│
  │▌   • Rail_88                  ▐│
  │▌   • Rail_91                  ▐│
  └─────────────────────────────────┘
```

**Facts encoded:**
- bundle-ness (vs individual rail) → kind-glyph overlay ⊞ + dashed left-rail outline + 'bundle__N' name prefix
- N count → header badge '[N×<count>]' — instantly readable at any scale
- direction → same src/dst rows as TwoLegRail; for singleleg bundles, single 'leg' row with direction glyph
- endpoint role pair → src/dst rows (bundle key is structural — these match every member)
- member rail names → bullet rows, first 3 inline, '…+N more' elision; each bullet has its own data-id='rail__<rail_name>' so members stay addressable for trainer/coverage WITHOUT splitting into separate graphviz nodes (the bundle stays one glyph, but its cells are individually wired)
- expansion affordance → '▾ members' row with port='expand'; JS layer can wire click-on-expander to open a sidebar listing all members; click on a bullet routes to that specific rail's entity card
- FIXES the synthetic-id problem from the audit → bundle's own data-id stays addressable (rail__bundle_<idx>), but each member cell ALSO carries the real rail__<name> data-id so visible_entities_for + trainer don't fall back to un-filter-all

**Rendering approach:** graphviz node, shape='plain', HTML TABLE with dashed outline (BGCOLOR + an extra border-emulating row). PORT='src' / 'dst' / 'leg' identical to individual-rail glyph so endpoint edges dock the same way. PORT='expand' on the expander row. Member bullets are <TR><TD PORT='mem_<rail_name>' HREF='trainer/rail/<rail_name>'>• <name></TD></TR>. data-id at node level = 'rail__bundle_<idx>'; data-id per cell (via PORT name) = 'rail__<member_name>'. The endpoint-edge penwidth-scaling (min(1.0+0.3*N, 3.0)) stays — the bundle bar is wider so it earns thicker incoming edges.

### Aggregating Rail (sweeper)

```
  ┌─────────────────────────────────┐
  │▌ ⟳  Rail_81            [Σ-2leg]▐│  ← kind-glyph ⟳ (cyclic = sweep); [Σ-2leg] badge
  │▌─────────────────────────────  ▐│
  │▌ src  ► InternalRole_02       ▐│
  │▌ dst  ◄ InternalRole_05       ▐│
  │▌─────────────────────────────  ▐│
  │▌ ⏲ cadence  intraday-4h       ▐│  ← REQUIRED row for aggregating; bold
  │▌ ⌬ bundles  29 activities     ▐│  ← REQUIRED row; count of bundles_activity refs
  │▌   • Rail_12 (R)              ▐│  ← first 3 bundled refs inline
  │▌   • Rail_19 (R)              ▐│  ← (R)=Rail ref, (T)=Template ref, (Y)=TransferType
  │▌   • payment-out (Y)          ▐│
  │▌   … +26 more                 ▐│
  └─────────────────────────────────┘
   ↑
   left-rail = GRAY with TEAL TOP-CAP — sweepers get a unique color slot (teal)
   on the top of the left-rail to flag 'this is operational rhythm, not flow'
```

**Facts encoded:**
- is-aggregating → kind-glyph ⟳ + header badge '[Σ-2leg]' or '[Σ-1leg]' (the Σ prefix is the unique sweeper marker)
- cadence (intraday-Nh / daily-eod / weekly-mon / monthly-eom) → '⏲ cadence' row, ALWAYS present (validator-required for aggregating)
- bundle scope count → '⌬ bundles N' row with N=len(bundles_activity)
- bundled-activity refs → bullet rows with (R)/(T)/(Y) kind-suffix telling operator whether the ref is a Rail / Template / TransferType, each with its own data-id for navigation
- sweep direction → src/dst rows same as TwoLegRail; visually identical, the ⟳ glyph + the bundle list disambiguate
- ghost convergence (future) → optional dashed gray edges from each bundled-activity rail's right port to this sweeper's left port; can be toggled by the Show: layer
- left-rail teal top-cap → the only place teal appears in the language; sweepers earn a unique color slot because 'this fires on a clock, not on an event' is a fundamentally different mental model

**Rendering approach:** Same plain-shape HTML-TABLE as Rail. Adds two required rows (cadence + bundles) and bullet rows for the bundled-activity refs. Left-rail gets a special construction: top 12pt is a TEAL <TD WIDTH='4' BGCOLOR='#1abc9c'>, remaining height is GRAY <TD WIDTH='4' BGCOLOR='#666666'> — both spanning their share of the rows via ROWSPAN. data-id='rail__<name>' (no _agg_ prefix; aggregating is a flag, not a separate kind). Bullets carry per-ref data-id='rail__<name>' / 'tmpl__<name>' / 'type__<name>' so clicking a bundled-activity ref navigates to that entity.

### TransferTemplate (cluster + inner node) — RECAST as single port-record

```
  ╔═══════════════════════════════════════════╗   ← double-border = TEMPLATE family
  ║▌ ⊡  TransferTmpl_15           [T·5legs]  ▐║   ← kind-glyph ⊡; badge: T=template, 5legs=count
  ║▌ keys: [transfer_id, batch_id]            ▐║   ← transfer_key row (was lost in current emit)
  ║▌ Σnet: 0   complete: business_day_end+2d  ▐║   ← expected_net + completion (CRITICAL, today invisible)
  ║▌ ƒ: ~3/day                                ▐║   ← firings_typical_per_period
  ║▌════════════════════════════════════════ ▐║
  ║▌ leg  Rail_12      src►dst    (Int)      ▐║   ← port='leg_Rail_12'; chain edges dock here
  ║▌ leg  Rail_19      src►dst    (Int)      ▐║   ← port='leg_Rail_19'
  ║▌ leg  Rail_27      leg▲ Cr    (Agg)      ▐║   ← port='leg_Rail_27'; singleleg in template
  ║▌ leg  Rail_33      leg▼ Dr    (Int)      ▐║   ← port='leg_Rail_33'
  ║▌ leg  Rail_41      leg◆ Var   (Int) ⚠   ▐║   ← ⚠ = variable, hints at XOR; see XOR type
  ║▌════════════════════════════════════════ ▐║
  ║▌ ⚓ chain  child of Rail_18               ▐║   ← chain participation (port='chain_in')
  ║▌ ⚓ chain  parent of TransferTmpl_27      ▐║   ← (port='chain_out')
  ╚═══════════════════════════════════════════╝
   ↑
   left-rail = ORANGE (template family color); double-border on the whole glyph;
   ELIMINATES the current double-render (cluster + inner component node) — the
   table IS the template. Endpoints of leg rails STILL exist as separate rail
   glyphs elsewhere on the canvas, but membership is now a port docking, not a
   dotted edge — dot has many fewer edges to lay out (matches the audit's
   'template recast' -58 edges win).
```

**Facts encoded:**
- cluster identity → the table itself; data-id='tmpl__<name>'
- template name → header center cell
- transfer_key list → 'keys:' row (today STORED in metadata but NOT rendered)
- expected_net + completion expression → combined row 'Σnet: 0   complete: <expr>' (CRITICAL today-invisible facts)
- firings_typical_per_period → 'ƒ:' row when set
- leg-rail count → header badge '[T·Nlegs]'
- per-leg-rail membership → one body row per leg-rail with port='leg_<rail_name>'
- per-leg type (1leg/2leg + direction + origin) → row content shows src►dst (2leg) or leg▲/▼/◆ Dr/Cr/Var + (Int)/(Ext)/(Agg)/(Cust)
- chain participation as parent/child → '⚓ chain' rows at the bottom with port='chain_in' / 'chain_out' so chain edges dock at the exact role
- shared-rail anomaly (Rail_23 in 2 templates) → flag via a footer row '⚠ shared with TransferTmpl_13' when seen_rail_ids race detected; instead of lying about exclusive membership, surface it

**Rendering approach:** graphviz node, shape='plain', HTML TABLE with double-border emulation (outer + inner TR rows of BGCOLOR=border filling HEIGHT=2). REPLACES the current graphviz subgraph cluster + inner component-node pair. Membership edges (dotted template_member) are DELETED — the table rows ARE the membership. Chain edges now use port docking: dot syntax g.edge('tmpl__15:chain_out', 'rail__Rail_18:src') or similar. Per-leg PORTs ('leg_<rail_name>') let chain-in/chain-out edges land on the exact leg they relate to. data-id='tmpl__<name>' at node level; per-row PORT names plus HREF give each leg its own click target. JS layer (_stripIdPrefix, _parseEdgeTitle) needs the ':port' suffix handling the audit's CF.3.f flagged — that's the upfront cost.

### TransferTemplate XOR-group (nested sub-cluster) — RECAST as inset rows

```
  ╔═══════════════════════════════════════════╗
  ║▌ ⊡  TransferTmpl_FuzzXor      [T·3legs]  ▐║
  ║▌ keys: [transfer_id]                      ▐║
  ║▌ Σnet: 0   complete: business_day_end     ▐║
  ║▌════════════════════════════════════════ ▐║
  ║▌ ┌─XOR grp 1: exactly 1 fires──────────┐ ▐║   ← inset frame inside the template table
  ║▌ │  leg  FuzzXorVariant_Auto    ◆ Var │ ▐║   ← port='leg_FuzzXorVariant_Auto'
  ║▌ │  leg  FuzzXorVariant_Std     ◆ Var │ ▐║
  ║▌ │  leg  FuzzXorVariant_Slow    ◆ Var │ ▐║
  ║▌ └──────────────────────────────────────┘ ▐║
  ║▌════════════════════════════════════════ ▐║
  ║▌ leg  Rail_OpeningDr   src►dst    (Int)  ▐║   ← non-XOR leg rendered at template level
  ╚═══════════════════════════════════════════╝

  Multi-XOR template (two groups):
  ║▌ ┌─XOR grp 1: exactly 1 fires──────────┐ ▐║
  ║▌ │  leg  RailA          ◆ Var          │ ▐║
  ║▌ │  leg  RailB          ◆ Var          │ ▐║
  ║▌ └──────────────────────────────────────┘ ▐║
  ║▌ ┌─XOR grp 2: exactly 1 fires──────────┐ ▐║
  ║▌ │  leg  RailC          ◆ Var          │ ▐║
  ║▌ │  leg  RailD          ◆ Var          │ ▐║
  ║▌ └──────────────────────────────────────┘ ▐║
```

**Facts encoded:**
- XOR-group identity gi → inset frame label 'XOR grp <gi+1>'
- member count → implicit from row count inside the frame; explicit in label '(of N)' if needed
- member rail names → one row per member, each with port='leg_<rail_name>' so chain edges to XOR members still dock precisely
- 'exactly 1 fires' invariant → frame label text
- Variable-direction members → ◆ Var glyph on each row (validator-required)
- relationship to parent template's expected_net → implicit from the frame being INSIDE the template's table (vs floating)
- multiple XOR groups → multiple inset frames, vertically stacked
- data-id on the XOR frame itself → 'tmpl__<name>__xor_<gi>' on a PORT covering the whole frame's header row → addressable for 'filter to this XOR contract'

**Rendering approach:** Implemented as nested HTML <TABLE> inside the parent template's TABLE — graphviz renders this fine. Inset frame is a sub-table with BORDER='1' COLOR='#5a6f9c' BGCOLOR='#f0f4ff' (matching today's XOR colors), CAPTION-ROW header for the label. Per-member-row PORTs at the OUTER template-table level (PORT='leg_<rail_name>') still work because graphviz HTML labels propagate PORT to the leaf cell regardless of nesting depth. data-id='tmpl__<name>__xor_<gi>' assigned via the frame's header row PORT name. Eliminates the today subgraph-cluster nesting entirely (no more cluster_tmpl_<name>_xor_<gi>) — it's all one TABLE.

### Chain (edge between rail/template)

```
  Required chain (singleton child):
   ┌──tmpl glyph──┐                          ┌──rail glyph──┐
   │ ...          │                          │ ...          │
   │ ⚓ chain_out ─────────━━━━━━━━━━━━━━━━━→│ src ◄........│
   │              │      ↑                   │              │
   └──────────────┘   solid + filled-arrow   └──────────────┘
                      label: 'req'
                      penwidth=1.5
                      color=#4a4a4a

  XOR chain (multi children — 3-way):
   ┌──parent rail glyph──┐
   │ ...                 │                          ┌──Rail_A──┐
   │ ⚓ chain (xor of 3) ─┬──━━━━━━━━━━━━━━━━━━━━──→│ src      │
   │                     │     ↑                    └──────────┘
   │                     │  hollow-diamond head     ┌──Rail_B──┐
   │                     ├──━━━━━━━━━━━━━━━━━━━━──→│ src      │
   └─────────────────────┘     +                    └──────────┘
                               same source port +   ┌──Rail_C──┐
                               'xor 1/3' label      │ src      │
                               on each              └──────────┘

  Fan-in chain (N parent firings → 1 child template Transfer):
   ┌──parent rail──┐                            ┌──child template──┐
   │ ⚓ chain_out  │═══━━━━━━━━━━━━━━━━━━━━━━━━▶│ chain_in         │
   │               │   ↑                        │ (Σnet: 0)        │
   └───────────────┘ DOUBLE LINE (penwidth=3)   └──────────────────┘
                     hollow-funnel arrowhead
                     label: 'fan-in N→1' + expected count if set
```

**Facts encoded:**
- required vs XOR cardinality → arrowhead shape (filled = required singleton, hollow-diamond = XOR member); label 'req' vs 'xor i/N' per edge
- fan-in vs not → edge weight: penwidth=1.5 for 1:1, penwidth=3 + double-line + hollow-funnel arrowhead for fan-in
- expected parent count when set → suffix on label 'fan-in N→1 (exp 7)'
- XOR siblings as a group → all edges share the SAME source PORT (port='chain_out') and fan out to distinct child ports — graphviz lays this out as a visual fan from one anchor, making the alternation obvious
- parent kind (rail vs template) → already implicit from the source glyph's left-rail color
- child kind → same on target glyph
- chain edges to/from XOR-group-member rails → dock at port='leg_<rail_name>' INSIDE the template table → operator sees exactly which member the chain references
- cross-cluster smell → chain edges that traverse long canvas distances are inherent to layout, but port docking eliminates the floating-mid-glyph appearance

**Rendering approach:** graphviz edge with tail and head ports: g.edge('tmpl__15:chain_out', 'rail__18:src', style='solid', color='#4a4a4a', penwidth='1.5', arrowhead='normal', label='req'). XOR uses arrowhead='odiamond'. Fan-in uses arrowhead='oinv' with penwidth='3.0' and label='fan-in N→1'. STYLE CHANGE: chain edges become SOLID (today they're dashed, which collides with control_parent). Solid = behavioral/sequencing; dashed = structural/hierarchical. Each chain edge gets a stable id via dot's edge id= attribute = 'chain__<parent>__<child>' so JS click handlers can route to the chain entity card.

### Control-parent edge (subledger → control)

```
  Subledger account → control parent (no caps on parent):
   ┌──ExternalRole_07──┐                         ┌──ConcMaster──┐
   │ ◯  ExtRole_07 [1] │                         │ ◉ ConcMaster │
   │ ⚓ ctrl ──────────┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄→│ ctrl-of: 7   │
   └───────────────────┘   ↑                     └──────────────┘
                  DASHED + open-arrowhead
                  color=#888888 (gray) — distinct from chain (which is now SOLID)
                  no label needed; the edge kind is unambiguous from style

  Templated subledger → control:
   ┏━TemplateRole_02━┓                          ┌──ConcMaster──┐
   ┃ ⊟  TR_02   [N] ┃                          │ ◉ ConcMaster │
   ┃ ⚓ ctrl ───────┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄→│ ctrl-of: 8   │
   ┗━━━━━━━━━━━━━━━━┛   ↑                      │ ⚖ caps: 4    │
                same dashed-open edge           └──────────────┘
                source double-border signals    ↑
                child kind = template           cap badge on target
                                                signals caps live here

  HOT control (target of many control_parents):
   ConcMaster's '⚓ ctrl-children: 23' row in its own glyph becomes the visible badge.
   Operator sees 'this is a hot control account' from the count, not by edge-counting.
```

**Facts encoded:**
- direction (subledger → parent) → arrowhead at parent end
- child kind (Account vs AccountTemplate) → source-glyph's header (single vs double border) — no extra edge decoration needed
- fan-in count on control parent → '⚓ ctrl-children: N' row on the control role's own glyph (counted at emit time)
- structural-vs-behavioral split → DASHED + GRAY for control_parent; SOLID + DARK-GRAY for chain — the visual distinction is now unambiguous (collision in today's emit FIXED by making chain solid)
- caps live on the parent → '⚖ caps' row on the parent role's glyph, not on the edge (today's '($ caps)' label moved to where it belongs)

**Rendering approach:** graphviz edge: g.edge('role__ExtRole_07:ctrl', 'role__ConcMaster', style='dashed', color='#888888', arrowhead='open'). PORT docking on the source side ('ctrl') so the edge originates from the exact parent_role row on the subledger glyph. No label on the edge — the edge kind is unambiguous because chain is now SOLID. data-id on the edge via id='ctrl__<child>__<parent>'. The cap badge that today decorates this edge ('controls ($ caps)') MOVES to the parent role's glyph as a '⚖ caps' body row.

### LimitSchedule (cap, was unrendered)

```
  LimitSchedule gets TWO renderings — one on the parent role, one on each subject rail.

  (1) On the parent role (rollup row):
   ┌──ConcMaster───────────────────┐
   │▌ ◉  ConcMaster        [1]    ▐│
   │▌ ⚖ caps  → 4 (3⇡ 1⇣) port=⚖ ▐│   ← rollup row; port='caps' for cap-arc head
   │▌    • Rail_12  ⇡Out $25k     ▐│   ← per-cap detail rows (port='cap_Rail_12_Out')
   │▌    • Rail_19  ⇡Out $5k      ▐│
   │▌    • Rail_27  ⇡Out $100k    ▐│
   │▌    • Rail_42  ⇣In  $50k     ▐│   ← inbound: ⇣ glyph distinguishes
   └───────────────────────────────┘

  (2) On the subject rail (cap badge):
   ┌──Rail_12─────────────────────┐
   │▌ ═  Rail_12          [2leg] ▐│
   │▌ src ► Internal     (Int)   ▐│
   │▌ dst ◄ ExtRole_07   (Int)   ▐│
   │▌ ⚖ cap → 1 Out $25k         ▐│   ← reverse-lookup row; port='cap_Out'
   │▌    @ ConcMaster (ctrl)     ▐│   ← which control owns the cap
   └──────────────────────────────┘

  Optional: a hairline cap-arc edge connecting the two:
   role:cap_Rail_12_Out  ╮
                          ╲╲╲ thin amber arc, arrowhead=none
   rail:cap_Out          ╱
   (rendered when the focus filter includes BOTH endpoints, dimmed otherwise to
   avoid arc clutter on dense canvases)
```

**Facts encoded:**
- existence → '⚖ caps' row on parent role + '⚖ cap' row on subject rail; both addressable via PORT for direct entity-card click
- cap amount → '$<magnitude>' in row text — readable in both places
- direction (Outbound/Inbound) → ⇡/⇣ glyph + 'Out'/'In' label on each cap row
- specific subject rail → per-cap bullet row on the role glyph; reverse on the rail glyph
- count of caps on a parent → header badge in the rollup row '4 (3⇡ 1⇣)' = total / Outbound / Inbound breakdown
- composite identity → PORT name encodes (parent_role, rail, direction); data-id via HREF on the cell carries 'ls__<parent>__<rail>'
- interaction with aggregating-rail subject → the cap row on the sweeper's glyph still appears — operator sees 'this sweep is throttled'

**Rendering approach:** Two HTML-TABLE row insertions, no new graphviz nodes. Parent role glyph gets a '⚖ caps' rollup row + per-cap bullet rows underneath (one per LimitSchedule entry whose parent_role matches). Subject rail glyph gets a reverse-lookup '⚖ cap' row (one per LimitSchedule entry whose rail matches). PORT names on each cap row let an OPTIONAL cap-arc edge be emitted between them (style='solid', color='#d4a017' amber, penwidth=0.5, arrowhead='none', constraint='false') so dot doesn't rank-couple them. Cap arcs default OFF at heavy density (toggleable via Show: lever); default ON at sasquatch scale where they're readable. data-id per cap = 'ls__<parent_role>__<rail>' (the composite key visible_entities_for already uses).

### Self-loop (single-leg rail on its own leg_role)

```
  Self-loop = the leg_role IS one of the roles already adjacent to this rail.
  No special glyph needed: the rail and the role are normal port-records, and
  the edge loops from role:e → rail:leg via dot's normal self-loop routing.

   ┌──ExternalRole_04────────┐
   │░ ◯  ExternalRole_04 [1] ░│
   │░──────────────────────  ░│
   │░ ⚓ ctrl → ExtPool      ░│       ╭─────╮  ← dot's self-loop arc
   │░                        ░│       │     │
   └─────────────────────────┘    ┌───┴─────┴───┐
                ↑                 │ Rail_10     │
                role glyph        │ ─  [1leg]   │
                                  │ leg ▼ ExtR4 │
                                  │     Dr (Ext)│
                                  └─────────────┘
                ↑
                rail glyph; edge runs ExternalRole_04:e → Rail_10:leg
                and the same role appears as the destination of another two-leg rail
                — dot lays out the loop without language-level intervention

  Direction (Debit/Credit/Variable) is on the rail glyph's leg row (▼/▲/◆),
  not on the loop edge — the edge is just a routing concern, the semantic
  fact lives in the rail.
```

**Facts encoded:**
- direction (Debit/Credit/Variable) → already on the rail glyph's leg row (▼/▲/◆); the loop edge needs no extra decoration
- reconciliation source → already in the rail glyph's '⚓ recon' row
- leg_role identity → already in the leg row
- self-loop topology → emergent from the data; the language doesn't need a special idiom for it

**Rendering approach:** No language-level treatment. Same plain-shape rail glyph + same role glyph + dot's normal self-loop edge routing. The PORT-record approach actually IMPROVES self-loops because the edge now docks at port='leg' on the rail and exits the role from a specific side rather than floating mid-glyph. The today TopologyEdge.kind='self_loop' diagnostic stays in the typed projection for completeness but renders no differently from a normal endpoint edge.

### Undeclared / orphan role (data-quality)

```
  Undeclared (referenced by rail, no Account/Template):
  ┌──────────────────────────────┐
  │▌ ⚠  Phantom_Role_X      [!] ▐│   ← RED left-rail; ⚠ kind-glyph; [!] badge
  │▌──────────────────────────  ▐│
  │▌ ⊘ undeclared                ▐│   ← diagnostic row; red text
  │▌ ← referenced by:            ▐│
  │▌   Rail_42 (src)             ▐│   ← which rails reference; each addressable
  │▌   Rail_99 (dst)             ▐│
  └──────────────────────────────┘

  Orphan (declared, never referenced) — surfaces at Layer 1 only:
  ┌──────────────────────────────┐
  │▒ ◉  DeclaredButUnused   [1] ▒│   ← FADED left-rail (50% opacity blue)
  │▒──────────────────────────  ▒│
  │▒ ⊘ orphan: no rail refs     ▒│   ← diagnostic row; gray text
  │▒ ⚓ ctrl → ConcMaster        ▒│   ← still shows declared facts
  └──────────────────────────────┘
```

**Facts encoded:**
- undeclared state → RED left-rail (unique color slot; reserved for data-quality errors) + ⚠ kind-glyph + '[!]' header badge + '⊘ undeclared' diagnostic row
- orphan state → FADED left-rail (50% opacity of the would-be scope color) + '⊘ orphan' diagnostic row
- rails referencing the undeclared role → per-rail bullet rows with addressable data-id so operator can click through and fix
- renders-but-flagged contract → undeclared role is EMITTED (not silently dropped) so it's visible in the diagnostic; today's _style_for fallback silently uses internal style — this design makes the bug visible

**Rendering approach:** Same plain-shape HTML-TABLE as Role. Left-rail BGCOLOR=#c00000 for undeclared, alpha-blended (BGCOLOR with #RRGGBBAA notation if supported, else flat gray-mix-down for orphan). Header badge '[!]'. '⊘ undeclared' or '⊘ orphan' diagnostic row added unconditionally when n.scope is None or n.id not in referenced_roles. data-id='role__<name>' with an additional data-state='undeclared'/'orphan' attribute on the graphviz node (passable via tooltip or post-SVG-processing) so trainer/coverage can filter for data-quality smells.

## Rail collapse treatment

Collapsed bundles render as a port-record rail glyph with three distinguishing features stacked top-to-bottom: (1) header gets a ⊞ kind-glyph overlay on the standard ═/─ rail bar + a '[N×<count>]' badge that's instantly countable at any scale, (2) the left-rail outline becomes DASHED-GRAY so 'collapsed/not load-bearing' reads at the same glance as 'individual rail' would as a solid-outline-gray, and (3) below the standard src/dst rows, a 'members' section lists the first 3 member rails as bullet rows with elision '…+N more' beyond 3. Each member bullet carries its own PORT name (port='mem_<rail_name>') and HREF (data-id='rail__<rail_name>') so the bundle stays ONE graphviz node but each member is INDIVIDUALLY clickable — this fixes today's synthetic-id problem where rail__bundle_<idx> falls back to un-filter-all in visible_entities_for. An '▾ members (click to expand)' row with port='expand' affords sidebar-expansion in the JS layer. ASCII:\n\n  ┌─────────────────────────────────┐\n  │▌ ═  ⊞ bundle__7        [N×17] ▐│  ← ⊞ overlay + N×17 badge = bundle, instant read\n  │▌─────────────────────────────  ▐│\n  │▌ src  ► InternalRole_02       ▐│  ← shared src/dst from the bundle key\n  │▌ dst  ◄ ConcMaster            ▐│\n  │▌─────────────────────────────  ▐│\n  │▌ ▾ members (click to expand)  ▐│  ← port='expand'; sidebar trigger\n  │▌   • Rail_12                  ▐│  ← port='mem_Rail_12', HREF='trainer/rail/Rail_12'\n  │▌   • Rail_19                  ▐│  ← per-member addressability preserved\n  │▌   • Rail_27                  ▐│\n  │▌   … +14 more                 ▐│\n  └─────────────────────────────────┘\n   left-rail = DASHED-GRAY outline (vs SOLID-GRAY for individual rails)\n\nEndpoint edges still scale penwidth with N (today's min(1.0+0.3*N, 3.0) preserved) so the bundle's gravity is visible from across the canvas before the eye lands on the [N×count] badge.

## XOR group treatment

XOR groups render as an INSET HTML sub-table INSIDE the parent template's main table — not as a separate graphviz cluster. The inset has BORDER=1, BGCOLOR=#f0f4ff (today's XOR fill, preserved for muscle memory), a CAPTION-ROW header 'XOR grp <gi+1>: exactly 1 fires', and one row per member rail. Each member row carries the SAME port naming convention as a non-XOR leg ('leg_<rail_name>') so chain edges land identically whether targeting an XOR member or a standalone leg. The Variable-direction glyph ◆ on each member row reinforces the closing-leg semantic. Multiple XOR groups in one template stack vertically as multiple insets, each with its own gi-suffixed PORT for 'filter to this XOR contract'. Non-XOR leg rails in the same template render OUTSIDE the inset, at the template-table level. ASCII:\n\n  ╔═══════════════════════════════════════════╗\n  ║▌ ⊡  TransferTmpl_FuzzXor    [T·3legs]    ▐║\n  ║▌ Σnet: 0   complete: business_day_end     ▐║\n  ║▌════════════════════════════════════════ ▐║\n  ║▌ ┌─XOR grp 1: exactly 1 fires──────────┐ ▐║   ← inset table; data-id='tmpl__FuzzXor__xor_0'\n  ║▌ │  leg  FuzzXorVariant_Auto    ◆ Var │ ▐║   ← port='leg_FuzzXorVariant_Auto'\n  ║▌ │  leg  FuzzXorVariant_Std     ◆ Var │ ▐║   ← chain edges still dock at exact port\n  ║▌ │  leg  FuzzXorVariant_Slow    ◆ Var │ ▐║\n  ║▌ └──────────────────────────────────────┘ ▐║\n  ║▌════════════════════════════════════════ ▐║\n  ║▌ leg  Rail_OpeningDr   src►dst  (Int)    ▐║   ← non-XOR leg outside the inset\n  ╚═══════════════════════════════════════════╝\n\nThis ELIMINATES today's nested graphviz subgraph cluster (cluster_tmpl_<name>_xor_<gi>) — saves dot a clustering pass and removes the cluster_xor / cluster_tmpl rank tension. The frame's header row carries the XOR group's own data-id, so 'show me this mutual-exclusion contract' becomes a first-class filter target.

## Information density demo (Template + 5 rails + 2 chains)

```
Template with 5 legs + 2 chains attached (one incoming chain from Rail_18, one outgoing chain to TransferTmpl_27), plus 1 of the 5 legs is in an XOR group with 2 sibling members. This is a realistic heavy-fixture-shape template glyph:\n\n         ┌─Rail_18─────────────────┐\n         │▌ ═  Rail_18    [2leg]  ▐│\n         │▌ src ► InternalRole_02 ▐│\n         │▌ dst ◄ ConcMaster      ▐│\n         │▌ ⚓ chain  parent      ▐│ ──┐\n         └─────────────────────────┘   │\n                                       │ chain edge: solid, penwidth=1.5, arrowhead=normal\n                                       │ docks at port='chain_in' of target\n                                       ▼ label='req'\n         ╔════════════════════════════════════════════╗\n         ║▌ ⊡  TransferTmpl_15           [T·5legs]   ▐║\n         ║▌ keys: [transfer_id, batch_id]             ▐║\n         ║▌ Σnet: 0   complete: business_day_end+2d   ▐║\n         ║▌ ƒ: ~3/day                                 ▐║\n         ║▌═════════════════════════════════════════ ▐║\n         ║▌ leg  Rail_OpenDr     src►dst   (Int)     ▐║ ← port='leg_Rail_OpenDr'\n         ║▌ leg  Rail_OpenCr     src►dst   (Int)     ▐║ ← port='leg_Rail_OpenCr'\n         ║▌ leg  Rail_FeeOut     leg▼ Dr   (Agg)     ▐║ ← port='leg_Rail_FeeOut'\n         ║▌ ┌─XOR grp 1: exactly 1 fires──────────┐  ▐║\n         ║▌ │  leg  Rail_CloseAuto    ◆ Var      │  ▐║ ← port='leg_Rail_CloseAuto'\n         ║▌ │  leg  Rail_CloseManual  ◆ Var      │  ▐║ ← port='leg_Rail_CloseManual'\n         ║▌ └──────────────────────────────────────┘  ▐║\n         ║▌═════════════════════════════════════════ ▐║\n         ║▌ ⚓ chain_in   ← child of Rail_18         ▐║ ← incoming chain docks HERE (port='chain_in')\n         ║▌ ⚓ chain_out  → parent of TransferTmpl_27▐║ ── ┐ outgoing docks at port='chain_out'\n         ╚════════════════════════════════════════════╝   │\n                                                          │ chain edge: solid, penwidth=1.5,\n                                                          ▼ arrowhead=normal, label='req'\n         ╔─TransferTmpl_27 [T·3legs]──────────────────╗\n         ║▌ keys: [transfer_id]                       ▐║\n         ║▌ Σnet: 0   complete: business_day_end      ▐║\n         ║▌ leg  Rail_NextA      src►dst   (Int)     ▐║\n         ║▌ leg  Rail_NextB      leg▲ Cr   (Int)     ▐║\n         ║▌ leg  Rail_NextC      leg▼ Dr   (Int)     ▐║\n         ║▌ ⚓ chain_in  ← child of TransferTmpl_15  ▐║\n         ╚════════════════════════════════════════════╝\n\nINFORMATION DENSITY ACCOUNTING for the central template glyph alone:\n  - 1 template name + kind + leg-count badge        (header)\n  - 2-element transfer_key                          (keys row)\n  - expected_net AND completion expression          (combined row — was INVISIBLE today)\n  - firings-per-period band                         (ƒ row — was INVISIBLE today)\n  - 5 leg-rail names + per-leg type + per-leg direction + per-leg origin (5 rows)\n  - 1 XOR contract with explicit 'exactly 1 fires' invariant + 2 member rails (inset)\n  - 2 chain participation roles with explicit parent/child + counterpart names (2 rows)\n\nTotal facts encoded in ONE template glyph: ~22 atomic facts. Today's render of the same template shows: template name (1) + 5 dotted membership lines (5) + maybe a transfer_key in the inner-node label (1) = 7 facts, and the operator must trace dotted lines to count legs. Density ratio: ~3.1× more facts per glyph at roughly comparable visual footprint (the table is taller but template+leg-rail-membership-edges collapse into ONE glyph instead of 1 cluster + 1 inner node + 5 free-floating rail nodes + 5 dotted edges = 12 visual objects → 1). At heavy-fixture scale (31 templates × 5 legs avg = 155 visual-object savings = ~half the current edge count).
```

## Consistency argument

Every shape in this language is the SAME graphviz construct — shape='plain' + HTML-TABLE label — and composes from the SAME three primitives: a header bar, 0+ body rows, two side rails. Roles, rails, bundles, sweepers, templates, XOR groups, and undeclared/orphan diagnostics are all variations on 'how many rows, which kind-glyph in the header, which color on the left rail'. The eye learns ONE pattern (top-band identifies type, body-rows enumerate facts, left-rail paints scope, right-rail paints coverage) and reuses it on every glyph. Kind-discrimination lives in two places only — the header's kind-glyph character (◉/◯/⊟/═/─/⊞/⟳/⊡/⚠) and the left-rail color (blue/amber/orange/gray/teal-cap/red) — both readable in <500ms of glance. Within-family variation (singleton vs templated role; 2leg vs 1leg rail; standalone vs bundled vs aggregating; XOR vs non-XOR template) uses sub-cues (border style, badge bracket, kind-glyph overlay) that only matter once the family is recognized. This is the opposite of today's mix-of-shapes (box / folder / ellipse / component / dashed-cluster / dashed-cluster-inside-dashed-cluster) where each new type added a new graphviz shape and there's no shared chrome to ground the visual grammar.

## Tradeoffs (honest cost)

- JS-layer rebinding cost is UP-FRONT and unavoidable: today's diagram.js keys off g.node[data-id] for the whole rendered shape; port-records make rails into <td PORT> cells, so _stripIdPrefix (:471) and _parseEdgeTitle (:57) need to handle ':port' suffixes, and _stampCoverage (:435) needs to proxy onto visible cells. The v13.1.1 audit explicitly called this out as the gate on the port-node direction — paying it once buys per-leg / per-cap / per-member click targets forever.
- Glyph footprint grows VERTICALLY: a heavily-decorated rail with origin + cap + aging + magnitude + cadence + chain rows is 8-9 rows tall vs today's 1-row ellipse. At heavy-fixture L3 (158 nodes) this means total canvas height grows ~30-50% even after collapsing template+inner-node+membership-edges into one glyph. Mitigation: rows are conditional (only present when the underlying field is set), so sasquatch-scale glyphs stay 3-4 rows; the row vocabulary is consistent so vertical scanning is cheap.
- Color budget is TIGHT: left-rail uses blue/amber/orange/gray/teal/red/purple-outline = 7 distinct slots, plus right-rail coverage tint paints over a separate slot. Operators with red-green color-blindness lose the 'undeclared' red signal — mitigation is the ⚠ kind-glyph + [!] header badge as redundant signals, but the color stays as the at-a-glance cue.
- Bundle expansion is a JS-side concern: the in-glyph member list shows first 3 + elision; full expansion (all N members in a sidebar) requires JS click handling on port='expand'. Until that JS lands, large bundles look truncated. Acceptable interim because the bullets that ARE shown carry per-rail data-ids — trainer/coverage already work pre-expansion.
- Chain SOLID vs control_parent DASHED is a STYLE INVERSION from today (today both dashed, only color differs). Operator muscle memory shifts; existing screenshots in docs/PLANs go stale. Worth it because the structural-vs-behavioral split becomes unambiguous and the chain edges stop looking like extra control_parent edges.
- Aggregating-rail glyph spends a unique color slot (teal top-cap on left-rail) on a feature that appears only 2× in the heavy fixture. The slot is 'expensive' relative to its frequency. Justification: sweepers are operationally critical — invisible-today is the worst possible state; a unique slot makes them findable from across the canvas.
- Per-leg PORT names ('leg_<rail_name>') mean rail names with non-alphanumeric chars need escaping for dot's port syntax. Validator currently allows Identifier characters that are dot-safe, so this is a constraint-codification not a real restriction, but it pins down naming.
- Per-cap arc edges (the optional thin amber connection between a role's '⚖ caps' row and a rail's '⚖ cap' row) ADD edge count when toggled on. At heavy density they'd be illegible — default OFF, toggleable. The fact still exists in both glyphs' body rows; the arc is the optional 'connect the dots' affordance.
- Templated-role header double-line emulation requires multi-row HTML-TABLE construction (no native CSS borders in graphviz HTML). Adds ~3 lines of label-build code per templated role; verbose but not deep complexity.
- Eliminates today's clean separation between 'cluster' (template) and 'node' (rail/role) graphviz concepts — everything becomes a node. Anyone debugging dot output loses the 'find cluster_tmpl_X' grep affordance; gain is the 50% edge-count drop the audit measured for the template recast.
- Coverage-tint reserved as the RIGHT rail competes with the existing per-shape fill for the operator's 'is this hot?' question; reserving a dedicated paintable slot solves it cleanly but commits the language to right-rail forever for that fact.
