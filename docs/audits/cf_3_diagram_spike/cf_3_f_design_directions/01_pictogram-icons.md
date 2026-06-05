# Direction 1/5 — pictogram-icons

**Pitch.** Every L2 entity becomes a labeled SF-Symbols-style silhouette card — a 24px glyph slot encodes the type, a fixed badge gutter encodes runtime attributes (caps, cadence, age-watch, origin), so the operator reads "what is it" from the icon and "how does it behave" from the gutter without ever parsing the title.

**Rendering complexity:** medium

## Shared visual primitives

- 24x24px monochrome SVG glyph in the top-left slot of every card (1.5px stroke, 2px corner radius, single-tone — matches SF Symbols family)
- Fixed-width 4-slot badge gutter along the card's top edge (cap, cadence, age-watch, origin) — empty slots render as faint dotted placeholders so the gutter geometry is identical across types
- Scope rail: 3px-wide colored stripe down the LEFT edge of every card (blue=internal, amber=external, red=undeclared, grey=structural) — reserved as the coverage-tint paint surface, never the fill
- 2px corner radius on every card (roles, rails, templates, bundles); chains and control_parent edges are the only non-card primitives
- Title font: 11pt sans, single line, truncated with ellipsis at 18 chars — predictable card width
- Subtitle row below title: 9pt sans, mid-grey, fact-encoding (e.g. "2-leg | Outbound", "singleleg Var", "sweep @ 4h") — type-specific but rendered in the same row geometry every time
- data-id sits on the outer <g> of every shape — HTML-table labels are avoided in favor of image= + label= so graphviz keeps one g.node per entity (audit's port-node concern stays addressable)
- Edges share a 2-axis style grammar: solid+arrow = money flow, dashed+open = chain (behavioral sequencing), dotted+diamond = control_parent (structural hierarchy) — the three edge kinds become visually orthogonal

## Vocabulary (per L2 type)

### Role (Account-scope, internal singleton)

```
+--+----+----+----+----+
|||$ |   |   |   |   |  <- badge gutter: $ filled (caps), 3 placeholders
|||--+----+----+----+|
|||                  |
|||  [BANK]          |  <- 24px building silhouette
|||  CashControl     |  <- title (11pt)
|||  internal | ctrl |  <- subtitle: scope | role-in-chart
|||  fan: 14 rails   |  <- connectivity hint (degree)
+--+------------------+
 ^
 blue stripe = internal scope
```

**Facts encoded:**
- scope → left stripe color (blue=internal)
- is-control-parent → "ctrl" tag in subtitle + bold building cornice on glyph
- carries LimitSchedule → $ badge in gutter slot 1, count as superscript
- fan-out degree → "fan: N rails" subtitle line (N=count of incident rail edges)
- expected_eod_balance set → small "=" tick in glyph base
- missing description → faint orange dot in title row (data-quality smell)
- is subledger (parent_role set) → upward chevron above building roof

**Rendering approach:** shape="none" with HTML <table> label: 1-row gutter (4 fixed cells, image= per cell or empty), 1 image cell for the 24px PNG building silhouette + 3 text rows. data-id set on outer g via id="role__<name>". Building PNG generated once into out/_glyphs/ at startup; image= references the file path. Subscript counts via <font point-size="7">.

### Role (Account-scope, external singleton)

```
+--+----+----+----+----+
||| . |   |   |   |   |
|||--+----+----+----+|
|||                  |
|||  [PHONE]         |  <- 24px handset/satellite glyph
|||  CustomerPay     |
|||  external        |
|||  fan: 3 rails    |
+--+------------------+
 ^
 amber stripe = external
```

**Facts encoded:**
- scope → amber left stripe
- external counterparty kind → phone-handset glyph (vs building for internal)
- fan-out degree
- is control parent (rare for external) → same cornice convention as internal
- balance-untracked status implicit via glyph choice (no "=" tick possible)

**Rendering approach:** Same HTML-table structure as internal, image= points to handset.png. Stripe color resolved at emit-time from role scope.

### Role (AccountTemplate-scope, internal templated)

```
+--+----+----+----+----+
|||$ |   |   |   |   |
|||--+----+----+----+|
|||                  |
|||  [BANK x3]       |  <- building glyph with stacked-cards drop shadow
|||  CustomerDDA*    |  <- asterisk suffix = templated
|||  internal templ  |
|||  parent: CashCtl |  <- rolls up to control parent
|||  fan: 22 rails   |
+--+------------------+
 ^
 blue stripe + DOUBLE inner border = templated (many-instances)
```

**Facts encoded:**
- templated vs singleton → stacked-cards drop shadow on the glyph (a "x3" stamp) AND a double inner border on the card
- scope → stripe color (heavy fixture's templates are all internal; external templates would carry the amber stripe with the same shadow)
- parent control role → "parent: <name>" subtitle line
- expected_eod_balance set → "=" tick on glyph
- custom instance_id_template/instance_name_template → tiny gear glyph in gutter slot 4
- fan-out degree across all instances

**Rendering approach:** Same HTML-table label, BUT the outer table has border="2" (vs border="1" for singletons) producing the double-edge. image= points to a building-with-shadow PNG variant; the "x3" stamp is baked into the PNG. data-id="role__<template_role_name>".

### Rail (TwoLegRail)

```
+--+----+----+----+----+
|||  | f |   | I |   |  <- gutter: cap empty, cadence empty, age empty, origin=I (InternalInit)
|||--+----+----+----+|
|||                  |
|||  [==>>]          |  <- 24px parallel-arrows glyph (TWO arrows = 2-leg)
|||  Rail_42         |
|||  2-leg | $$ med  |  <- magnitude band (typical_range → $/$$/$$$ scale)
|||  ~12/day         |  <- firings cadence
+--+------------------+
 ^
 grey stripe = structural (rail) — coverage tint paints HERE
```

**Facts encoded:**
- 2-leg discriminant → DOUBLE parallel arrows glyph (single-leg gets single arrow)
- direction → arrow heads always point right; flow direction inherited from edge geometry
- aggregating → sweep-clock glyph replaces parallel arrows (separate Aggregating Rail type)
- origin → letter badge in gutter slot 4 (I=InternalInitiated, F=ExternalForcePosted, A=ExternalAggregated, C=custom)
- anchoring state → SOLID border = anchored (template/chain member); DASHED border = standalone/bundleable
- magnitude band (amount_typical_range) → "$"/"$$"/"$$$" in subtitle
- firing cadence → "~N/day" or "~N/wk" in subtitle
- aging-watch (max_pending_age or max_unbundled_age set) → clock badge in gutter slot 3
- limit-subject (rail appears in any LimitSchedule.rail) → $-cap badge in gutter slot 1 with arrow direction (^ Outbound, v Inbound)
- template membership → drawn inside template cluster (positional)
- XOR membership → drawn inside XOR sub-cluster (positional)
- chain participation → chain edges dock at the card (no separate decoration on the rail itself)
- multi-role RoleExpression → "|2src" or "|2dst" tag in subtitle

**Rendering approach:** shape="none" + HTML <table>. image= for the 24px parallel-arrows PNG. Border style switches via outer table style attribute. Magnitude band computed at emit-time from amount_typical_range midpoint (log-bucket into 3 bins). data-id="rail__<rail_name>".

### Rail (SingleLegRail)

```
Debit:                Credit:               Variable (XOR closing):
+--+----+----+----+--+ +--+----+----+----+--+ +--+----+----+----+--+
|||  |   |   | I |   | |||  |   |   | I |   | |||  |   |   | C |   |
|||--+----+----+----+| |||--+----+----+----+| |||--+----+----+----+|
|||                  | |||                  | |||                  |
|||  [-->]           | |||  [<--]           | |||  [<-?->]         |  <- forked arrow
|||  Rail_07_dr      | |||  Rail_07_cr      | |||  FuzzXorAuto     |
|||  1-leg Dr | $    | |||  1-leg Cr | $    | |||  1-leg Var | $$  |
|||  ~3/day          | |||  ~3/day          | |||  ~1/wk           |
+--+------------------+ +--+------------------+ +--+------------------+
 yellow stripe = single-leg yellow stripe         yellow + red corner = Variable
```

**Facts encoded:**
- single-leg discriminant → SINGLE arrow glyph (Debit = right arrow, Credit = left arrow, Variable = forked/bidirectional arrow)
- leg direction → glyph variant + "Dr/Cr/Var" tag in subtitle
- is the variable closing leg of an XOR group → red corner notch on top-right of card + "Var" tag
- reconciliation source covered/uncovered → uncovered single-leg gets a red exclamation in title row (data-quality smell)
- all other rail facts (origin, aging, magnitude, cadence, limits, multi-role) → identical gutter+subtitle grammar as TwoLegRail
- anchoring (template/chain member vs standalone) → border style same as TwoLegRail

**Rendering approach:** Same HTML-table as TwoLegRail, three image= variants (single-right.png / single-left.png / single-fork.png). Yellow stripe on left edge (vs grey for twoleg) makes single/twoleg distinguishable BEFORE reading the glyph — redundant encoding by design. data-id="rail__<name>".

### Rail Bundle (collapsed-parallel)

```
+--+----+----+----+----+
|||  |   |   |   |   |   <- gutter empty (mixed contents collapse)
|||--+----+----+----+|
|||                  |
||| [==>>]\          |  <- parallel-arrows glyph with TWO drop-shadow copies behind
|||  [==>>]\         |
|||   [==>>]         |
|||  +14 rails       |  <- count badge replaces single name
|||  twoleg | $-$$$  |  <- magnitude range across members
|||  click: expand   |  <- hint for sidebar pop-out
+--+------------------+
 grey stripe (twoleg) OR yellow stripe (singleleg)
 DOTTED outer border (signals "this is a collection, not a single entity")
```

**Facts encoded:**
- N (collapsed count) → "+N rails" badge in subtitle (large, prominent)
- bundle kind (twoleg vs singleleg) → glyph variant + stripe color (matches the member kind)
- direction → arrow direction in glyph (mixed-direction bundles impossible by construction)
- magnitude range across members → "$-$$$" subtitle range
- expansion affordance → "click: expand" hint + dotted border AND triple-stacked drop-shadow glyph (3 overlapping copies signal "more behind")
- endpoint role pair → implicit from edges (same as individual rails)

**Rendering approach:** shape="none" + HTML <table> with border style="dotted". image= points to a pre-rendered stacked-arrows PNG (3 offset copies of the base glyph). data-id="rail__bundle_<idx>" — JS layer needs to learn this synthetic prefix and route bundle clicks to a sidebar pop-out listing member rails (today falls through to un-filter; that's the gap to close).

### Aggregating Rail (sweeper)

```
+--+----+----+----+----+
|||$ | f | C | I |   |  <- f=cadence filled, C=cap, I=origin
|||--+----+----+----+|
|||                  |
|||  [(O)~~>]        |  <- clock-with-flow glyph (sweep idiom)
|||  Rail_81 SWEEP   |
|||  2-leg @ 4h      |  <- cadence in subtitle
|||  bundles 29 rails|  <- activity-ref count
|||  $$$ heavy       |
+--+------------------+
 grey stripe; DASHED ghost arrows fan IN from each bundled-activity
 source rail (rendered as constraint=false ghost edges)
```

**Facts encoded:**
- is-aggregating → distinct CLOCK-WITH-FLOW glyph (not parallel-arrows) — instantly distinguishable
- cadence (intraday-Nh / daily-eod / weekly-X / monthly-Y / etc.) → in subtitle AND cadence badge in gutter slot 2 (small dot for intraday, half-moon for daily, full-moon for weekly, calendar for monthly)
- bundle scope (count of activity refs) → "bundles N rails" subtitle
- direction of sweep → glyph arrow direction + chain endpoint geometry
- activity-ref convergence → DASHED GHOST EDGES (low-opacity, constraint=false) from each bundled-activity rail INTO the sweep node — currently invisible, this is the headline gain
- single-leg vs two-leg → glyph variant (clock+single vs clock+double arrows)
- all standard rail facts via gutter+subtitle

**Rendering approach:** Same HTML-table card as Rail; image= points to clock-flow.png. Ghost-convergence edges emitted as a second pass: for each rail listed in bundles_activity, add edge with style="dashed", color="#a0a0a0", penwidth=0.5, arrowhead="open", constraint="false" so dot ignores them for layout. data-id="rail__<name>" (same as regular rail — the routing target is unchanged).

### TransferTemplate (cluster + inner node)

```
+============================================+
| [DOC] TransferTemplate_FuzzXor    +-------+|  <- folded-paper glyph + title in cluster header bar
| keys: txn_id,key_1   completion: BDE      ||  <- header subtitle
| 5 legs | XOR:1 | ~3/day                   ||  <- leg count, XOR groups, firings band
+============================================+
|  +----+ +----+                            |
|  |rail| |rail|   <- regular leg-rails (cards as defined above)
|  +----+ +----+                            |
|  +- XOR group 1 (exactly 1 fires) -------+|  <- nested sub-cluster
|  | +----+ +----+ +----+                  ||
|  | |Auto| |Std | |Slow|                  ||
|  | +----+ +----+ +----+                  ||
|  +-----------------------------------------+|
+--------------------------------------------+
 outer border SOLID orange (template chrome — distinct from rail cards)
 the [DOC] glyph + the header bar IS the click target (data-id on the cluster header node)
```

**Facts encoded:**
- cluster identity → solid orange chrome (vs dashed today; templates earn solid since they're load-bearing)
- template name → header title
- transfer_key fields → "keys: <csv>" header subtitle (currently invisible — headline gain)
- completion expression → "completion: BDE" / "BDE+2d" / "MEoM" / "meta.<key>" abbreviated tag in header (currently invisible — headline gain)
- expected_net → implicit (always 0; surface only if non-zero with a red "net≠0" tag)
- leg-rail count → "N legs" in header subtitle
- XOR group count → "XOR:N" in header subtitle (0 omitted)
- firings_typical_per_period band → "~N/day" in header subtitle
- chain participation → chain edges dock at the cluster header (data-id supports this); inner component node DEMOTED to invisible point
- shared rail anomaly → if a leg-rail's primary cluster ≠ this one, the rail card carries a red "shared" stamp in its title row

**Rendering approach:** Graphviz cluster (subgraph cluster_tmpl_<name>) with style="solid,rounded", color="#a6622c", penwidth=2. Cluster label uses HTML <table> with the folded-paper glyph (image=) + title + 2 subtitle lines. The OLD inner component node is demoted to shape="point", width="0.01", style="invis" but keeps data-id="tmpl__<name>" as the chain-edge dock. CF.3.f port-node compatibility: when port-node mode flips on later, the cluster collapses into a single Mrecord with per-leg ports — same data-id scheme survives. Chain edges target the invisible point (graphviz routes to cluster boundary).

### TransferTemplate XOR-group (nested sub-cluster)

```
+- XOR group 1 (exactly 1 fires) ---------+
|  [Y-FORK]                               |  <- 16px fork-glyph in sub-cluster header
|  3 alternates | all Variable            |  <- member count + Variable-direction confirmation
|                                         |
|  +----+  +----+  +----+                 |
|  |Auto|  |Std |  |Slow|  <- member rail cards (Variable variant w/ red corner)
|  +----+  +----+  +----+                 |
+-----------------------------------------+
 pale blue fill (distinct from parent template's orange chrome)
 dashed border ("contract" feel — runtime picks one)
 header has data-id="xor__<tmpl>__<gi>" — addressable for trainer/filter
```

**Facts encoded:**
- XOR-group identity within parent template → sub-cluster + gi index in label
- member count → "N alternates" in header
- Variable-direction enforcement → "all Variable" confirmation in header (validator already enforces; visual restates)
- exactly-1-fires invariant → header text + Y-fork glyph
- members visually grouped → all within the sub-cluster
- relationship to parent expected_net → implicit (parent template's header carries the net contract)

**Rendering approach:** subgraph cluster_tmpl_<tmpl>_xor_<gi> with style="dashed,rounded,filled", fill="#f0f4ff", color="#5a6f9c". Cluster label = HTML <table> with fork-glyph image= + 2 text lines. NEW: add an invisible point node inside the sub-cluster with data-id="xor__<tmpl>__<gi>" so the XOR group is addressable (today only members are). Member edges from template invisible-point to each rail use style="dashed", color="#5a6f9c" (matches sub-cluster border) — visually links XOR members to their parent.

### Chain (edge between rail/template)

```
Required (singleton):
  [card] ====>> [card]
         chain
         (required)

XOR (multi-child, sibling fanout):
  [card] ====+====>> [Auto]
             +====>> [Std]      <- single brace, 3 dashed edges sharing source
             +====>> [Slow]
         chain (xor 1-of-3)

Fan-in (N parents → 1 child template):
  [Rail_a] >====+
  [Rail_b] >====+====>> [TmplCard]
  [Rail_c] >====+   chain [fan-in 3->1]
              FUNNEL glyph at the child dock
```

**Facts encoded:**
- required vs XOR cardinality → SINGLE dashed arrow (required) vs BRACE/Y-MERGE with shared origin and one arrow per sibling (XOR) — visually different at-a-glance
- fan-in vs not → FUNNEL glyph (small triangle) docked at the child endpoint + label suffix [fan-in N→1] + penwidth=2.0 (kept from today)
- expected_parent_count → when set, the funnel glyph carries a number badge
- parent kind / child kind → endpoint shape conveys (cards are visually distinct rail vs template)
- behavioral vs structural → DASHED edge (chain = behavioral sequencing); contrast with DOTTED+diamond for control_parent (structural)
- crosses template-cluster boundary → no extra decoration (let dot reveal the long edge)
- XOR-chain-to-XOR-template-member → chain edge dock targets the XOR sub-cluster's invisible point, not the individual member

**Rendering approach:** Today's dashed edge stays, with two upgrades: (1) XOR chains render as N edges from parent to each child sharing source coordinates — graphviz handles this naturally when N edges declared with same src node; visual brace emerges from dot's bundling. (2) Fan-in funnel glyph emitted as a tiny invisible image-node spliced between parent and child (parent → funnel_<id> → child, both edges dashed); funnel_<id> uses image= for the triangle PNG. Edge id attr in SVG carries data-chain-id="<parent>::<children-csv>" for entity routing.

### Control-parent edge (subledger → control)

```
[subledger card]  .....<>....  [control card]
                  controls
                  ($ caps: 2 Out, 1 In)

  dotted line + diamond arrowhead at the parent
  visually DISTINCT from chain's dashed-line + normal arrowhead

With LimitSchedule(s) on the parent:
  [SubLedgerA] ....<>.... [CashControl + $-stack badge]
                                          ^
                                          $-badge stack on the control role's gutter slot 1
                                          (shows count + Out/In mix; cap magnitudes
                                          available on hover/click)
```

**Facts encoded:**
- roll-up direction → arrow points subledger → control
- child kind → endpoint shape (role-card variant: singleton vs templated visible immediately)
- parent carries LimitSchedule(s) → CAP BADGE STACK on the control role's gutter (NOT on the edge — moves the signal closer to the entity); edge label drops the "($ caps)" decoration
- fan-in count on control side → multiple control_parent edges visually converge — implicit; control card's "fan: N rails" subtitle now also gets "+M subledgers" line when M > 0
- behavioral vs structural distinction → DOTTED line + DIAMOND arrowhead (vs chain's dashed + open) — orthogonal edge grammar
- control-of-control (rare) → handled identically; multi-hop hierarchy emerges from chained edges

**Rendering approach:** Edge style="dotted", color="#888888", arrowhead="diamond", label="controls". The cap-count badge MOVES from edge label to the control role card's gutter (handled in Role rendering above). Edge id attr keeps data-control-parent="<sub>__<parent>" for routing. Visually orthogonal to chain edges (dashed+open) — operator no longer confuses hierarchy with sequencing.

### LimitSchedule (cap badge on role + rail)

```
Cap badges appear in TWO places (the cap exists at the intersection):

On the control role's gutter:
  +--+----+----+----+----+
  |||$2|   |   |   |   |   <- $2 = 2 caps total
  |||--+----+----+----+|
  ||| [BANK] CashCtl   |
  ||| ctrl | $cap: ^1v1|   <- 1 Outbound, 1 Inbound in subtitle
  +--+------------------+

On each capped rail's gutter:
  +--+----+----+----+----+
  |||^$|   |   |   |   |   <- ^$ = Outbound cap applies
  |||--+----+----+----+|
  ||| [==>>] Rail_42   |
  ||| 2-leg | $$ med   |
  ||| capped by CashCtl|   <- subtitle line names the parent
  +--+------------------+

No standalone shape — the cap IS the badge pair on (role, rail).
```

**Facts encoded:**
- existence → $ badge in gutter slot 1 on BOTH the parent role card AND each capped rail card (today: only edge label hint)
- cap count on parent → superscript number on the $ badge
- direction (Outbound ^ / Inbound v) → arrow modifier on the badge glyph
- specific rail subject → on each rail's card; subtitle line "capped by <parent>" names the relationship
- cap magnitude → not in the badge (badge is binary); on-hover tooltip via SVG title element; full magnitude in entity-card sidebar
- composite identity (parent_role::rail) → data-id="ls__<parent>__<rail>" stamped on the rail-side badge AND on the role-side badge (badges are addressable, not nodes)

**Rendering approach:** No new graphviz node. Cap badges are CELLS inside the HTML-table label gutter on Role and Rail cards. Each badge cell carries id="ls__<parent>__<rail>" — graphviz preserves <table> cell ids in SVG output, so JS can route badge clicks to the LimitSchedule entity card. Direction arrow + cap-count are baked into 4 small PNG variants (cap-out.png, cap-in.png, cap-out-multi.png, cap-in-multi.png). When a role has BOTH Out and In caps, slot 1 shows the multi-badge; subtitle disambiguates count + mix.

### Self-loop (single-leg rail on its own leg_role)

```
+--+----+----+----+----+         +--+------------+
|||  |   |   | I |   |    <----  |||LegRoleCard |
|||--+----+----+----+|           |||internal    |
|||                  |    ---->  +--+------------+
|||  [-->^]          |       ^
|||  Rail_loop       |       |
|||  1-leg Dr | $    |   self-loop = single-leg-rail card to single role-card,
|||  ~3/day          |   yellow edge (NOT a self-loop on the role; the rail node
+--+------------------+   is its own anchor — the loop is rail<->role)
 yellow stripe

The "loop" character is implicit in the role appearing on both sides of
the rail when the rail is single-leg. Drawn as TWO edges (rail->role + role->rail
for Variable direction) OR ONE directional edge (for Debit / Credit).
```

**Facts encoded:**
- direction (Debit / Credit / Variable) → encoded in the SingleLegRail card's glyph (single arrow / reverse arrow / fork) — already covered
- Variable closing-leg → red corner notch on rail card (already covered)
- leg_role hotness → degree subtitle on the role card
- reconciliation source → no separate visual; the chain or aggregating-rail relationship surfaces via chain edges / ghost-convergence edges
- edge color preserves today's yellow (#7f6000) for single-leg flows — distinguishes from twoleg blue at the edge level

**Rendering approach:** No new shape — self-loop is purely an edge property. Edge between rail card and role card with color="#7f6000", arrowhead="normal" for Debit/Credit, arrowhead="normalonormal" for Variable (bidirectional). The role card's degree count includes self-loops. data-id remains rail__<name> (the rail) and role__<name> (the role) — no new ids needed.

### Undeclared / orphan role (data-quality)

```
Undeclared (referenced by a rail, not declared anywhere):
+--+----+----+----+----+
|||  |   |   |   | ! |  <- red exclamation in gutter slot 4
|||--+----+----+----+|
|||                  |
|||  [?-BLDG]        |  <- building glyph with question mark overlay
|||  GhostRole       |
|||  UNDECLARED      |  <- subtitle in red
|||  refs: Rail_XX   |  <- which rail flagged this
+--+------------------+
 RED stripe (data-quality flag) — overrides scope color since scope unknown

Orphan (declared but unreferenced — Layer 1 view only):
+--+----+----+----+----+
|||  |   |   |   | o |  <- grey 'o' = orphan marker
|||--+----+----+----+|
|||                  |
|||  [BANK-fade]     |  <- building glyph at 50% opacity
|||  LonelyAccount   |
|||  orphan          |  <- subtitle in grey
+--+------------------+
 GREY stripe (no rail flow) + 50% opacity card
```

**Facts encoded:**
- data-quality flag → RED stripe (undeclared) or 50%-opacity card with GREY stripe (orphan)
- reason → subtitle text + glyph variant (question-mark overlay for undeclared, faded glyph for orphan)
- referencing rail → "refs: <rail_name>" subtitle line (undeclared only)
- data-id stays role__<name> with new data-state="undeclared"|"orphan" attribute → trainer/coverage can filter on it
- no scope info → red stripe replaces the would-be scope color

**Rendering approach:** Same HTML-table card. Stripe color resolves to red (undeclared) or grey (orphan) at emit-time when _scope_for_role returns None or referenced_roles excludes the role. Glyph PNG variants: building-question.png (undeclared) and building-faded.png (orphan). data-state attribute injected via id="role__<name>__undeclared" suffix OR a separate data-state attr if post-processing the SVG (audit confirms post-processing is on the table).

## Rail collapse treatment

Bundled-parallel rails collapse into ONE card with three visual cues: (1) the rail-glyph is TRIPLE-STACKED with offset drop shadows — three overlapping copies of the parallel-arrows (or single-arrow) silhouette visually shouting "there are more behind me"; (2) the outer card border becomes DOTTED (vs solid/dashed for individual rails — solid=anchored, dashed=standalone, dotted=collection); (3) a prominent "+N rails" badge replaces the title's role in the subtitle row, with the magnitude RANGE across members ($-$$$) instead of a single band. The triple-stack glyph is the at-a-glance signal — operator sees the layered icon and reads "stacked = many" without parsing the count. Click affordance is a "click: expand" hint at the bottom of the card; JS routes rail__bundle_<idx> to a sidebar pop-out listing every member rail with its own pictogram (the existing un-filter-fallback path is the gap to close). Bundle kind (twoleg vs singleleg) preserved via stripe color + glyph base (parallel-arrows vs single-arrow), so a "+18 rails" twoleg bundle is visually distinct from a "+12 rails" singleleg bundle without reading text.

ASCII:
```
INDIVIDUAL (anchored):       INDIVIDUAL (standalone, bundleable):    BUNDLE (collapsed):
+--+--+--+--+--+              +--+--+--+--+--+                       +-+--+--+--+--+--+
|||  |  |  | I|  |              |||  |  |  | I|  |                       |||  |  |  |  |  |
|||--+--+--+--+|              |||--+--+--+--+|                       |||--+--+--+--+|
|||            |              |||            |                       |||            |
|||  [==>>]   |              |||  [==>>]   |                       ||| [==>>]\    |
|||  Rail_42  |              |||  Rail_07  |                       |||  [==>>]\   |
|||  2-leg $$ |              |||  2-leg $  |                       |||   [==>>]  |
|||  ~12/day  |              |||  ~3/day   |                       |||  +14 rails|
+--+----------+              +--+----------+                       |||  twoleg $-$$$|
SOLID border                 DASHED border                         |||  click expand|
                                                                   +-+------------+
                                                                   DOTTED border
```

## XOR group treatment

XOR groups render as a nested sub-cluster INSIDE the parent template's cluster, with a small Y-FORK glyph (16px) in the sub-cluster header — the fork visually says "one path of N will be taken". The sub-cluster has pale blue fill (#f0f4ff) and dashed border (#5a6f9c), visually orthogonal to the parent template's solid orange chrome — operator's eye sees the color shift and reads "different contract here". The header carries "<N> alternates | all Variable" confirming the cardinality + Variable-direction enforcement. Member rail cards inside carry the Variable-direction glyph (forked arrow) AND a red corner notch on the top-right (the redundant encoding — operator reads "this is a Variable closing leg" from either the glyph OR the notch, whichever catches the eye first). NEW: the sub-cluster gets its own data-id (xor__<tmpl>__<gi>) via an invisible point node — today only the individual members are addressable. Chain edges targeting an XOR member's parent template route to the sub-cluster's invisible point instead of the individual member when the chain semantic is "the XOR group as a whole fires"; member-specific chain edges still target the individual rail.

ASCII:
```
+- [DOC] TransferTemplate_FuzzXor ------------------+
|  keys: txn_id   completion: BDE   3 legs | XOR:1   |
|                                                    |
|  +- [Y-FORK] XOR group 1 (exactly 1 fires) ------+ |
|  |  3 alternates | all Variable                   | |
|  |                                                 | |
|  |  +--+--+--+--+--+    +--+--+--+--+--+    +--+--+--+--+--+
|  |  |||  |  |  | C|  |    |||  |  |  | C|  |    |||  |  |  | C|  |
|  |  |||  [<-?->]    |    |||  [<-?->]    |    |||  [<-?->]    |
|  |  |||  Auto       *|   |||  Standard  *|   |||  Slow      *|  <- * = red corner (Var)
|  |  |||  1-leg Var $$|   |||  1-leg Var $$|   |||  1-leg Var $|
|  |  +-+-----------+ +    +-+-----------+ +    +-+-----------+
|  |                                                 |
|  +-------------------------------------------------+ |
|                                                    |
|  (other non-XOR leg-rails of the template here)    |
+----------------------------------------------------+
   ^ pale-blue sub-cluster vs orange parent chrome
```

## Information density demo (Template + 5 rails + 2 chains)

```
A TransferTemplate "DailyACHBatch" with 5 leg-rails + 2 chains attached (one incoming required chain, one outgoing XOR-3 chain to alternative settlement paths):

```
                                                                            +--+--+--+--+--+
                                                                            |||  |  |  | I|  |
                                                                            |||  [-->]      |
                                                                            |||  AlertRail  |
                                                                            |||  1-leg Dr $ |
                                                                            +--+-----------+
                                                                                  ^
                                                                                  | chain (required)
+--+--+--+--+--+                                                                  |
|||$1|  | C| I|  |    chain (required)             +============================ DailyACHBatch =====+
|||  [==>>]    |   ===========================>>>  | [DOC] DailyACHBatch                              |
|||  TriggerR  |                                   | keys: batch_id,run_date  completion: BDE+1d      |
|||  2-leg $$$ |                                   | 5 legs | XOR:0 | ~1/day                          |
|||  capped CashCtl|                              +==================================================+
+--+-----------+                                   |  +--+--+--+--+--+   +--+--+--+--+--+            |
                                                   |  |||  |  |  | I|  |   |||  |  |  | I|  |            |
                                                   |  |||  [==>>]    |   |||  [==>>]    |            |
                                                   |  |||  CustDebit |   |||  GLCredit  |            |
                                                   |  |||  2-leg $$  |   |||  2-leg $$  |            |
                                                   |  +--+-----------+   +--+-----------+            |
                                                   |                                                  |
                                                   |  +--+--+--+--+--+   +--+--+--+--+--+   +--+--+--+--+--+
                                                   |  |||  |C |  | A|  |   |||  |  | C| I|  |   |||  |  | C| F|  |
                                                   |  |||  [-->]     |   |||  [<--]     |   |||  [-->]     |
                                                   |  |||  FeeDebit  |   |||  RebateCr  |   |||  ExtPost   |
                                                   |  |||  1-leg Dr $|   |||  1-leg Cr $|   |||  1-leg Dr $|
                                                   |  +--+-----------+   +--+-----------+   +--+-----------+
                                                   +==================================================+
                                                                          ||
                                                                          || chain (xor 1-of-3)
                                                                          ||
                       +-----------+--------------+-----------+
                       |           |              |           |
                       v           v              v           v
                  +--+--+--+--+--+ +--+--+--+--+--+ +--+--+--+--+--+
                  |||  |  |  | I|  | |||  |  |  | I|  | |||  |  |  | I|  |
                  |||  [==>>]    | |||  [==>>]    | |||  [==>>]    |
                  |||  FastSettle| |||  StdSettle | |||  SlowSettle|
                  |||  2-leg $$$ | |||  2-leg $$  | |||  2-leg $   |
                  +--+-----------+ +--+-----------+ +--+-----------+

LEGEND OF FACTS READ-OFF (10+ facts visible without label-deep-dive):
  - DailyACHBatch's completion = "BDE+1d" → header subtitle (today invisible)
  - DailyACHBatch's transfer_key = "batch_id, run_date" → header subtitle (today invisible)
  - 5 leg-rails / 0 XOR groups / ~1 firing/day → header subtitle
  - TriggerR has a $-cap from CashCtl → gutter slot 1 + subtitle "capped CashCtl"
  - TriggerR has cadence f-badge + origin I → gutter slots 2 + 4
  - FeeDebit has cadence-C badge AND origin=A (ExternalAggregated) → gutter
  - ExtPost has origin F (ExternalForcePosted) → distinct from internal-initiated siblings
  - AlertRail is single-leg (yellow stripe + single-arrow glyph) → instantly visible
  - Incoming chain is "required" → single dashed arrow with label
  - Outgoing chain is XOR 1-of-3 → brace-fanout with three dashed siblings
  - All settlement alternatives are 2-leg + magnitude varies ($/$$/$$$) → at-a-glance comparison
```

Operator reads template behavior (completion, keys, firing-band), every rail's origin + cadence + cap + magnitude, AND the chain semantics, in one glance — no label spelunking. ~12 facts encoded across the cluster without a single visible word longer than 12 chars.
```

## Consistency argument

Every card — Role (singleton + templated), Rail (TwoLeg + SingleLeg), Bundle, Aggregating Rail, undeclared/orphan — shares the EXACT SAME geometry: 3px left stripe (scope/kind paint surface), 4-slot fixed-width badge gutter along the top, 24px glyph slot in the body, 11pt title, 9pt subtitle row(s). Templates wrap this card vocabulary in an outer cluster chrome (solid orange) and XOR groups nest one layer deeper (dashed pale-blue). The three edge kinds — money-flow (solid+arrow), chain (dashed+open), control_parent (dotted+diamond) — form an orthogonal 2-axis style grammar (line style × arrowhead) so an operator can instantly distinguish "behavioral sequencing" from "structural hierarchy" from "actual money movement". Glyph silhouettes within the family share stroke weight (1.5px), corner radius (2px), and monochrome tone — they read as one SF-Symbols-style set, not a clip-art collage. Badges in the gutter share a 4-slot grid with consistent semantic positions (slot 1 = cap, slot 2 = cadence, slot 3 = age-watch, slot 4 = origin) so reading any card across the diagram is the same eye-movement pattern. The redundant encodings (scope color AND glyph variant for role kind; stripe color AND glyph variant for rail leg-count) are intentional — operators recognize family membership from any one channel even when the others are obscured by coverage tints or focus dimming.

## Tradeoffs (honest cost)

- Card footprint is LARGER than today's bare ellipses/boxes — heavy fixture's 158-node diagram grows ~1.5-2x in node area; we trade canvas density for per-card information density. Mitigation: focus dimming + bundle collapse are now MORE valuable, not less.
- Requires a pre-generated PNG glyph asset library (~15 PNGs at startup); first-render adds a one-time generate-glyphs step. Glyphs need to be designed by a human (or an iteration loop with the operator) — generic clip-art will undermine the SF-Symbols-family feel. Asset versioning becomes a concern (glyph hash → cache invalidation).
- HTML <table> labels in graphviz are powerful but quirky — cell borders + image= + nested tables can render inconsistently across graphviz versions. Need a pin on graphviz version in the wasm-graphviz client side (audit already flags this concern); SVG output validation becomes a test target.
- data-id placement gets more complex — currently one data-id per node; now we want ids on table CELLS (for cap badges, for XOR sub-cluster headers) so JS routing must learn the new id prefixes (ls__<parent>__<rail>, xor__<tmpl>__<gi>). visible_entities_for + the Studio entity router both need updates. This is real new code, not just a CSS pass.
- Information OVERLOAD risk at small zoom — a 12-fact-per-card card is unreadable when zoomed out to fit a 158-node canvas. The fisheye/focus-dim treatment becomes a HARD requirement, not a nice-to-have. At-rest zoomed-out view will look like a forest of identical badge gutters; only the glyph silhouettes will carry signal at that scale.
- Bundle expand-on-click currently un-implemented (the existing code falls through to un-filter). Pictogram-icons direction REQUIRES building the sidebar pop-out for bundle membership — otherwise the triple-stack glyph promises an affordance the system can't deliver. Adds a JS-side feature to the scope of this redesign.
- Glyph design assumes an operator already grounded in banking — "building = internal" / "handset = external counterparty" / "parallel arrows = 2-leg" reads instantly to a financial-ops person but may confuse new operators on day-1. Acceptable tradeoff for the target audience (CPAs, recon analysts) but a real cold-start cost.
- Edge-grammar orthogonality (solid/dashed/dotted × normal/open/diamond) eats up the dashed+open combo that today's chain edges use; backward compat with existing operator muscle memory is broken. Worth it for the cognitive payoff but expect retraining.
- Templated-role visual cost: building-with-shadow + double-border + asterisk suffix is THREE redundant signals for one fact ("this is templated"). Defensible because templated vs singleton is the single most-confused distinction today, but it eats visual budget that could go elsewhere.
- QuickSight render parity is NOT a goal here — this is App2 / Studio-only. The QS dashboards don't render topology diagrams; the pictogram-icons direction has no QS parity story. If a future requirement drags topology into QS, this entire vocabulary needs reconsideration.
