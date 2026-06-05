# Direction 5/5 — scorecard-glyphs

**Pitch.** Every shape is a HTML-table mini-dashboard with a fixed left "spine strip" (color = kind, glyph = subtype, swatch = scope) and a right "fact panel" of micro-badges that turn the diagram from an identity map into a one-glance health board.

**Rendering complexity:** high

## Shared visual primitives

- Spine strip (left 8-12px column): solid color band that names the KIND in one channel — blue=role, orange=template, slate=rail, plum=bundle, teal=aggregator. Coverage tint paints HERE (top-to-bottom fill), so the kind color and the data tint never fight for the same pixels.
- Glyph cell (top-left, 16px square): subtype icon — square for singleton role, folder for templated role, arrow-right for TwoLegRail, hook for SingleLegRail, fan for bundle, clock for aggregator. Lets the operator read kind+subtype from 50% zoom.
- Title row (12pt bold): analyst-facing identifier. Always wraps at 24ch — predictable bbox keeps dot's layout sane.
- Badge slot (top-right corner, 3x10px chips): up to 3 single-letter status pips — L=limit-bearing, A=aging-watched, X=XOR-member, F=fan-in target, V=variable-direction, !=data-quality flag. Reading order = severity.
- Micro-bar lane (bottom 8px): horizontal stack-bar quantifying the shape's main count — leg-rails for templates, member-count for bundles, fan-out degree for roles, magnitude band for rails. Black tickmarks at p50/p95 of the instance's distribution so the bar is comparative not absolute.
- Footer row (9pt mono, italic): the one secondary identifier that disambiguates (transfer_key for templates, cadence for aggregators, parent_role for subledger roles, direction-arrow + role for rails).
- Corner-style rule: rounded corners = balance-tracked / institution-owned; sharp corners = external / counterparty / synthetic. Operator learns the perimeter without a legend.
- Border-stroke pattern: solid = declared & wired; dashed = templated (many instances); dotted-red = undeclared / orphan (data-quality). Stroke pattern is independent of fill and of spine color, so it overlays cleanly.
- Every shape has the same outer bbox aspect ratio (~2.4:1) so dot's rank/grid algorithm doesn't have to juggle wildly different widths — info density rides INSIDE the box, not via box size.

## Vocabulary (per L2 type)

### Role (Account-scope)

```
Internal singleton (8 in heavy):
+========================================+
|#| [#] InternalRole_02              L L |   <- spine=blue, glyph=square, 2 limit pips
|#|                                      |
|#| Cash | EOD: $4.2M  | parent: GLCtrl  |   <- footer: balance + control breadcrumb
|#| ##########|#####|##  deg 14 (p95:11) |   <- micro-bar: fan-out degree, p95 tick
+========================================+

External singleton (20 in heavy):
+----------------------------------------+
|.| [#] ExternalRole_07                  |   <- sharp corners, dotted spine, no L pip
|.|                                      |
|.| Counterparty | EOD: -- | parent: --  |
|.| ###|####################  deg 2      |
+----------------------------------------+

Control parent (cap-bearing) variant adds 'CTRL' chip on title row:
+========================================+
|#| [#] InternalRole_05  [CTRL] L L L L  |   <- 4 caps stacked
|#| Concentration | EOD: $58M | parent:- |
|#| ##############|####|####  deg 31     |
+========================================+

Undeclared role (loud red dotted border, ! pip):
+----------------------------------------+
|?| [?] RoleMissingDecl                ! |
|?| UNDECLARED -- referenced by Rail_44  |
|?| -                                    |
+----------------------------------------+
```

**Facts encoded:**
- scope (internal/external) -> corner radius + spine color saturation
- control-parent role -> [CTRL] inline chip on title row
- limit-bearing -> L pips top-right, one per LimitSchedule (max 4 then '+N')
- expected_eod_balance set/unset -> footer shows $value or '--'
- parent_role -> footer breadcrumb 'parent: <name>' or '--'
- fan-out degree -> micro-bar at bottom with p50/p95 ticks (computed once per render)
- undeclared/orphan -> dotted-red border + ! pip + spine becomes '?'
- subledger (own parent_role) -> footer always shows parent; if parent itself has parent, breadcrumb chains 'parent: B>C'

**Rendering approach:** shape=plaintext, label=<<TABLE BORDER='0' CELLBORDER='1' CELLSPACING='0' CELLPADDING='2'>...</TABLE>>. Outer TABLE carries the data-id via HREF='#role__<name>' + TOOLTIP. First TR has 2 TDs: TD width=10 BGCOLOR=spine + TD COLSPAN=N for title/glyph/pips. Second TR=footer. Third TR=micro-bar (TD BGCOLOR=fill repeated to draw the bar segments). Rounded vs sharp = TABLE STYLE='ROUNDED' vs not. Coverage tint stamped JS-side on the spine TD's BGCOLOR. Border-stroke pattern via TABLE BORDER on outer wrapper subgraph (graphviz HTML labels don't natively support dashed table borders, so dashed/dotted is faked with a wrapping cluster of style=dashed,peripheries=1 around the single role node — cheap and dot honors it).

### Role (AccountTemplate-scope)

```
Internal templated (4 in heavy):
+========================================+
|#| [F] TemplateRole_02   [tmpl x N]     |   <- folder glyph, [tmpl] chip, dashed border
|#|                                      |
|#| Subledger | parent: ConcentrationMst |
|#| ##############|######  deg 18 (p95)  |
+========================================+
   ^^ dashed border = templated

With expected_eod_balance set, footer shows it:
|#| Subledger | EOD/inst: $250k | par:..|

External-scope templated (latent bug today, supported here):
+----------------------------------------+
|.| [F] ExtTemplateRole_01  [tmpl]       |   <- sharp corners + dashed border + ext spine
|.| Counterparty class | parent: --      |
|.| ##|##########  deg 3                 |
+----------------------------------------+
```

**Facts encoded:**
- templated -> folder glyph + dashed outer border + [tmpl] inline chip
- scope -> same corner+spine rule as singleton (fixes the latent ext-template bug — scope flows from primitive, not _style_for fallback)
- instance count hint -> [tmpl x N] chip shows declared instance-id-template's expected count when known, else 'tmpl'
- parent_role -> footer breadcrumb (validator: always a singleton, so chain depth is 1)
- expected_eod_balance set -> footer EOD/inst
- custom instance_id_template -> small '*' adornment on the folder glyph
- fan-out degree -> same micro-bar treatment

**Rendering approach:** Same HTML-table plaintext shape as Account-role; differs in (a) glyph cell shows folder unicode/svg, (b) wrapped in cluster style='dashed,peripheries=1' to get the dashed outer border, (c) the chips row is mandatory. Data-id stays role__<name> — one role identity regardless of singleton/templated.

### Rail (TwoLegRail)

```
Standalone TwoLegRail (most common):
+--------------------------------------------------+
|=| [->] Rail_12     orig:I  $$  10/d    A         |   <- spine=slate, ->arrow glyph, magnitude $$, cadence 10/d, A=aging
|=|                                                |
|=| Src_A --> Dst_B   key: txn_id   T:TmplA        |   <- source-arrow-dest line, transfer_key, template membership
|=| amt: |##|####|########|##  p50:$5k p95:$420k   |   <- magnitude band sparkbar with anchored p50/p95
+--------------------------------------------------+

TwoLegRail with multi-role source (fan-out):
|=| [->] Rail_77    orig:E                          |
|=| {A,B,C} --> Dst_X                               |   <- braces denote role-set

XOR-group member variant (X pip, V pip if Variable):
|=| [->] Rail_FXa   X V                             |   <- inside XOR sub-cluster too
|=| Src --> Dst    key: k_0   T:FuzzXor[g1]         |

Limit-subject rail:
|=| [->] Rail_18                       L L          |   <- 2 limits target this rail
|=| Src --> Dst   key: k0   T:Tmpl14                |

Anchored vs bundled is visually identical to standalone — the difference is whether the rail RENDERS individually or gets folded into a bundle node (see Rail Bundle).
```

**Facts encoded:**
- 2-leg vs 1-leg -> -> glyph (right-arrow) vs hook glyph
- direction -> -> glyph orients left-to-right by default; flow direction comes from src-->dst in footer + actual edge arrows
- origin -> orig:I/E/A/X chip on title row (I=InternalInitiated, E=ExternalForcePosted, A=ExternalAggregated, X=custom); per-leg overrides show as 'orig:I/E' split
- magnitude band -> $/$$/$$$ chip + bottom sparkbar with p50/p95 anchored from amount_typical_range, instance-wide p50/p95 ticks for comparison
- cadence -> N/d, N/w, N/mo chip from firings_typical_per_period (no chip if unset)
- aging-watched -> A pip
- limit-subject -> L pip count (one per matching LimitSchedule.rail entry)
- XOR/Variable -> X and V pips (inside XOR sub-cluster framing too)
- transfer_key -> footer 'key: <fields>'
- template membership -> footer 'T:<TmplName>'; if rail appears in 2+ templates (shared-rail anomaly), footer shows 'T:A|B [!]' + ! pip
- multi-role expression -> brace notation {A,B,C} in src/dst slot
- self-source==dest -> footer renders 'Src_A <-> Src_A' (double-arrow indicating self-loop)
- anchored vs bundled -> a rail you see at all IS anchored; the bundle treatment handles unanchored siblings

**Rendering approach:** shape=plaintext, HTML-TABLE with 3 rows: title-row (spine TD + glyph + name + chips + pips), src-arrow-dst footer (TD COLSPAN with src --> dst text + key + template ref), magnitude sparkbar row (TD COLSPAN with N micro-TDs of varying BGCOLOR to draw a bar; p50/p95 ticks are 1px black TDs interleaved). Data-id = rail__<name>. Aspect ratio constrained to ~2.4:1 to keep dot's rank packing predictable. SingleLegRail uses the same shape with glyph=hook and src/dst line reads 'Role <-/-> Role (Debit/Credit/Variable)' — see SingleLegRail entry.

### Rail (SingleLegRail)

```
Debit SingleLegRail:
+--------------------------------------------------+
|=| [J] Rail_24    orig:I  $   2/d                  |   <- hook (J) glyph, no A pip, single-$
|=|                                                |
|=| Role_X -> rail   (Debit)   key: k0  T:Tmpl3    |
|=| amt: |#|##|####  p50:$80 p95:$3k                |
+--------------------------------------------------+

Credit SingleLegRail (arrow flipped in footer):
|=| [J] Rail_25    orig:E                          |
|=| rail -> Role_X  (Credit)  key: k0  T:Tmpl3     |

Variable SingleLegRail (XOR closing leg — V pip + V in direction slot):
+--------------------------------------------------+
|=| [J] FuzzXorVarA   X V                          |
|=|                                                |
|=| Role_X <-> rail  (Variable) key:k0 T:FuzzXor[g1]|
|=| amt: |##|##|####|##  p50:$120 p95:$2.4k        |
+--------------------------------------------------+

Aggregating SingleLegRail (rare):
|=| [J] Rail_99   orig:I  $$  agg                  |   <- 'agg' chip flags aggregator
|=| Role_Y -> rail  (Debit)  cadence: weekly-mon   |   <- footer cadence overrides template ref
|=| bundles: Rail_4, Rail_7, Type:Sweep            |   <- second footer row for activity refs
```

**Facts encoded:**
- 1-leg discriminant -> hook (J) glyph
- leg direction -> footer arrow shape (Role->rail = Debit, rail->Role = Credit, Role<->rail = Variable) + parenthetical word; Variable also gets V pip
- XOR-member-ness -> X pip + nested in XOR sub-cluster framing
- reconciliation source -> footer 'T:<Tmpl>' or 'agg→<sweeperRail>' if covered only by aggregator
- uncovered single-leg (invalid) -> ! pip + footer reads 'T: NONE [!]'
- multi-role leg_role -> brace notation Role -> rail with {A,B,C}
- metadata_value_examples populated -> small '*' on glyph
- aging/magnitude/cadence/limit pips -> identical to TwoLegRail

**Rendering approach:** Same HTML-TABLE shape as TwoLegRail. The glyph cell switches to hook icon; the src-->dst footer line becomes the leg-direction sentence with parenthetical direction. Spine color uses a slightly different slate tone (warmer slate for single-leg, cooler slate for two-leg) so a pure 'count rails by type at glance' read works at zoom-out where the glyph is illegible. Data-id = rail__<name>.

### Rail Bundle (collapsed-parallel)

```
Small bundle (N=3, 2-leg):
+--------------------------------------------------+
|=| [F] bundle_3                       N=3         |   <- fan glyph (F), N badge prominent
|=|                                                |
|=| Src_A ==> Dst_B   (3 parallel rails)           |   <- double-arrow signals 'bundled'
|=| .  .  .                                        |   <- 3 dots = N member sparks
+--------------------------------------------------+

Medium bundle (N=7):
+--------------------------------------------------+
|=| [F] bundle_5                       N=7  +      |   <- '+' chip means 'expandable'
|=|                                                |
|=| Src_A ==> Dst_B   (7 parallel rails)           |
|=| . . . . . . .                                  |   <- 7 dots
+--------------------------------------------------+

Large bundle (N=29) — sparkline shows cumulative magnitude bar instead of individual dots:
+--------------------------------------------------+
|=| [F] bundle_11                      N=29 +      |
|=|                                                |
|=| Src_A ==> Dst_B   (29 parallel rails)          |
|=| amt: |####|########|####|##  agg p50/p95       |   <- aggregated magnitude band over N rails
+--------------------------------------------------+

Single-leg bundle (hook + leg-direction in footer):
|=| [F] bundle_18  hook                N=5 +      |
|=| Role_Y -> rail   (Debit x5)                   |
|=| . . . . .                                     |
```

**Facts encoded:**
- N -> N=<count> badge in title row (large/visible — operator's #1 fact)
- expandable affordance -> '+' chip when N>=4 (signals 'click to drill / popout sidebar')
- kind (twoleg vs singleleg) -> fan glyph stays but src-arrow-dst footer reads 'Src ==> Dst' (twoleg) vs 'Role -> rail (Direction xN)' (singleleg)
- individual members -> N dots in member-spark lane when N<=10; for N>10 the lane switches to an aggregated magnitude sparkbar across all members (so the box doesn't balloon)
- endpoint pair -> implicit from src/dst in footer
- aggregating members -> if any member rail has aggregating=true the title row gains an 'agg' chip (rare; usually agg rails are anchored by purpose but the type allows bundling)
- bundle stability -> bundle_<idx> id stays deterministic (per existing build_topology_graph_per_rail logic)

**Rendering approach:** shape=plaintext, HTML-TABLE same skeleton as Rail. The label DROPS the splatted member-name list (the current 'N rails:\n<n1>\n<n2>...' that balloons at N>=10) and replaces it with a fixed-height member-spark lane: N<=10 → N dot-cells; N>10 → aggregated magnitude bar. Data-id = rail__bundle_<idx>. CRITICAL gap-fix: the bundle's data-id today routes to 'un-filter all kinds' (the synthetic id isn't a Studio entity kind); the scorecard adds a HREF='#bundle-popout?bundle=<idx>' overlay on the '+' chip so click-to-expand opens a sidebar with the member list, while click on the box body still fires the focus filter on the bundle as a unit.

### Aggregating Rail (sweeper)

```
Aggregating TwoLegRail (Rail_81 in heavy: sweeps 29 rails):
+----------------------------------------------------+
|=| [O] Rail_81   orig:I  $$$  intraday-4h  agg N=29 |   <- clock-O glyph, 'agg' chip, N=29 sweep-fanin
|=|                                                  |
|=| InternalRole_02 ==> InternalRole_05              |
|=| sweeps: 29 rails  cadence: intraday-4h           |   <- dedicated sweep-info row
|=| amt: |####|######|####|##  swept p50/p95         |
+----------------------------------------------------+
   ^^ teal spine instead of slate so aggregators pop out
   from regular rails at zoom-out

Small aggregator (Rail_88: daily-eod, 2 rails):
+----------------------------------------------------+
|=| [O] Rail_88   orig:I  $$   daily-eod    agg N=2  |
|=| Src_A ==> Dst_B                                  |
|=| sweeps: Rail_12, Rail_64                         |   <- if N<=4, list members; else 'N rails (Types: Sweep)'
|=| amt: |##|####|##  p50/p95                        |
+----------------------------------------------------+

With ghost-edges feature on (optional add-on):
   Rail_12 ..............>  [Rail_88]
   Rail_64 ..............>  [Rail_88]
   (dotted teal edges from each bundled-activity ref to the aggregator,
    so the sweep relationship is VISIBLE not data-only)
```

**Facts encoded:**
- aggregating flag -> teal spine + clock-O glyph + 'agg' title chip (today completely invisible — biggest spike-gap fix)
- cadence -> chip on title row + restated in sweep-info row (intraday-Nh, daily-eod, daily-bod, weekly-<day>, monthly-eom/bom/<day>)
- fan-in count N -> 'N=<count>' badge in title row, same shape as bundle's N badge so the visual rhymes ('this rail consumes N things, like a bundle consumes N rails')
- bundled activity refs -> sweep-info row enumerates them when N<=4; else 'N rails (Types: <type-list>)'
- direction of sweep -> src ==> dst in standard footer slot
- convergence visualization -> opt-in dotted teal edges from each bundled-activity ref to the aggregator (configurable; off by default at L3 to control density)

**Rendering approach:** Same HTML-TABLE as Rail with teal spine color and clock glyph. The sweep-info row is a dedicated TR (not the magnitude bar) — when present it shifts the magnitude bar down by one row, so aggregators are visibly taller than regular rails. Optional ghost-edges: emit g.edge(src=member, dst=aggregator, style='dotted', color='#5fa8a0', constraint='false', arrowhead='odot') for each bundles_activity ref — gated behind a topology.py flag because at heavy density these add ~30 edges. Data-id = rail__<name>.

### TransferTemplate (cluster + inner node)

```
Default multi-leg template (5 leg-rails, 1 chain in, 1 chain out):
+======================================================+
|##| TransferTemplate_11   [tmpl]       L=5  F:2/wk    |   <- orange spine, [tmpl] chip, leg-count + cadence
|##|                                                   |
|##| key: txn_id,batch_id     completion: BDE+1d       |   <- composite key, completion expr
|##| chain: 2 in, 1 out       net=$0  XOR:0            |   <- chain in/out counts, expected_net, XOR-group count
|##| anchored:|########|##  5 anchored, 0 bundled      |   <- anchored/bundled split bar (always anchored=5 here since legs are anchored by membership)
+======================================================+
   |                                                  |
   |  port: Rail_12 (Debit)                           |
   |  port: Rail_13 (Credit)                          |
   |  port: Rail_14 (Credit)                          |
   |  port: Rail_15 (Variable, XOR g1)                |
   |  port: Rail_16 (Variable, XOR g1)                |
   +--------------------------------------------------+

The template renders as a SINGLE port-record glyph (no double-render); each leg-rail is
a port cell INSIDE the template's HTML-table, with chain edges docking at the port:

  (chain_parent_rail) ----chain---> [TransferTemplate_11:port_Rail_12]
  [TransferTemplate_11:port_Rail_16] ----chain---> (TransferTemplate_27)

Single-leg-rail template (TransferTemplate_01):
+======================================================+
|##| TransferTemplate_01   [tmpl]       L=1  F:--      |
|##| key: txn_id           completion: BDE             |
|##| chain: 0 in, 0 out    net=$0  XOR:0               |
|##| anchored:|##  1 anchored                          |
+======================================================+
   |  port: Rail_23 (Credit) [!]   <- ! = shared with TransferTemplate_13
   +--------------------------------------------------+
```

**Facts encoded:**
- cluster identity -> port-record glyph, NO double-render (matches v13.1.1 audit's port-node recommendation)
- transfer_key -> dedicated 'key:' line (fixes today's gap — key is in TopologyNode.metadata but unrendered)
- completion expression -> dedicated 'completion:' line (fixes today's gap — invisible)
- leg-rail count -> L=N chip on title row
- firings cadence -> F:N/period chip from firings_typical_per_period
- chain participation -> 'chain: N in, M out' line
- expected_net -> 'net=$0' inline
- XOR-group count -> 'XOR:N' inline; legs in XOR groups show '(Variable, XOR g<idx>)' in their port cell
- anchored/bundled split -> bar at bottom (informational — template legs are always anchored, but for parity with bundle visuals)
- shared-rail anomaly -> port cell gets ! marker + tooltip 'also in TransferTemplate_X' (fixes the seen_rail_ids race-condition gap)
- completion=metadata.<key> -> 'completion: meta:<key>' in italics to signal dynamic

**Rendering approach:** shape=plaintext, HTML-TABLE with header section (4 TR rows for title/key/chain/anchored-bar) AND a port section: one TR per leg-rail with TD PORT='<rail_safe_id>'. Chain edges use 'TransferTemplate_11:port_Rail_12' syntax — dot honors port targeting. Removes the inner component node entirely (matches the audit's CF.3.f recast). data-id = tmpl__<name> on the outer table; each port TD additionally carries HREF='#rail__<name>' so click on a port cell focuses the rail; click on the header (non-port area) focuses the template. This means the rails-as-port-cells inherit rail__<name> data-ids — they no longer need a separate ellipse node. Template_member edges go away entirely (cluster's port section IS the membership).

### TransferTemplate XOR-group (nested sub-cluster)

```
XOR group inside a template (TransferTemplate_FuzzXor, 3-way XOR):
+======================================================+
|##| TransferTemplate_FuzzXor  [tmpl]    L=4  F:1/d   |
|##| key: txn_id   completion: BDE                    |
|##| chain: 0 in, 0 out    net=$0  XOR:1              |
|##| anchored:|########  4 anchored                   |
+======================================================+
   |  port: RailA (Debit)                             |
   +--------------------------------------------------+
   ||  [XOR g1: exactly 1 of 3 fires]                ||   <- inset XOR band: indigo background, bold label
   ||  port: FuzzXorVarAuto      (Variable) X V      ||
   ||  port: FuzzXorVarStandard  (Variable) X V      ||
   ||  port: FuzzXorVarSlow      (Variable) X V      ||
   +--------------------------------------------------+

Mixed template (some XOR legs, some standalone):
+======================================================+
|##| TmplMixed   [tmpl]   L=5  F:--                   |
+======================================================+
   |  port: NormalLegA (Debit)                        |
   |  port: NormalLegB (Credit)                       |
   +--------------------------------------------------+
   ||  [XOR g1: exactly 1 of 2 fires]                ||
   ||  port: VarLeg1 (Variable) X V                  ||
   ||  port: VarLeg2 (Variable) X V                  ||
   +--------------------------------------------------+
   ||  [XOR g2: exactly 1 of 1 fires]                ||   <- validator allows multiple groups
   ||  port: VarLeg3 (Variable) X V                  ||
   +--------------------------------------------------+
```

**Facts encoded:**
- XOR group identity (gi) -> inset BAND row inside the template HTML-table with indigo background and 'XOR g<i+1>:' label
- member count -> 'exactly 1 of N fires' restated in the band label
- member rail names -> rendered as PORT cells inside the band's TBODY group
- Variable-direction semantics of members -> each port cell carries V pip
- X pip on member rails -> stamped on the rail-port row, so the XOR membership is readable when zooming into the port cell
- parent template -> implicit (XOR band is nested inside template's table)
- multiple XOR groups -> stacked bands inside the same table
- entity-routability -> band TR carries data-id='xor__<tmpl>__<gi>' (new addressable entity — fixes today's gap where XOR sub-cluster has no data-id)

**Rendering approach:** Nested TBODY group inside the template's HTML-TABLE. The XOR band is a TR with COLSPAN=N TD BGCOLOR='#e8eafa' (indigo tint) carrying the label, followed by one TR per member with PORT='<rail_safe_id>' — identical port mechanics as non-XOR legs, just visually banded. No nested graphviz subgraph cluster needed (the template is one glyph now). data-id on the band TD = xor__<tmpl>__<gi>; click filters Studio to 'show me the XOR contract'. The peripheral border of the band TR uses a faux-cluster style via CELLBORDER + a per-cell border-style override (dot's HTML-table dialect supports BORDER attr per cell).

### Chain (edge between rail/template)

```
Required chain (singleton child):
  [Rail_18] =====chain====>  [TransferTemplate_14:port_Rail_x]
              required
   ^^ solid black edge, label='chain (req)' above midline

XOR chain (multi children share parent):
  [Rail_22] =====chain====>  [Rail_23]
              xor 1 of 3
           \===chain====>  [Rail_24]
              xor 1 of 3
            \===chain====>  [Rail_25]
              xor 1 of 3
   ^^ all three edges colored amber, gathered visually at the parent
      via a small 'XOR-fan' glyph stamp on the parent's right edge

Fan-in chain (N parents -> 1 child Template):
  [Rail_30]  -.
  [Rail_31]   -.
  [Rail_32]    -=> [TransferTemplate_27:port_X]   ^funnel^
           expected: 5 parents      [N->1]
   ^^ thick edges (penwidth=2.0), double-arrow head ('onormalonormal'),
      label 'fan-in N->1' + 'exp:5' if expected_parent_count set
      Funnel glyph stamp on the target's left edge

Template -> Template chain:
  [TransferTemplate_11:port_Rail_16] ==chain==> [TransferTemplate_27:port_Rail_a]
              required
   ^^ docks at the EXACT port on both sides (operator reads which leg)
```

**Facts encoded:**
- required vs XOR cardinality -> edge color (black=required, amber=XOR) + label text 'required' vs 'xor 1 of N'
- fan-in -> edge weight (penwidth=2.0) + double-arrowhead + 'fan-in N->1' label suffix + 'exp:N' if expected_parent_count set + funnel glyph stamp on target
- expected_parent_count -> 'exp:N' label suffix
- XOR siblings as a group -> amber edges share an XOR-fan glyph stamp on the parent's right edge (the stamp itself is a single tiny svg glued to the parent's bounding box; the operator sees 'this rail has an XOR-chain departing')
- parent kind / child kind -> implicit from endpoint shape (port-record templates dock at named ports, rails are standalone glyphs)
- port-targeted -> chain edges go to TmplName:port_<rail> not the cluster — operator reads which leg the chain triggers
- data-id on chain row -> g.edge gets id='chain__<parent>__<sorted-children-csv>' so the edge is JS-addressable for entity-card routing (fixes today's gap)

**Rendering approach:** g.edge(parent_id, child_id, color=color_by_cardinality, penwidth=fan_in_weight, arrowhead=arrow_by_fanin, label=text, id='chain__...'). XOR-fan glyph + funnel glyph are emitted as TINY invisible-node-with-image-attr nodes positioned via pos= adjacent to the parent/child, OR more cheaply as a single Unicode character in the edge's label (less precise placement, but no extra nodes). data-id propagation: dot doesn't write edge ids into SVG title stream by default — workaround is to encode 'chain__<parent>__<csv>' as the edge's xlabel (which DOES land in SVG) and have diagram.js parse it; same shape as the audit's edge-kind sidecar proposal.

### Control-parent edge (subledger -> control)

```
Subledger Account -> control role:
  [SubAccount_A]  ........ctrl........>  [InternalRole_05 (CTRL)]
                  hierarchy, not flow
   ^^ purple double-line edge (visually distinct from chain's black/amber)
      label='ctrl' (5 chars; less visual noise than 'controls')
      arrowhead='oinv' (hollow inverted — signals roll-UP)

With parent carrying limits (today: '($ caps)' label):
  [SubAccount_A]  ........ctrl........>  [InternalRole_05 (CTRL) L L L L]
   ^^ no '($ caps)' label noise — the L pips on the control role's badge
      already carry the fact; edge stays clean

AccountTemplate -> control role (dashed source border conveys 'class'):
  [TemplateRole_02 (dashed)]  ......ctrl......>  [ConcMaster (CTRL)]

Chain-of-control (rare):
  [SubA] ..ctrl..> [MidCtrl] ..ctrl..> [TopCtrl]
   ^^ same purple double-line for both hops; visually a vertical hierarchy spine
      (group= attribute used to keep them rank-aligned)
```

**Facts encoded:**
- roll-up direction -> hollow inverted arrowhead (oinv) — visually distinct from chain's normal/onormalonormal arrows
- edge category -> purple double-line stroke (color='#6b4f9c', style='bold' to fake double-line in dot's vocabulary) — eliminates today's collision with chain's dashed-gray
- child kind -> implicit at source endpoint (Account=solid rounded box, AccountTemplate=dashed folder shape per their entries)
- parent has limits -> NOT on the edge (offloaded to L pips on the control role's badge) — keeps the edge clean
- fan-in on control side -> implicit from edge count converging on the (CTRL) role; the role's micro-bar shows degree so operator reads 'hot control account'
- structural-not-flow signal -> 'ctrl' (short) label + purple color + the consistent style say 'hierarchy' as a distinct visual register from rail-flow blue/yellow + chain black/amber

**Rendering approach:** g.edge(child_role, parent_role, color='#6b4f9c', style='bold', arrowhead='oinv', label='ctrl', id='ctrl__<child>__<parent>'). Style 'bold' is dot's closest native to a double-line; alternative is style='solid,bold' or two parallel edges (rejected — adds rank-noise). Edge id encoded via xlabel for JS pickup. Drop the '($ caps)' decoration entirely — the role badge carries it.

### LimitSchedule (cap, NOT rendered as node/edge today)

```
FIRST-CLASS BADGE on the control role + reciprocal mark on the rail:

Control role with 4 caps stacked (Outbound + Inbound mix):
+========================================+
|#| [#] InternalRole_05 (CTRL)           |
|#|                          [L:O$1M->Rail_a] |   <- one L chip per LimitSchedule entry
|#|                          [L:O$500k->Rail_b]|      O=Outbound, I=Inbound, magnitude shown
|#|                          [L:I$10k->Rail_c] |      target rail name
|#|                          [L:I$25k->Rail_d] |
|#| Concentration | EOD: $58M | parent:--|
|#| ##############|####|####  deg 31     |
+========================================+

Reciprocal indicator on the capped rail:
+----------------------------------------+
|=| [->] Rail_a   orig:I  $$  L:O$1M    |   <- L chip on rail says 'I have a $1M Outbound cap somewhere'
|=| Src --> Dst   key: k0  T:Tmpl3      |
|=| amt: |##|########|##  p50/p95       |
+----------------------------------------+

Multiple caps on same rail (different controls):
|=| L:O$1M(R5) L:I$25k(R8)               |   <- 'I'm capped by R5 outbound + R8 inbound'

Visual click-through (data-id):
  L chip on the control role has data-id='ls__<parent_role>__<rail>'
  L chip on the rail has the SAME data-id (entity-card pairs them)
```

**Facts encoded:**
- existence -> L chip is FIRST-CLASS not just a label decoration on an edge (fixes the today gap)
- direction -> O vs I prefix on each L chip
- cap amount -> $-formatted magnitude on the chip ($1M / $500k / $25k — short-form)
- target rail -> '->Rail_X' suffix on chip; on the rail itself, '(R<n>)' back-ref to the control role for symmetry
- count per parent -> N stacked L chips on the control role (degraded to 'L x N' counter if N>6 to avoid overflow)
- composite identity -> ls__<parent_role>__<rail> data-id on BOTH endpoints (control role chip + rail chip), so click on either pops the same entity card
- interaction with aggregators -> aggregator rail also gets its L chip if subject to a cap (the sweep itself throttled)
- interaction with chains -> independent fact; chain edge stays clean, cap fact reads from endpoint badges

**Rendering approach:** L chips are TD cells inside the role's and rail's HTML-TABLE labels — they're not separate graphviz nodes/edges. Each chip carries HREF='#ls__<parent>__<rail>'. The same chip text is generated server-side on both endpoints (deterministic by sorting LimitSchedule entries). No new nodes/edges in the graph = zero impact on dot's layout. The '($ caps)' decoration on control_parent edges is REMOVED. Trainer/coverage can stamp tint onto the L chip's BGCOLOR via data-id selector — the chip becomes a first-class tintable surface.

### Self-loop (single-leg rail on its own leg_role)

```
Debit self-loop where leg_role appears once:
  +-----------+               +-----------+
  | Role_X    | <----debit--- | [J] Rail  |
  +-----------+               +-----------+
         ^                          |
         |                          |
         +----<credit-back? no, self-loop is one direction--+
   ^^ rendered as a single edge with arrowhead pointing AWAY
      from the role (Debit) — the rail node itself shows '(Debit)'
      in its footer arrow-line, so the direction is doubly-encoded

Variable self-loop (XOR closing leg):
  +-----------+               +-----------+
  | Role_X    | <===variable=>| [J] FXVa  |   <- double-arrow indicates Variable
  +-----------+               +-----------+
                              | (Variable) X V

Bundled self-loop (N>=2 single-leg rails with same leg_role + direction):
  +-----------+              +---------------------+
  | Role_X    | <---debit--- | [F] bundle_singleleg|   <- fan glyph, N=N badge, same bundle treatment
  +-----------+   (xN)       | Role_X -> rail      |
                             | (Debit x5)          |
                             +---------------------+
```

**Facts encoded:**
- direction (Debit/Credit/Variable) -> footer arrow-line on the rail node ('Role_X -> rail (Debit)' etc), AND edge direction at the rail<->role line
- Variable closing-leg -> rail node shows V pip + footer reads '(Variable)' + the edge uses arrowhead='diamond' (visually distinct from Debit/Credit's open/normal arrows)
- single-leg-ness -> J hook glyph on the rail node
- reconciled by template vs aggregator -> footer T:<tmpl> or agg:<rail> on the rail node
- leg-role hotness -> the role node's fan-out micro-bar reflects the self-loop contribution (counts once per direction)
- bundling of self-loops -> if N>=2 single-leg rails share leg_role+direction, they collapse into a singleleg bundle and the edge becomes one bold edge with N annotation

**Rendering approach:** No special shape — the existing pattern of 'rail node + edge to leg_role' handles it. The improvement is purely encoding-level: direction is doubly-encoded (footer text on rail node + arrowhead on edge), Variable gets a distinct arrowhead='diamond'. data-id on rail__<name> already serves the click target; the edge itself doesn't need a data-id (focus on either endpoint suffices).

### Undeclared / orphan role (data-quality)

```
Undeclared role (LOUD — dotted red border + ? spine + ! pip):
.----------------------------------------.
:?: [?] RoleMissingDecl              ! ! :   <- ? glyph, ? spine, multiple ! pips for severity
:?:                                       :
:?: UNDECLARED  referenced by:           :   <- diagnostic in footer slot
:?:   Rail_44, Rail_67                   :
:?:                                       :
'----------------------------------------'
   ^^ entire box uses dotted red border (style='dotted', color='#c43a3a')
      + spine is alarm-red (no scope color — scope is unknown)

Orphan role (declared but unused — shown only in Layer-1 view today, badge-flagged):
+----------------------------------------+
|#| [#] InternalRole_Z       orphan      |   <- 'orphan' chip, regular spine but greyed
|#|                                      |
|#| Cash | EOD: $0 | parent: --          |
|#| ----  deg 0                           |
+----------------------------------------+
   ^^ rendered translucent (alpha 0.5) so the operator sees 'declared, but nothing wires to me'

Collision case (same role declared as BOTH Account and AccountTemplate — singleton wins):
+========================================+
|#| [#] CollidedRole [tmpl-shadowed] !   |   <- ! pip + 'tmpl-shadowed' chip
|#| Cash | EOD: $4.2M | parent: GLCtrl   |
|#| ##########|#####  deg 14             |
+========================================+
```

**Facts encoded:**
- undeclared -> entire shape recoded: ? glyph, ? spine, dotted-red border, ! pips, diagnostic footer listing referencing rails
- orphan (declared-unused) -> 'orphan' chip + translucent rendering + deg 0 — VISIBLE in the diagram (today filtered out at L2+); operator surface change requires unfiltering or layer-1-only visibility (defer to flag)
- Account+Template collision -> singleton renders with 'tmpl-shadowed' chip + ! pip; templated declaration's facts surfaced via tooltip
- severity -> ! pip count escalates (1 = warning, 2 = error, 3 = data-loss); spine color shifts from amber (warning) to red (error)
- click-routability -> data-id stays role__<name>; data-state='undeclared'/'orphan'/'shadowed' attribute added for trainer/coverage to filter

**Rendering approach:** Same HTML-TABLE skeleton as Role, with template-driven swaps: spine TD BGCOLOR = '#c43a3a' (red), glyph cell = '?', border style via wrapping cluster style='dotted', color='#c43a3a'. Orphan uses the regular Role shape but with FILLCOLOR alpha-blended (BGCOLOR='#ffffff80'). Collision uses the regular shape with an extra '[tmpl-shadowed]' chip. data-state attribute added at the SVG group level by post-processing the rendered SVG (graphviz HTML labels don't natively set arbitrary attributes — the JS layer reads from the HREF anchor on the wrapping cluster or from a sidecar emitted alongside role_meta).

## Rail collapse treatment

Bundled-parallel rails collapse into a single scorecard glyph that visually rhymes with Aggregating Rails (both 'one thing that stands for N things'). The bundle scorecard:\n\n  +--------------------------------------------------+\n  |=| [F] bundle_5                      N=7  +       |   <- plum spine, fan glyph, N=7 in big chip, + = expandable\n  |=|                                                |\n  |=| Src_A ==> Dst_B   (7 parallel rails)           |   <- double-arrow == signals 'bundled flow'\n  |=| . . . . . . .                                  |   <- 7 dots, one per member (N<=10)\n  +--------------------------------------------------+\n\nFor N>10 the dot lane switches to an aggregated magnitude sparkbar (so the box doesn't balloon — today's bug is that the label balloons at N>=10 by splatting all names). The '+' chip is a discrete affordance: clicking it opens a sidebar popout with the member list, instead of doing nothing useful (today the bundle's synthetic id un-filters all kinds). Clicking the body still fires focus on the bundle as a unit. Visual rhyme between bundle (plum, N=) and aggregator (teal, N=) helps the operator learn 'box with N= chip stands for many'; the color/glyph axis (fan vs clock) tells them which kind. Direction is implicit in the double-arrow ==; multi-leg vs single-leg shows in the src-arrow-dst sentence (Src ==> Dst vs Role -> rail). Endpoint edges from the bundle scale penwidth as min(1.0 + 0.3 * N, 3.0) — same as today, preserved so the bundle's 'thickness' on outgoing edges is a redundant cue.

## XOR group treatment

XOR groups render as an INSET BAND inside the parent template's port-record glyph — not as a separate nested subgraph cluster (which today fights for visual budget with the parent's chrome). The band:\n\n  +======================================================+\n  |##| TransferTemplate_FuzzXor  [tmpl]   L=4  F:1/d    |\n  |##| key: txn_id   completion: BDE                    |\n  |##| chain: 0 in, 0 out    net=$0  XOR:1              |\n  +======================================================+\n     |  port: NormalLeg (Debit)                          |\n     +---------------------------------------------------+\n     ||  XOR g1: exactly 1 of 3 fires                   ||   <- indigo BGCOLOR band, bold label, this is the XOR header\n     ||  port: FuzzXorVarAuto      (Variable) X V       ||   <- each member port carries X+V pips\n     ||  port: FuzzXorVarStandard  (Variable) X V       ||\n     ||  port: FuzzXorVarSlow      (Variable) X V       ||\n     +---------------------------------------------------+\n\nKey design moves:\n- The band has a distinct BGCOLOR (#e8eafa indigo tint) that contrasts with the template's orange chrome and the rail-port rows' default white — operator sees the band as a visual unit instantly.\n- The band header cell carries data-id='xor__<tmpl>__<gi>' (NEW — addressable entity; today the XOR sub-cluster has no data-id at all).\n- Member rails inside the band still carry rail__<name> data-ids on their port cells; the X pip on each port row redundantly encodes 'I'm in an XOR group' so the fact is still readable when zoomed into the port cell alone.\n- Multiple XOR groups in one template stack as multiple bands inside the same table — visually clear that they're separate contracts.\n- Variable direction (V pip) and XOR membership (X pip) are independent flags; a rail could be V without X (rare, would be flagged by validator) or X without V (impossible, validator enforces V on XOR members) — pips are independent so the future-flex is intact.\n- The band replaces today's nested graphviz subgraph cluster — saves one layout-cluster per XOR group (heavy fixture has 1 today; future fixtures with many would benefit more).

## Information density demo (Template + 5 rails + 2 chains)

```
Template with 5 leg-rails (3 normal + 2 in an XOR group) + 2 chains attached (1 in, 1 out):\n\n                                                                       +========================================+\n                                                                       |#| [#] InternalRole_05 (CTRL) L L L L  |\n                                                                       |#| Concentration | EOD: $58M | parent:- |\n                                                                       |#| ##############|####|####  deg 31     |\n                                                                       +========================================+\n                                                                                          ^\n                                                                                          : ctrl\n                                                                                          :\n  +--------------------------------------------------+                                    :\n  |=| [->] Rail_18  orig:I $$ 5/d        L           |  ---chain (req)--->   +============:===============================+\n  |=| Src_X --> Dst_Y  key:txn_id T:Tmpl11           |                       |##| TransferTemplate_11 [tmpl] L=5 F:2/wk |\n  |=| amt: |#|########|##  p50:$5k p95:$420k         |                       |##| key: txn_id,batch_id  completion: BDE+1d|\n  +--------------------------------------------------+                       |##| chain: 1 in, 1 out   net=$0  XOR:1     |\n                                                                             |##| anchored:|########|##  5 anchored      |\n                                                                             +========================================+\n                                                                                |  port: Rail_a (Debit)                |\n                                                                                |  port: Rail_b (Credit)               |\n                                                                                |  port: Rail_c (Credit)         L     |  <- Rail_c capped\n                                                                                +--------------------------------------+\n                                                                                || XOR g1: exactly 1 of 2 fires      ||  <- indigo band\n                                                                                || port: VarLeg1 (Variable) X V       ||\n                                                                                || port: VarLeg2 (Variable) X V       ||\n                                                                                +--------------------------------------+\n                                                                                          |\n                                                                                          | chain (req), docks at port: VarLeg1\n                                                                                          v\n                                                                       +--------------------------------------------------+\n                                                                       |=| [F] bundle_3                    N=4  +         |   <- downstream bundle\n                                                                       |=| Dst_Y ==> Dst_Z   (4 parallel rails)          |\n                                                                       |=| . . . .                                       |\n                                                                       +--------------------------------------------------+\n\nDensity read at a glance (no drill needed):\n- Template carries 5 leg-rails (L=5), fires ~2/wk (F:2/wk), key is composite (txn_id,batch_id), completion is delayed (BDE+1d), expected_net=0, 1 XOR group present.\n- Rail_18 (incoming chain parent): TwoLegRail, internal-initiated, $$ magnitude, 5/d cadence, subject to 1 limit cap (L pip), reconciles against Tmpl11, p50=$5k / p95=$420k typical.\n- Rail_c (template leg): the cap on the chain-input flows visually to this leg via the L pip on its port cell.\n- XOR group: 2 Variable-direction members, only 1 fires per Transfer, chain docks at VarLeg1 specifically (port-targeted).\n- Downstream bundle: 4 parallel rails Dst_Y->Dst_Z, expandable.\n- Control role: bears 4 limits, EOD $58M, fan-out degree 31 (above p95).\n\nFacts conveyed per shape (target was 2-3): control role=6, Rail_18=8, template=8, XOR band=3, port cells=3 each, bundle=4. The information density target is exceeded everywhere without label clutter — chips and pips do the heavy lifting; the footer rows hold the disambiguating identifier and the micro-bar is the only computed-from-instance fact.
```

## Consistency argument

Every shape is the same HTML-TABLE skeleton: spine TD (left, kind color) + glyph cell (subtype icon) + title row (name + chips + pips) + footer row (disambiguating identifier) + optional micro-bar row (quantitative fact). Across all 13 type entries the variation is in WHICH cells get populated and WHICH glyphs/chips appear — never the underlying grammar. The operator learns ONE shape-grammar (spine-glyph-title-chips-pips-footer-bar) and then applies it to roles, rails, templates, bundles, aggregators, XOR bands. Pips are universal single-letter status flags with stable meanings (L=limit, A=aging, X=XOR, V=variable, F=fan-in, !=quality). Chips are universal short-form attribute carriers (orig:I, $$, 5/d, L=5, F:2/wk, N=7, agg, tmpl, CTRL). Corner radius + border-pattern are independent universal axes (rounded=internal/owned, sharp=external/synthetic; solid=declared, dashed=templated, dotted-red=undeclared). Coverage tint paints on the spine TD across ALL shapes, never competing with the kind color or the fill. Click data-ids follow the prefix convention (role__, rail__, rail__bundle_, tmpl__, xor__, ls__, chain__, ctrl__) — every visible entity is addressable. The result is a vocabulary, not a collection: when the operator sees a new shape (say, a future entity type like LiquidityWindow or RegulatoryWatch), they already know which slot it'll use and which axis it'll vary on.

## Tradeoffs (honest cost)

- High implementation cost: HTML-TABLE labels are verbose to construct in topology.py; each entry shape needs a builder function (~10 of them). Estimate ~400-600 LOC net add for the label-builder layer plus ~50 LOC for the micro-bar p50/p95 quantile pre-pass.
- Layout footprint: scorecard glyphs are 2-4x the bbox of today's ellipses (~200x80px vs ~80x30px). On the heavy fixture (158 nodes) this likely means the canvas grows ~2x in both dimensions — even with the port-record template recast (which subtracts ~half the nodes) the per-node cost goes up. Operator gets per-shape density at the cost of canvas density. Cache covers the render-time, but viewport scrolling and zoom-out legibility need user testing.
- Micro-bar quantile dependency: the comparative p50/p95 ticks require a pre-pass over the L2 instance to compute distributions (fan-out degree distribution for roles, magnitude distribution for rails, etc.). This couples topology.py to a small statistics module — fine architecturally but adds a deterministic compute step before each render. For matview-state coverage tinting (future), the dependency extends to the database.
- Port-record template glyphs lock the template into a single visual unit — gone is the dashed cluster boundary that today gracefully fades behind member nodes. Operators used to the cluster idiom will need to re-learn. The shared-rail anomaly (Rail_23 in two templates) becomes a ! pip on the port cell rather than a visual smear across two clusters; arguably clearer, but loses the 'I'm in both' visual that today's overlapping clusters could in principle show (today they don't either — the seen_rail_ids race-condition emits one).
- JS-side click handling complexity: HTML-table labels split the click target (the audit notes this). Each cell carries its own HREF; diagram.js needs to read HREFs not just g.node[data-id]. The _stripIdPrefix + _parseEdgeTitle changes the v13.1.1 audit calls out as 'gated work' become mandatory. Coverage tint stamping needs to know which cell to paint per shape kind.
- Chip/pip overflow at extreme cardinalities: a role with >6 limits would overflow the badge slot; we fall back to 'L x N' counter, losing per-limit data-ids. Similar for a template with 15 leg-rails — the port section becomes tall. Soft caps acceptable, but edge cases lose information vs unrestricted.
- Glyph icon vocabulary: 8 distinct glyphs (square, folder, arrow-right, hook, fan, clock, ?, ribbon) need either a font/icon-font dependency or inline SVG. graphviz HTML labels don't render SVG inline cleanly — fallback is Unicode characters (■, 📁, →, ⮌, ✦, ⏱, ?, ▤) which differ across system fonts. Acceptable but breaks pixel-perfect rendering between server and client unless we ship a webfont.
- Quantitative scope of micro-bars is 'L2 declaration data', not 'live matview state'. The bars say 'this rail has typical magnitude band $5k-$420k' (from amount_typical_range), not 'this rail's last 30-day volume was $4.7M' (which would require matview reads). Closing that gap is the operator's stated future direction but is out of scope for first ship — risk that operators read the bars as live state when they're declarative.
- Color palette load: spine colors (blue/orange/slate/plum/teal/red) + chip backgrounds + indigo XOR band + pip colors = ~12 distinct colors needed. Accessibility / colorblind-safety must be checked; pips' single-letter labels are the fallback channel and remain readable in greyscale, but spine kind-identification degrades.
