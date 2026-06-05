# Direction 2/5 — monogram

**Pitch.** Every entity collapses to a 24×24pt typographic monogram — one bold capital letter wearing four corner slots and a left scope-stripe, so the eye scans glyphs like an alphabet instead of reading labels.

**Rendering complexity:** medium

## Shared visual primitives

- 24x24pt square baseline canvas — every entity occupies the same footprint regardless of identifier length
- One bold capital letter (24pt monospace, single character) as the body: R=Role, T=Template, ⇄=TwoLegRail, →=SingleLegRail, △=Aggregating, B=Bundle, X=XOR-group, $=LimitSchedule, U=Undeclared
- Four corner slots (TL/TR/BL/BR, 7pt) for fact badges — fixed semantics: TL=identifier-hash (4-char base36), TR=cardinality/count, BL=direction/state, BR=warnings/limits
- Left edge color-stripe (3pt wide) encodes scope/state: blue=internal, amber=external, slate-grey=structural-only, red=undeclared, purple=variable-direction
- Top edge accent-bar (2pt) encodes operational rhythm: solid=anchored, dashed=collapsible, dotted=ghost/derived
- Monospace typography family throughout (JetBrains Mono / SF Mono) — gives the 'monogram engraving' feel and makes hash codes legible at 7pt
- Tooltip is the canonical surface for full name + description; on-canvas hash badge (TL slot) is the persistent visual handle the operator learns to pattern-match
- Edges carry tiny inline glyphs at midpoint (¶ for chain, ⊂ for control-parent, ✕N for fan-in) rather than verbose word labels

## Vocabulary (per L2 type)

### Role (Account-scope)

```
  internal singleton                external singleton          control-parent w/ caps
  ┌──────────────┐                ┌──────────────┐            ┌──────────────┐
  │a3f2        ·│                │7c91        ·│            │d04e       3↓│
  │┃   R      ·│                │┃   R      ·│            │┃   R      $4│
  │┃          ·│                │┃          ·│            │┃          ·│
  │┃         ◯│                │┃         ◯│            │┃   ★      ◉│
  └──────────────┘                └──────────────┘            └──────────────┘
  blue stripe                      amber stripe                blue stripe +
  TL=hash  BR=hub-degree           hub-degree dot              TL=hash TR=fan-in
                                                               BR=caps$N  ★=ctrl-parent

  subledger (parent_role set)       w/ expected_eod_balance     undeclared (data-quality)
  ┌──────────────┐                ┌──────────────┐            ┌──────────────┐
  │1b22       ↑P│                │ff03        ≡│            │????       ⚠│
  │┃   R      ·│                │┃   R      ·│            │┊   U      ·│
  │┃          ·│                │┃          ·│            │┊          ·│
  │┃         ◯│                │┃         ◯│            │┊         ◯│
  └──────────────┘                └──────────────┘            └──────────────┘
  TR=↑P (rolls up)                BR=≡ (eod-bal declared)     red dotted stripe
                                                               ⚠ in TR slot
```

**Facts encoded:**
- scope (internal/external) → left stripe color (blue / amber)
- identifier → 4-char base36 hash in TL slot (collision-resistant for operator pattern-match; full name in tooltip)
- is-control-parent → ★ glyph centered on the body letter
- carries LimitSchedule → $N in BR slot (N = cap count); direction conveyed by ↓Outbound / ↑Inbound arrows in TR
- is-subledger → ↑P glyph in TR (P = parent's hash)
- expected_eod_balance present → ≡ glyph in BR
- connectivity degree → dot stack in right margin: · (1-3), ·· (4-9), ··· (10+); high-fan-out hubs read like dice
- undeclared → red stripe + dotted stripe pattern + ⚠ in TR slot + U letter instead of R

**Rendering approach:** shape=plain, label is an HTML <table border="0" cellpadding="0" cellspacing="0" width="32" height="32"> with 3 columns (stripe / body / margin) × 4 rows (top-accent / TL-TR / body / BL-BR). Stripe = <td bgcolor="#1f4e79" width="3"> spanning all rows. Body cell = <td><font face="JetBrainsMono" point-size="18"><b>R</b></font></td>. Corner badges = <font point-size="7">a3f2</font>. Outer container is shape=plain (no graphviz border) so the HTML table IS the chrome. data-id lives on the outer <g class="node"> via tooltip="role__<name>" + xlabel-free. Coverage tint paints the stripe column at runtime (CSS or post-process).

### Role (AccountTemplate-scope)

```
  internal template                  internal template w/ instance_id_template
  ┌╌╌╌╌╌╌╌╌╌╌╌╌╌╌┐                  ┌╌╌╌╌╌╌╌╌╌╌╌╌╌╌┐
  │e801      ×N│                  │a91c      ×N│
  │┃   R'     ·│                  │┃   R'    ·│
  │┃   ▒▒    ·│                  │┃   ▒▒    ·│
  │┃         ◯│                  │┃    ƒ    ◯│
  └╌╌╌╌╌╌╌╌╌╌╌╌╌╌┘                  └╌╌╌╌╌╌╌╌╌╌╌╌╌╌┘
  R' (R-prime) = template-role     ƒ = custom instance_*_template
  ▒▒ = halftone band               otherwise same vocab as Role
  dashed outer border = 'multiple
  instances at runtime'
  TR=×N (instance multiplier hint)
```

**Facts encoded:**
- templated → R' (R with prime mark) + dashed outer table border + ▒▒ halftone band below the letter (texture of 'many copies')
- scope → same stripe color (blue/amber); halftone overrides 'flat fill' so it doesn't drown the stripe
- parent_role → ↑P in TR slot when rolls up (same vocabulary as singleton subledger)
- expected_eod_balance → ≡ in BR (same)
- custom instance_id/name_template → ƒ glyph in body (right of letter); absent = no glyph
- rail-fan-out degree → dot stack in right margin (same as Role)
- TR ×N → 'instance multiplier' hint placeholder; numeric absent at L2 design time, operator reads as 'many'

**Rendering approach:** Identical HTML-table primitive as Role; differences: outer table border style='dashed' (graphviz HTML supports table BORDER but not dashed — emulate via dual-cell wrapper trick OR fall back to shape=plain wrapping a dashed <td>). The R' character uses Unicode U+0052 + U+02B9 modifier prime, or alternatively an SVG <text> inside an HTML cell rendered via image= for one-time asset. Halftone ▒▒ is just Unicode shade block U+2592. Tooltip + data-id same shape (role__<role_name>).

### Rail (TwoLegRail)

```
  standalone TwoLegRail              template leg-rail (anchored)        aggregating TwoLegRail
  ┌──────────────┐                  ┌──────────────┐                  ┌──────────────┐
  │7f0e      I→I│                  │b302      i↔i│                  │c4a1     ⏱4h│
  │┊  ⇄      $·│                  │┃  ⇄      $·│                  │┃  △     ×29│
  │┊         ◇│                  │┃         ◇│                  │┃  ⇄      ◇│
  └──────────────┘                  └──────────────┘                  └──────────────┘
  dashed top = collapsible          solid top = anchored               △ above ⇄ = aggregating
  TR=I→I (origin: Internal→Int)     in-template (port-dock style)      TR=⏱<cadence>
  BR=$· (no caps)                                                       BL=×29 bundled-activity

  multi-role fan-out (src=[A,B])    self-loop (src==dst, Rail_10)      w/ aging + magnitude band
  ┌──────────────┐                  ┌──────────────┐                  ┌──────────────┐
  │aa01     [2]→│                  │f00d       ⟲│                  │d12c     ⏰$M│
  │┊  ⇄      ·│                  │┊  ⇄      ·│                  │┊  ⇄      ·│
  │┊         ◇│                  │┊         ◇│                  │┊  ▰▰     ◇│
  └──────────────┘                  └──────────────┘                  └──────────────┘
  TR=[2]→ (2 admissible sources)    ⟲ glyph beside body                ⏰=aging-watch, $M=mag-band
                                                                       ▰▰ thickness = $-magnitude
```

**Facts encoded:**
- 2-leg discriminant → ⇄ body glyph (double-headed arrow, unmistakably two-sided)
- anchoring → top accent: solid bar=anchored (template/chain member), dashed bar=collapsible
- origin → TR slot 2-letter code: I→I, I→E, E→I, E→E, A→A (Aggregated); per-leg differ shows 'I→E' style
- magnitude band amount_typical_range → ▰▰ thickness bar in BL area (1 block=$1-100, 2=$100-10k, 3=$10k+); absent=blank
- firing cadence firings_typical_per_period → ◇ frequency dot in BR (single=rare, three=often)
- aging watches max_*_age set → ⏰ in TR override slot
- multi-role RoleExpression → [N]→ in TR (N=admissible source count) — says 'any of N roles' not 'N rails'
- self-loop (src==dst) → ⟲ glyph beside body letter
- subject-to-LimitSchedule → $· in BR upgrades to $N when N caps reference this rail; direction (Outbound/Inbound) via ↓/↑ prefix

**Rendering approach:** Same HTML-table primitive as Role/Template. Body cell carries the ⇄ Unicode glyph (U+21C4) at point-size=20. Stripe is replaced by a 3pt 'rail' color band: blue if both endpoints internal-scope, amber if external touches either side, gradient (dual <td> 1.5pt each) for cross-scope. Top accent <td height="2" bgcolor="#222"> when anchored; dashed via stippled GIF tile (image= fallback for the 2pt cell). The ▰ magnitude bar is a horizontal mini-bar in the BL cell composed of N filled rect glyphs.

### Rail (SingleLegRail)

```
  Debit single-leg                   Credit single-leg                  Variable (XOR closing leg)
  ┌──────────────┐                  ┌──────────────┐                  ┌──────────────┐
  │2a91      I→·│                  │b557      ·→I│                  │f7e2       ?±│
  │┊  →      $·│                  │┊  ←      $·│                  │┃  ⇆      $·│
  │┊  D      ◇│                  │┊  C      ◇│                  │┃  V      ◇│
  └──────────────┘                  └──────────────┘                  └──────────────┘
  → body glyph (out of role)        ← body glyph (into role)           ⇆ body glyph + purple stripe
  D letter under body =             C letter under body =              V letter under body =
  Debit direction                   Credit direction                   Variable
                                                                       BL=? mark = posting-time choice
                                                                       TR=± = sign undetermined
```

**Facts encoded:**
- 1-leg discriminant → → or ← body glyph (single-headed arrow) — visually distinct from ⇄ at-a-glance
- leg direction → body glyph rotation (→ Debit, ← Credit, ⇆ Variable) + sub-letter D/C/V under body
- Variable direction (XOR closing) → purple left stripe override + ⇆ body + V letter + ? in BL slot
- anchoring → same solid/dashed top accent as TwoLegRail
- origin (single Origin field) → TR slot, single 2-letter code
- leg_role multi-role → [N]→ same idiom as TwoLegRail
- aging / magnitude / cadence / limit-subject → same vocabulary slots as TwoLegRail
- reconciliation-source → not on-canvas; tooltip lists 'reconciled by tmpl X' or 'swept by rail Y'; uncovered (invalid) state lights red stripe + ⚠ in TR

**Rendering approach:** Same HTML-table primitive. Body is two-row: row1 = → (U+2192) / ← (U+2190) / ⇆ (U+21C6) at 16pt; row2 = capital D/C/V at 10pt. Purple stripe override for Variable = stripe <td bgcolor="#7a3fa6">. ⇆ for Variable visually 'wavers' (open arrows both sides) to telegraph indeterminacy.

### Rail Bundle (collapsed-parallel)

```
  small bundle (3 rails)             medium bundle (8)                  large bundle (17)
  ┌──────────────┐                  ┌──────────────┐                  ┌──────────────┐
  │bnd3       ×3│                  │bnd9       ×8│                  │bndA      ×17│
  │┊  ⇄      $·│                  │┊  ⇄≣     $·│                  │┊  ⇄▦     $·│
  │┊  R×3    ◇│                  │┊  R×8    ◇│                  │┊  R×17   ◇│
  └──────────────┘                  └──────────────┘                  └──────────────┘
  TR=×3 cardinality                 ⇄≣ = body + bundle-tail            ⇄▦ = body + heavy-bundle
  R×3 in body-sub = 'rails: 3'      (thicker stack)                    block (visually heavier)

  singleleg bundle (Debit, ×5)       expanded popout (hover/click)
  ┌──────────────┐                  ┌────────────────────────┐
  │bnd5       ×5│                  │bnd9 ⇄ — 8 rails ───────┤
  │┊  →≣     $·│                  │ ┌── Rail_12 7f0e         │
  │┊  R×5  D ◇│                  │ ├── Rail_19 b302         │
  └──────────────┘                  │ ├── Rail_27 c4a1   ... │
                                    └────────────────────────┘
  same body glyph as singleleg,    sidebar/popout (not graphviz)
  ≣ tail = bundle indicator        triggered by client-side click
```

**Facts encoded:**
- N (count) → ×N in TR slot (canonical cardinality position) + sub-letter row 'R×N' under body
- direction → preserved body glyph (⇄ / → / ←) — bundle inherits parent rail's direction discriminant
- bundle-tail glyph ≣ (U+2263) at small N, escalates to ▦ (U+25A6) at large N — operator can spot 'fat bundles' at a glance
- endpoint role pair → implicit from incident edges (no on-canvas label)
- edge penwidth scales 1.0 + 0.3·N capped at 3.0 (preserve existing visual), but now monogram body also carries the cardinality so penwidth isn't sole signal
- member list → tooltip lists sorted member names; on-click sidebar (client-side, NOT graphviz) opens the popout view
- synthetic id mapping → on-click popout offers per-member click-through that re-emits diagram with focus_node_id=rail__<member>, fixing the visible_entities_for un-filter-all degradation

**Rendering approach:** Same HTML-table primitive. data-id = rail__bundle_<idx>. Body glyph composes parent + tail: ⇄ + ≣ (just two HTML <td>s side-by-side). Sub-row 'R×N' is a small <td colspan=2 align=center><font point-size="8">R×N</font></td>. The on-hover/on-click popout is JS-side (not dot's domain) — graphviz emits the static glyph; the JS layer wires tooltip+popout. Bundle stripe color = neutral grey (#888) to telegraph 'synthetic/aggregate'.

### Aggregating Rail (sweeper)

```
  Rail_81 sweep (intraday-4h, 29)    Rail_88 sweep (daily-eod, 2)       single-leg sweeper
  ┌──────────────┐                  ┌──────────────┐                  ┌──────────────┐
  │c4a1     ⏱4h│                  │a302     ⏱1d│                  │d901     ⏱1w│
  │┃  △     ×29│                  │┃  △      ×2│                  │┃  △      ×4│
  │┃  ⇄      ◇│                  │┃  ⇄      ◇│                  │┃  →   D  ◇│
  │┃ ⤴⤴⤴   ◉│                  │┃  ⤴      ◉│                  │┃  ⤴      ◉│
  └──────────────┘                  └──────────────┘                  └──────────────┘
  △ above ⇄/→ = aggregating layer    ⏱<cadence> in TR (h=hour,         ⤴ tally = bundled-activity
  ⤴⤴⤴ tally below = sweep-inflow     d=day, w=week, m=month)            count (saturates at 3)
  density (3 = 'fat sweep')          BL=×N bundled count                BR=◉ filled circle =
                                                                       'consumes activity'
```

**Facts encoded:**
- is-aggregating → △ glyph stacked above the underlying rail body (⇄ for two-leg, → / ← for single-leg) — instantly distinguishes sweepers from normal rails (currently invisible)
- cadence → ⏱<unit> in TR: 4h, 1d, eod, 1w, 1m-eom, etc. (compact tokens; tooltip carries full CadenceExpression)
- bundle scope size → ×N in BL slot (number of bundles_activity refs)
- sweep-inflow density → ⤴ tally row at body-bottom: 1, 2, 3+ marks; visualizes 'small sweep vs fat sweep' at a glance
- BR ◉ filled circle = 'this rail consumes activity' (distinguishes from regular rails' ◇ frequency-dot)
- direction-of-sweep → preserved underlying-rail glyph (⇄ for two-leg, → for single-leg)
- activity-ref convergence → no edges emitted in static dot (operator decision: do not add ghost edges; clutter cost > clarity); on-click highlights bundled members via JS

**Rendering approach:** Same HTML-table primitive. Body becomes 3-row: △ (top, 12pt), ⇄/→ (mid, 14pt), ⤴ tally (bottom, 8pt). Stripe stays scope-blue. ⤴ tally is N glyphs (1-3) — saturated at 3+ to keep the cell compact. Tooltip = cadence-pretty + 'sweeps: Rail_12, Rail_64, …'. No ghost edges to bundles_activity members — operator-decided to keep static-canvas clean; expansion is JS-driven popout (mirrors Bundle behavior).

### TransferTemplate (cluster + inner node)

```
  simple template (1 leg)            multi-leg template (4 legs)        with completion + transfer_key
  ┌──────────────┐                  ┌──────────────┐                  ┌──────────────┐
  │t01a       ⊟1│                  │t14b       ⊟4│                  │t27c    eod+2│
  │┃   T   ⊞·│                  │┃   T   ⊞4│                  │┃   T   ⊞4│
  │┃         §│                  │┃   ‡    §│                  │┃   ‡‡   §│
  │┃         ·│                  │┃   ⊠1   ·│                  │┃        ·│
  └──────────────┘                  └──────────────┘                  └──────────────┘
  ⊟N = leg-rail count               ‡ = chain-participant marker      eod+2 in TR =
  ⊞N = firings_typical hint         ⊠1 = 1 XOR-group nested            completion expr (delayed 2d)
  § in BL = completion present     (count of XOR groups)              ‡‡ = participates as both
  brown/orange stripe + ▒▒                                              chain parent AND child
  halftone band

  template render REPLACES the cluster — single 24pt monogram, NOT a subgraph with rails inside.
  Leg-rails attach via tiny port-docks (rendered as edge anchors on the monogram's right margin).
  See port-dock detail in 'information_density_demo'.
```

**Facts encoded:**
- template identity → T body letter (unmistakable from R/R'/⇄/→/△/B/X)
- leg-rail count → ⊟N in TR slot
- presence + count of XOR groups → ⊠N in BL (filled square w/ X = exclusive-or)
- firings_typical_per_period → ⊞N below leg-count
- completion = business_day_end → § in BL (default; minimal mark)
- completion = +Nd / month_end / metadata.<key> → richer token: eod+2, eom, md.<k> in TR slot replacing default §
- is-chain-participant → ‡ glyph (single = parent OR child, double ‡‡ = both)
- transfer_key fields → tooltip-only (multi-key composite would clutter on-canvas slot), unless composite then 'k:N' badge in BR
- expected_net (typically 0) → not encoded on-canvas (universally 0; the rare non-zero shows in tooltip)
- shared-rail anomaly (Rail_23 in two templates) → ⚡ in BR slot to flag the operator
- RECAST: cluster is GONE — template IS the monogram. Leg-rail edges dock at port-anchors on right margin (see info_density_demo). XOR groups become a separate X-monogram entity adjacent (not nested cluster).

**Rendering approach:** Same HTML-table primitive. Body letter T at 18pt. RIGHT margin column carries N <td height="4" port="leg_<i>">·</td> rows (one per leg-rail); edges to leg-rails use 'tmpl__name':leg_<i> as endpoint. data-id on the outer <g class="node"> = tmpl__<name>; per-leg ports addressable via SVG path queries (tmpl__name + port suffix). Halftone ▒▒ band in body = 'composite entity, internal structure inside'. Stripe color = brown/orange #a6622c (preserves existing template chroma). Eliminates double-rendering: NO inner component node, NO subgraph cluster, NO template_member edges (those 58 edges in heavy fixture vanish — pairs well with v13.1.1 CF.3.f port-record proposal).

### TransferTemplate XOR-group (nested sub-cluster)

```
  XOR group (3 members, Auto/Std/Slow)        XOR group monogram + member dock
  ┌──────────────┐                            t11d ──────────⊠─── x01
  │x01e      ⊠3│                                          (port)
  │┃   X      ?│                              x01
  │┃   ⊠ ⊠ ⊠ V│   ← 3 ⊠ tiles = 3 members    ├─⇆── FuzzXorAuto      (V purple-stripe rails)
  │┃         ·│                                ├─⇆── FuzzXorStandard
  └──────────────┘                              └─⇆── FuzzXorSlow
  X body letter (distinct from T)              ⊠ port-dock on parent template
  ⊠N in TR = N members                         x__<tmpl>__<gi> stable data-id
  ? in BR = 'exactly 1 fires' contract        XOR is a SIBLING glyph to T,
  V in BL = members are Variable-direction    not a cluster around T
  purple stripe = XOR contract                (template gets ⊠N count badge,
                                               XOR-group gets its own monogram)
```

**Facts encoded:**
- XOR-group identity → X body letter (distinct from T template)
- parent-template index gi → tooltip ('XOR group 1 of TransferTemplate_FuzzXor')
- member count → ⊠N in TR slot
- 'exactly 1 fires per Transfer' → ? in BR slot (the question-mark IS the XOR semantic at-a-glance)
- members-are-Variable-direction → V in BL (matches Variable singleleg rail's V sub-letter)
- purple left stripe = 'XOR contract' (matches Variable rail stripe — operator learns purple = posting-time-determined)
- row of ⊠ tiles in body sub-cell visualizes cardinality (3 tiles for 3-way XOR)
- RECAST: XOR is a sibling monogram, not a nested cluster. Edges from X-monogram dock to its members (Variable singleleg rails).

**Rendering approach:** Same HTML-table primitive. data-id = xor__<tmpl_name>__<gi>. Body row composes X letter (top) + N ⊠ tiles (bottom) — tiles laid in a <td><font>⊠ ⊠ ⊠</font></td>. Port = right-margin docks for each Variable member rail. Parent template's monogram carries ⊠N badge (in its BL slot) advertising 'I have N XOR groups'; the X-monograms render adjacent in graphviz (no cluster constraint — let dot place them, they'll cluster naturally via short edges).

### Chain (edge between rail/template)

```
  required (singleton) chain          XOR chain (3 alternatives)         fan-in chain (N→1)
  ⇄ ────¶───→ ⇄                      ⇄ ────¶?──→ ⇄                    ⇄ ════¶✕5═══» T
         (rail→rail)                       │                              (rail→template)
                                            ├──→ ⇄                       penwidth=2.5
                                            └──→ →                        double-arrow »
                                       all 3 share parent                ¶✕5 = fan-in N=5
                                       only 1 fires per Transfer

  templated parent → templated child  expected_parent_count exact match
  T ─────¶───→ T                      ⇄ ════¶✕N=10═══» T  (! if mismatched)
  (cross-template edge)                                       ! suffix when count mismatches
  short edge — both monograms small,
  edge label microscopic but readable
```

**Facts encoded:**
- chain-edge discriminant → ¶ glyph at edge midpoint (single canonical marker; replaces verbose 'chain\n(required)' text)
- required (singleton) → bare ¶
- XOR chain (multi children) → ¶? at edge midpoint + alternative children radiate from same parent with shared visual bracket (SVG arc joining children, post-process)
- fan-in → ¶✕N at edge midpoint + double-arrow head » + penwidth=2.5
- expected_parent_count exact → ¶✕N=K with ! suffix when runtime mismatches (data-driven, runtime tint)
- parent kind / child kind → endpoint monogram shape conveys it (T vs ⇄ vs → vs X)
- long edge crossing template boundary → renders the same; edge length IS the smell (operators learn 'long ¶ edges = template-crossing chain')
- differentiation from control_parent edge → ¶ vs ⊂ midpoint glyph + different edge color/style

**Rendering approach:** Graphviz edge with attribute label='¶' (or '¶?', '¶✕N') and fontsize=10. color=#2a4d7a (chain blue; distinct from control_parent grey). style='solid' (NOT dashed — that's control_parent now). Fan-in uses penwidth=2.5 + arrowhead='vee'. XOR-chain sibling grouping post-rendered by JS via SVG arc connecting sibling child endpoints (graphviz can't natively render an arc-brace; client-side overlay). data-id on edge: chain__<parent>__<sorted-children-csv>; emitted as edge id attribute, queryable in SVG.

### Control-parent edge (subledger → control)

```
  Account subledger → control role     AccountTemplate → control          Control parent w/ caps (target side)
  R ─────⊂───⤳ R                      R' ─────⊂───⤳ R                   target gets $N+↑/↓ badge
  (gray dashed-dotted, distinct        templated source → singleton       (rendered on target Role's
   from chain's solid ¶)                control                            BR slot, not on edge)

  edge style: gray, dashed-dotted (── · ── ·)
  midpoint glyph: ⊂ (subset-of)
  arrowhead: open diamond ⤳ (vs chain's filled vee)
  NEVER confused with chain edges — three independent signals: color, style, glyph
```

**Facts encoded:**
- direction of roll-up → ⊂ midpoint glyph (set-membership) + open-diamond arrowhead at parent end
- child kind → source endpoint monogram shape (R for Account, R' for AccountTemplate)
- parent carries LimitSchedules → $N badge LIVES ON THE PARENT ROLE'S monogram (BR slot), NOT on this edge (decoupled — the cap is a fact about the role, the edge is just hierarchy)
- fan-in count (many subledgers → one control) → not on edge; parent role's connectivity-degree dot stack ◯ telegraphs it
- STRUCTURAL vs FLOW separation → triple signal (color gray, style dashed-dotted, glyph ⊂) leaves no chance of confusing with chain (solid blue ¶) or rail-flow (solid blue/amber, no glyph)

**Rendering approach:** Graphviz edge: style='dashed', color='#888888', arrowhead='odiamond', label='⊂', fontsize=10. Distinct from chain (solid + ¶ + vee) AND from rail edges (no label, plain arrowhead). data-id on edge: ctrl__<child>__<parent>. The dashed pattern was the historical collision risk with chain — chain moves to SOLID in this language, fixing the existing visual collision.

### LimitSchedule (cap, NOT rendered as node/edge today)

```
  Outbound cap badge on parent Role  Inbound cap badge on parent Role   Multiple caps stacked
  ┌──────────────┐                  ┌──────────────┐                  ┌──────────────┐
  │d04e       ·│                  │a921       ·│                  │e173       ·│
  │┃   R      ·│                  │┃   R      ·│                  │┃   R      ·│
  │┃          ·│                  │┃          ·│                  │┃          ·│
  │┃         $↓│                  │┃         $↑│                  │┃        $↕3│
  └──────────────┘                  └──────────────┘                  └──────────────┘
  $↓ = Outbound cap (default)        $↑ = Inbound cap                  $↕3 = mixed caps, N=3
  data-id = ls__<role>__<rail>      (AML/structuring direction)        click expands to popout
  click → cap-detail sidebar         red-tint if cap recently breached

  Cap-subject rail badge (on Rail monogram, BR slot)
  ┌──────────────┐
  │7f0e      I→I│
  │┊  ⇄      ⊘│ ← ⊘ glyph = 'capped' (this rail throttled by ≥1 LimitSchedule)
  │┊         ◇│   tooltip lists caps; click navigates to LimitSchedule monogram
  └──────────────┘

  Optional dedicated LimitSchedule monogram (for entity-card surface)
  ┌──────────────┐
  │ls01      $↓│
  │┃   $    →R│ → R = applies to rail R; arrow direction = $↓/$↑
  │┃   1M     ·│   1M = cap magnitude in body sub-row
  │┃         ·│
  └──────────────┘
```

**Facts encoded:**
- existence on parent_role → $↓ / $↑ / $↕N in role's BR slot (was: invisible decoration on edge label)
- direction (Outbound/Inbound) → ↓ vs ↑ arrow (matches role's TR scheme for limit-direction)
- cap count on this role → numeric suffix when >1: $↕3
- cap magnitude → tooltip (full $1,000.00); on-canvas body sub-row of dedicated monogram shows abbreviated magnitude ($1M, $10k)
- specific-rail applies-to → ⊘ glyph in subject rail's BR slot; click navigates to LimitSchedule detail
- composite identity (parent_role::rail) → data-id ls__<parent>__<rail>
- OPTIONAL dedicated $ monogram (if operator opts into 'LimitSchedule as first-class entity') — body letter $, otherwise badges-on-related-entities approach

**Rendering approach:** Two-tier: (1) DEFAULT — badges in BR slots of Role (cap-bearing) + Rail (cap-subject); zero extra graphviz nodes; data-ids ls__<role>__<rail> live as HTML cell attributes the JS layer queries. (2) OPTIONAL — emit a $-monogram node per LimitSchedule for explicit entity-card surface; styled identical to Role/Rail but body=$ and BL=abbreviated-cap-magnitude. Adds N nodes (6 in heavy). Operator-toggleable per L2-instance config; default OFF to keep heavy fixture clean.

### Self-loop (single-leg rail on its own leg_role)

```
  Debit self-loop                    Credit self-loop                   Variable self-loop
  ┌──────────────┐                  ┌──────────────┐                  ┌──────────────┐
  │f00d       ⟲│                  │e201       ⟲│                  │a4b9       ⟲│
  │┊  →      ·│                  │┊  ←      ·│                  │┃  ⇆      ·│
  │┊  D ⟳    ◇│                  │┊  C ⟳    ◇│                  │┃  V ⟳    ◇│
  └──────────────┘                  └──────────────┘                  └──────────────┘
  TR=⟲ marks self-loop              ⟲ both times (TR + body)            purple stripe + V
  ⟳ in body sub-row = recurrence    direction inherent in body         ⟳ + ? semantic merge
  No incident edge needed —          arrow (← vs → vs ⇆)
  the rail IS its own loop
  (saves the graphviz self-edge
   visual clutter)
```

**Facts encoded:**
- self-loop discriminant → ⟲ glyph in TR slot (overrides origin code) + ⟳ in body sub-row
- direction (Debit/Credit/Variable) → preserved via body glyph + sub-letter (same vocab as SingleLegRail)
- leg_role hotness → connectivity-dot stack on the rail's monogram
- ELIDED EDGE: the graphviz self-loop edge becomes optional — the ⟲ + ⟳ glyph pair says 'rail loops on its role' without rendering a curved self-edge (significant clutter reduction in heavy fixture where self-loops are common)
- click target → still rail__<name>; the elided self-edge is purely visual elision, identity unchanged

**Rendering approach:** Same HTML-table primitive (it IS a SingleLegRail with self==destination). Renderer detects self-loop in build_topology_graph_per_rail; emits the rail monogram WITHOUT a self-edge to its leg_role; adds ⟲ to TR cell + ⟳ to body sub-row. Saves N edges in heavy where self-loops are present. Tooltip surfaces the elided semantic: 'self-loop on role X'.

### Undeclared / orphan role (data-quality)

```
  undeclared (rail refs missing role)  orphan (declared, unreferenced)
  ┌╌╌╌╌╌╌╌╌╌╌╌╌╌╌┐                    ┌──────────────┐
  │????       ⚠│                    │c102       ✗│
  │┊   U      ·│                    │┃   R      ·│
  │┊  ⚠⚠     ·│                    │┃          ·│
  │┊         ◯│                    │┃         ◯│
  └╌╌╌╌╌╌╌╌╌╌╌╌╌╌┘                    └──────────────┘
  RED stripe (dotted)                  AMBER ✗ in TR = orphan
  U body letter (not R)                Otherwise identical to R
  ⚠ in TR slot                         (renders only at L1 view today)
  ⚠⚠ in body sub-row = severity
  4-char hash is '????' since
  identifier is just the ref string
  Tooltip: 'rail Rail_42 references
  role X — no Account/AccountTemplate
  declares X'
```

**Facts encoded:**
- undeclared discriminant → U body letter (replaces R), RED left stripe (overrides scope), dotted stripe pattern (data-quality smell)
- severity → ⚠⚠ in body sub-row (single ⚠ = warning, double = error)
- orphan discriminant → R body letter preserved, AMBER ✗ in TR slot (declared but unreferenced; renders only at L1 today)
- referring-rail (for undeclared) → tooltip + click navigates to the offender
- data-state attribute → SVG node carries data-state='undeclared' | 'orphan' for trainer/coverage CSS filters
- click target → role__<name> (same as healthy Role); data-state lets the JS layer route to a 'fix-this' UI flow

**Rendering approach:** Same HTML-table primitive. Undeclared: body=U, stripe=#c0392b dotted, ⚠ in TR, ⚠⚠ in body-sub-row. Orphan: body=R preserved, ✗ in TR (amber #c2823a), stripe preserved. Both carry data-state attribute on outer <g class="node"> via tooltip-encoded or comment-emitted dot attribute the JS post-processor lifts to a DOM attribute.

## Rail collapse treatment

Bundle is a first-class monogram entity (body letter B) with the parent rail's glyph + cardinality multiplier baked in:

  ┌──────────────┐         ┌──────────────┐         ┌──────────────┐
  │bnd3       ×3│         │bnd9       ×8│         │bndA      ×17│
  │┊  ⇄      $·│         │┊  ⇄≣     $·│         │┊  ⇄▦     $·│
  │┊  R×3    ◇│         │┊  R×8    ◇│         │┊  R×17   ◇│
  └──────────────┘         └──────────────┘         └──────────────┘
       small N=3                N=8 (≣ tail)             N=17 (▦ block)

The body composes parent-discriminant + bundle-tail:
  ⇄  → ⇄≣ (small)  → ⇄▦ (large)     twoleg bundles
  →  → →≣ (small)  → →▦ (large)     singleleg debit bundles
  ←  → ←≣ (small)  → ←▦ (large)     singleleg credit bundles

Three growth tiers, each readable at canvas-zoom:
  N=2-3   : no tail, ×N in TR slot only  (lightest)
  N=4-9   : ≣ tail (U+2263 three-bar)    (medium)
  N=10+   : ▦ block (U+25A6 grid)        (heavy)

Plus:
  - Edge penwidth still scales (1.0 + 0.3·N cap 3.0) — redundant signal, OK
  - Stripe color = neutral grey #888 (synthetic/aggregate, not scope-tinted)
  - data-id = rail__bundle_<idx> (preserved)
  - Click → JS popout sidebar lists members with their own 4-char hashes; per-member click navigates focus_node_id=rail__<member_name> (fixes the visible_entities_for un-filter-all degradation noted in taxonomy)
  - The 'R×N' sub-row inside the body cell makes cardinality readable even when zoomed out and the ×N corner badge is too small to parse

Operator scan: 'fat bundle' = ▦ block, ×17, thicker edge — three independent signals say 'major rail aggregation here' without reading any text.

## XOR group treatment

XOR-group is a SIBLING monogram (body letter X), not a nested cluster:

  Parent template carries a ⊠N count badge in its BL slot ('I have N XOR groups')
  The X-monogram(s) render adjacent to the template
  Edges from X dock to each Variable-direction member rail
  Purple left-stripe on X AND on members signals 'XOR contract / posting-time determined'

  ┌──────────────┐           ┌──────────────┐         ┌──────────────┐
  │t11d       ⊟3│           │x01e      ⊠3│         │f7e2       ?±│
  │┃   T   ⊞4│   ───⊠───   │┃   X      ?│ ──┬──── │┃   ⇆      ·│
  │┃   ‡    §│             │┃   ⊠ ⊠ ⊠ V│   ├──── │┃   V      ◇│
  │┃   ⊠3   ·│             │┃         ·│   └──── └──────────────┘
  └──────────────┘           └──────────────┘                ↑
   template               XOR-group sibling         Variable singleleg
   (with ⊠3 advertising)  (with 3 ⊠ tiles)         (purple stripe)

Signals stacked:
  - Body X letter — distinct from T, never confused
  - Purple left stripe — matches Variable singleleg rails' stripe; eye learns 'purple = posting-time choice'
  - ⊠N badge in TR — N-way alternation cardinality
  - ? in BR — 'exactly 1 fires' contract glyph (the question-mark IS the XOR semantic)
  - V letter in BL — members are Variable-direction (the closing-leg semantic)
  - Row of ⊠ tiles in body sub-row — visualizes cardinality (3 tiles = 3-way)

data-id = xor__<tmpl_name>__<gi> — unblocks entity-card click-through (currently no data-id on the XOR sub-cluster).

Why sibling instead of nested cluster: nested clusters in graphviz force rank constraints that fight the post-CF.3.a constraint=false template_member edges, AND clusters can't carry data-ids reliably (the spike audit's port-node finding). Lifting XOR to a peer monogram + dock-edges keeps the layout engine happy and gives the operator a dedicated click-target.

The template's ⊠N badge cross-references all sibling X-monograms (tooltip lists them); short edges between template and its XORs cluster them visually without explicit grouping.

## Information density demo (Template + 5 rails + 2 chains)

```
Template TransferTemplate_14 with 5 leg-rails + 2 chains attached (1 incoming, 1 outgoing):

  ┌──────────────┐       ┌──────────────┐
  │bb01       ·│       │a3e2       ·│
  │┃   R      ·│       │┃   R      ·│
  │┃          ·│       │┃          ·│           Source role A
  │┃         ◯│       │┃         ◯│           (subledger, internal)
  └──────┬───────┘       └──────┬───────┘
         │                       │
         │                       │
         ▼                       ▼
  ┌──────────────┬┐
  │t14a       ⊟5│├──◦ ┌──────────────┐  ⇄  leg_rail #1  (anchored, solid top accent)
  │┃   T   ⊞4│├──◦ ┌──────────────┐  ⇄  leg_rail #2
  │┃   ‡‡   §│├──◦ ┌──────────────┐  ⇄  leg_rail #3
  │┃   ⊠1   ·│├──◦ ┌──────────────┐  →  leg_rail #4 (singleleg Debit)
  │┃        ·│├──◦ ┌──────────────┐  ⇆  leg_rail #5 (XOR member, purple, Variable)
  └──────────────┴┘
   │  port column                             chain ¶ edges                  XOR-group sibling
   │  (right margin                                                          ┌──────────────┐
   │   has 5 leg-docks)                                                      │x14a      ⊠1│
   │                                                                         │┃   X      ?│ ──┐
   ▼                  ─────¶───→  ⇄ Rail_99 (chain child, required)         │┃   ⊠      V│   │ docks
  ⇄ Rail_18 ─────¶─→ T (this template is chain child of Rail_18 too)        │┃         ·│   │ to leg #5
                                                                              └──────────────┘   │ above
                       ←────⊂─── R sub-role                                                      │
                                                                                                  ┘

  Density readout (everything visible WITHOUT label clutter):
    [t14a]         template ID hash (tooltip = 'TransferTemplate_14')
    ⊟5             5 leg-rails (cardinality immediate; no need to count cluster members)
    ⊞4             firings_typical_per_period bucket = 4 (high-frequency template)
    §              completion = business_day_end (default; minimal mark)
    ‡‡             this template is BOTH a chain parent AND a chain child (two ‡)
    ⊠1             1 XOR-group present (cross-references the x14a sibling)
    T body         template discriminant (vs R/⇄/B/X)
    brown stripe   template chroma (preserves existing color identity)
    halftone band  'composite entity, has internal structure'
    5 port docks   right-margin ports — each chain/leg edge anchors AT THE EXACT LEG
                   (replaces today's 5 dotted membership edges + cluster-spanning chrome)

  Chain semantics readable from edges alone:
    ¶              chain edge midpoint glyph (vs ⊂ for control-parent — never confused)
    solid line     chain (vs dashed = control-parent)
    blue color     chain (vs gray = control-parent)
    » double-arrow if fan-in

  This entire neighborhood — 1 template + 5 leg-rails + 2 chain edges + 1 XOR group with members
  — fits in roughly 200pt × 200pt. The CURRENT renderer takes ~700pt × 400pt for the same
  neighborhood (cluster chrome + duplicated labels + 5 separate ellipse leg-rails).

  Each shape's right-margin port column gives chain edges deterministic dock points,
  killing the 'long edge raking the canvas' smell (chain edges now terminate at the leg port,
  not at a random spot on the cluster boundary).
```

## Consistency argument

Every entity in the vocabulary is a 24×24pt HTML-table monogram built from the SAME six primitives in the SAME positions:

  1. Body letter (1 bold capital + optional 1-letter sub) — the type discriminant. R/R'/T/X/B/U + glyphs ⇄/→/←/⇆/△/$ form a closed alphabet of ~12 marks; an operator memorizes them in one sitting.
  2. Left stripe (3pt color band) — scope/state. Blue=internal, amber=external, brown=template-bundle, gray=synthetic-aggregate, purple=variable/XOR-contract, red=undeclared. ONE color rule across all types.
  3. Top accent bar (2pt) — anchoring/operational state. Solid=anchored, dashed=collapsible, dotted=ghost/derived. Reads the same on every shape.
  4. Four corner slots (TL/TR/BL/BR, 7pt monospace) — FIXED semantics regardless of type: TL=4-char hash identifier, TR=cardinality/origin/timing, BL=count/direction-sub, BR=warnings/limits/frequency. An operator scanning a foreign shape already knows which corner says what.
  5. Right-margin port column (when applicable: Template, XOR-group, Bundle) — edge dock anchors with deterministic positions. Same primitive on every composite entity.
  6. Connectivity-dot stack in right margin (1-3 dots) — fan-out degree on every node-like entity. Same idiom on Role and Rail.

Edges share a parallel set of primitives:
  - Midpoint glyph: ¶ (chain) / ⊂ (control-parent) / [none] (rail-flow). Three distinct marks, never overloaded.
  - Color encodes relationship-kind: blue=chain, gray=control-parent, scope-color=rail-flow.
  - Style encodes substance: solid=behavioral, dashed=structural, dotted=derived/ghost.

Cross-type composition rules also hold:
  - The cardinality position (TR) means 'count of N for this entity' on every type — leg-rail count for Template (⊟N), member count for XOR-group (⊠N), bundle size for Bundle (×N), instance count for AccountTemplate (×N).
  - The state-override position (BR) means 'warnings + caps + frequency' uniformly: ⚠/$↓/$↑/$↕N/◇ all live there.
  - The body sub-letter slot (under main body) is always a single capital that REFINES the body letter: D/C/V under → for Direction; R×N under ⇄ for bundle cardinality echo; the body sub-row is the 'sub-discriminant' shelf.

This is not a collection of ad-hoc shapes — it is a typographic system. The same way handwritten monograms compose initials around shared chrome, every Recon entity composes 1 body + 1-2 corner badges + 1 stripe color from the SAME inventory. Adding a new L2 entity type (when SPEC grows) requires picking a body letter + assigning scope-color semantics — the badge slots and stripe and port-column are already defined.

## Tradeoffs (honest cost)

- Full entity names disappear from the canvas — operator must read tooltips for full identifiers. The 4-char hash badge (TL) is the persistent visual handle; operators trade name-recall for shape-recall. For an L2 author iterating on their own YAML this is fine (they wrote the names); for a CPA cold-reader it's a learning curve.
- Hash collisions inevitable at >36^4 = 1.6M entities — not a real risk for L2 instances (heaviest fixture is 158 nodes), but the hash function needs to be deterministic + collision-detection at emit time (assert + escalate to 5-char if collision found).
- Symbol/Unicode glyph rendering depends on font availability — JetBrains Mono / SF Mono carry the needed glyphs (⇄ ⊟ ⊠ ⊂ ¶ △ ⟲), but graphviz SVG embeds text as <text> elements, and SVG renderers need the same font installed. Mitigation: ship the font as a static asset in the dashboards/studio JS bundle; PDF audit (audit_chrome) needs the same font embedded.
- HTML-table labels in graphviz break some tooling (the spike audit's 'port-node notes' caveat) — data-id placement requires either a wrapping <g> element or a tooltip-bridge in the JS post-processor. Stable per-port data-ids on Templates need either graphviz port syntax (tmpl__name:leg_<i>) OR a JS coordinate-lookup; the simpler path is port syntax + verifying SVG output preserves it.
- Bundle cardinality '×N' tells operator N exists but NOT which rails — requires JS popout for the member list. This is a click-cost; the current implementation puts names directly in the label (no extra click, but the label balloons at N>=10). Net win at N>=4, slight regret at N=2-3 (where seeing both names would be cheap).
- Cap badges on Role + Rail BR slots create N visual duplications when one LimitSchedule applies to one (role, rail) pair — the operator sees $↓ on the role AND ⊘ on the rail; both glyphs say 'cap exists' but neither alone tells the full story. Tooltips must cross-link. Tradeoff: redundancy = double-discoverable; cost = mild glyph noise.
- Variable-direction purple stripe on rails + XOR-groups OVERLOADS purple — operator must learn 'purple means runtime-determined direction OR XOR contract' (the two are related but distinct). A new operator might assume purple = XOR only. Acceptable because the two concepts ARE coupled (XOR groups always contain Variable rails), but adds a learning beat.
- Edge midpoint glyphs (¶, ⊂) require font support at small sizes and may not render cleanly when the edge is short or crossing — degrades to color+style only in those cases. Acceptable: color (blue/gray) + style (solid/dashed) already disambiguate without the glyph.
- Self-loop edge elision saves canvas clutter but breaks the 'every relationship is an edge' mental model — operators expecting a self-loop curve will see only the rail monogram with ⟲ in TR. Tooltip + visual ⟲ are sufficient signals, but it IS a departure from graphviz norms.
- Hash badges (4-char) mean two different operator workflows: 'I know the name, find the shape' (filter by name in sidebar) vs 'I see the shape, what's its name' (hover tooltip). The current renderer optimizes the first via inline labels; monogram optimizes the second. Net win for dense diagrams (160+ nodes); net loss for tiny ones (sasquatch 44 nodes — labels were already legible).
