# Direction 4/5 — silhouette-geometric

**Pitch.** Each entity is one bold geometric primitive whose outline you recognize before you read the label — pills are accounts, chevrons are rails (with direction baked into the silhouette and thickness encoding bundle count), stacked-tabs are templates, hex-clocks are limit caps — all single-stroke, single-fill, no HTML or images.

**Rendering complexity:** low

## Shared visual primitives

- Single-stroke outline (no nested borders, no HTML tables) — silhouette is the type signal
- Fill color = scope: cornflower-blue #dbe9f6 internal, butter-yellow #fff2cc external, peach #fce4d6 template-child, pale-mint #e6f4e6 templated-role, white = degraded/undeclared
- Border treatment = anchoring state: solid 1.2pt = anchored/standalone, dashed = bundled-into-collapse, double-stroke = chain-participant, dotted = degraded/undeclared
- Diagonal-stripe fill overlay = aggregating/sweep behavior (the only fill texture in the language)
- Bottom-right corner reserved for a small '×N' multiplicity badge (bundle count, XOR-member count, fan-in count) — always same position, always plain text
- Top-left corner reserved for a one-glyph state mark: ⊕ = chain-participant, ⚠ = data-quality flag, § = limit-bearing, ⧖ = aggregating (rendered as a Unicode character inside the label, not an image)
- Direction is encoded into the shape itself (chevron tip, hex orientation) — arrowheads on edges become redundant confirmation, not the primary signal
- Every shape has a 'tint surface' (the fill region) reserved for coverage stamping; chrome (border, badges, glyphs) never overlaps it

## Vocabulary (per L2 type)

### Role (Account-scope, singleton)

```
  Internal singleton:           External singleton:          Control-parent (bears limits):
  .------------------.          .------------------.          .======================.
 ( InternalRole_02   )        ( ExternalRole_07    )        (§ ConcentrationMaster ×4)
  '------------------'          '------------------'          '======================'
      blue fill                    yellow fill                    blue fill + double border
                                                                  § = limit-bearing glyph
                                                                  ×4 = 4 subledgers roll up

  Subledger (parent_role set):   Undeclared (data-quality):
  .------------------.           .  .  .  .  .  .  .  .
 ( DDA_01            )         ( ⚠ GhostRole_xx       )
  '------------------'           '  '  '  '  '  '  '  '
  blue fill                       dotted border + warn glyph
  (rolls up via control edge)
```

**Facts encoded:**
- scope (internal=blue, external=yellow fill)
- control-parent (double-stroke border)
- limit-bearing (§ glyph top-left)
- subledger fan-in to control (×N badge bottom-right)
- data-quality state (dotted border + ⚠)
- name as label centered

**Rendering approach:** shape=ellipse, style='filled' (or 'filled,dashed' / 'filled,dotted' / 'filled,bold' for double-stroke). label=name with optional prefix glyph '§ ' or '⚠ ' and optional suffix ' ×N'. Single graphviz node, single data-id=role__<name>. peripheries=2 toggles the double border for control-parents. fillcolor branches on scope; color/penwidth branch on state. No HTML table, no record.

### Role (AccountTemplate-scope)

```
  Templated internal role:               Templated + parent control:
  .--------------------.                 .--------------------.
  | TemplateRole_02  » |                 | DDA_Subledger    » |
  '--------------------'                 '--------------------'
        mint fill                              mint fill
        » tab on right = 'many instances'     (control_parent edge dashed to parent)

  Templated external (rare):             Templated + chain-anchor:
  ,--------------------.                 .====================.
  | ExtTemplateRole »  |                 | TemplateRole_04 » ⊕|
  '--------------------'                 '===================='
        pale-yellow fill                       mint + double border + ⊕ glyph
```

**Facts encoded:**
- templated (rectangle with right-side tab '»' suffix in label — silhouette differs from singleton's ellipse)
- scope (mint = internal templated; pale-yellow = external templated — fixes the latent scope bug)
- control-parent target (double-stroke border)
- chain-anchor participation (⊕ glyph)
- fan-out degree (×N badge bottom-right when high)

**Rendering approach:** shape=box, style='filled,rounded'. The '»' suffix in the label is the always-present visual marker that this is a templated role (cheap and unambiguous vs ellipse). Single node, data-id=role__<role_name>. peripheries=2 for control-parent; penwidth and color branch on chain participation. fillcolor branches on scope (mint internal #e6f4e6 / pale-yellow #fff7d6 external).

### Rail (TwoLegRail)

```
  Standalone two-leg rail (direction baked in):
   ____________
   \           >    Rail_42
    >_________/
      chevron points src → dst

  Template-member two-leg rail (chain-participant, anchored):
   ============
   \⊕          >>   Rail_18
    >>========/
      double-stroke + ⊕ = chain participant

  Aggregating two-leg rail (sweep, daily-eod):
   ////////////
   \⧖  /  /   />   Rail_81  ×29
    >//////////
      diagonal-stripe fill + ⧖ glyph + ×N badge for bundles_activity count

  Self-loop two-leg (src==dst):
   ___________
   \         ↻>  Rail_10
    >________/
      curl glyph in label signals self-loop
```

**Facts encoded:**
- two-leg discriminant (chevron silhouette — distinct from single-leg's arrow-flag silhouette)
- direction (chevron tip orientation src→dst)
- anchoring (solid = standalone, dashed = bundle-eligible-but-individual, double-stroke = template/chain anchored)
- aggregating (⧖ + diagonal-stripe fill)
- bundles_activity count for aggregating (×N badge)
- self-loop (↻ glyph)
- name as label
- magnitude band (border thickness modulated 1.0pt / 1.5pt / 2.0pt by amount_typical_range tier)

**Rendering approach:** shape=cds (graphviz built-in chevron pointing right — a real silhouette, not text-art). style='filled' + optional ',diagonals' overlay for stripe; aggregating fillcolor pattern via fillcolor='/pattern9/' or a stripe via style='striped' with two-color fillcolor='#f5f5f5:#dcdcdc'. peripheries=2 for chain participants. penwidth branches on magnitude tier. Direction in the SVG is the cds shape's intrinsic right-pointing tip; if src is on the right in layout, dot rotates via orientation= or we set rankdir locally. data-id=rail__<name>.

### Rail (SingleLegRail)

```
  Debit single-leg (money OUT of leg_role):
      .-----.
     /       \
    < D Rail_31
     \       /
      '-----'
      pentagon-pointing-left (D in label = Debit)

  Credit single-leg (money IN to leg_role):
      .-----.
     /       \
      C Rail_44 >
     \       /
      '-----'
      pentagon-pointing-right (C = Credit)

  Variable single-leg (XOR closing leg):
      .-----.
     /       \
     < V Rail_X >    (double-tipped: direction-at-posting)
     \       /
      '-----'
      V = Variable, both-tipped silhouette

  Aggregating single-leg:
      .////.
     ///⧖ //\
    < D Rail_88 ×2
     \///////
      '----'
      stripe + ⧖ + ×N
```

**Facts encoded:**
- single-leg discriminant (pentagon/arrow-flag silhouette — distinct from two-leg chevron)
- direction (pentagon tip orientation: Debit=left, Credit=right, Variable=both-tipped diamond)
- explicit D/C/V letter prefix in label (redundant confirmation, accessible)
- anchoring (border style as elsewhere)
- aggregating (stripe + ⧖)
- bundles_activity count (×N)
- name as label

**Rendering approach:** shape=cds for Credit (right-pointing), shape=rarrow flipped (or shape=cds with orient=180) for Debit, shape=diamond for Variable. The pentagon/arrow-flag silhouette is graphviz's rarrow/larrow/pentagon primitives. label gets a single-letter prefix 'D '/'C '/'V ' as the textual confirmation. data-id=rail__<name>. Same fill/stripe/border logic as TwoLegRail; the SHAPE is the discriminant.

### Rail Bundle (collapsed-parallel)

```
  Two-leg bundle of 17 rails:
   ===============
   \\\           >>>>   bundle_4
    >>>=========///       ×17
   ===============
     three stacked chevrons (literal triple-stroke silhouette)
     border thickness scales with N (capped at penwidth=4)
     ×17 badge bottom-right
     NO member-name splat — expansion via click reveals sidebar

  Single-leg debit bundle of 4:
      .=====.
     //     \\
    << D bundle_7
     \\     //   ×4
      '====='
      stacked-pentagon silhouette

  Small bundle (N=2):
   _____________
   \\          >>
    >>________//   bundle_1 ×2
   thinner stack — reads as 'a few'
```

**Facts encoded:**
- N (bundle count) as ×N badge AND visual stack-depth
- type retained (chevron for twoleg, pentagon for singleleg) — bundle of X looks like X, just multiplied
- direction retained from member kind
- bundle-ness signaled by peripheries=2 or 3 (literal stacked silhouette)
- afford expansion-on-click — silhouette stack hints 'there are layers inside'
- no member-name splat (replaces today's tall ellipse)

**Rendering approach:** shape=cds (twoleg) or shape=rarrow/larrow (singleleg), peripheries=min(2 + N//6, 4), penwidth=min(1.5 + 0.2*N, 4.0). label is just the bundle synthetic name + ' ×N' suffix — NO member names. data-id=rail__bundle_<idx>. Click handler in JS reads peripheries-based bundle-ness via data-bundle-count attribute injected by server (graphviz preserves arbitrary id= so we add 'bundle_<idx>_<count>' for the client to parse, OR emit a sibling <title> with the count for hover preview).

### Aggregating Rail (sweeper)

```
  Aggregating two-leg, intraday-4h cadence, bundles 29:
   ////////////////
   \⧖ / / / / / />>>>   Rail_81
    >//////////////       ×29  (intraday-4h)
   ////////////////
     diagonal stripe fill = sweep texture
     ⧖ (clock-like glyph) top-left = aggregating
     ×N badge = bundles_activity count
     cadence label suffix in parens
     border = template/chain anchored if applicable

  Aggregating single-leg, daily-eod, bundles 2:
      ./////.
     ///⧖ ////\
    < D Rail_88
     \////////    ×2 (daily-eod)
      '////''
```

**Facts encoded:**
- is-aggregating (diagonal-stripe fill + ⧖ glyph — the spike's most-obvious missing visual)
- cadence (parenthetical suffix in label — 'intraday-4h', 'daily-eod', etc.)
- bundles_activity count (×N badge)
- underlying rail kind (chevron / pentagon silhouette retained)
- direction (silhouette tip retained)

**Rendering approach:** Same shape= as the underlying rail kind. style='filled,diagonals' or fillcolor with a striped pattern (graphviz supports style='striped' with multi-color fillcolor). ⧖ glyph prepended in label; cadence appended in parens; ×N appended. data-id=rail__<name>. No new shape primitive needed — the stripe IS the aggregating signal, layered on top of the existing rail silhouette.

### TransferTemplate (cluster + inner node)

```
  TransferTemplate as 'stacked-tabs' silhouette (replaces cluster+component duo):
   .--.
   |  |---------------------.
   '--+                      \
      | TransferTemplate_13  |
      | key: customer_id     |   ⊕
      | completion: bday_end |
      | legs: 4, xor: 0      |
      '----------------------'
         peach fill, top-left tab silhouette = 'this groups N rails'
         ⊕ glyph if chain participant
         metadata baked into label rows (transfer_key, completion, leg count)

  Template with XOR group inside:
   .--.
   |  |---------------------.
   '--+                      \
      | TransferTemplate_X   |
      | key: txn_key         |
      | completion: month_end|
      | legs: 3, xor: 1×3    |
      '----------------------'
         xor: 1×3 = one XOR group of 3 members — see brace overlay

  Template as chain parent + chain child:
   .--.
   |  |---------------------.
   '--+                      \
      | TransferTemplate_11 ⊕|
      | key: deposit_id      |
      | completion: bday_end+2d|
      | legs: 2              |
      '----------------------'
         ⊕ glyph = chain participant
```

**Facts encoded:**
- template-ness (folded-tab silhouette — distinct from role pill, rail chevron, cap hex)
- transfer_key fields (label row)
- completion expression (label row — currently invisible)
- leg-rail count (label row)
- XOR group count and member sizes (label row 'xor: N×M')
- chain participation (⊕ glyph)
- fillcolor stays peach #fce4d6 (matches today's template-child palette)

**Rendering approach:** shape=folder (graphviz built-in 'folded-tab' silhouette — perfect for 'this groups other things'). HTML-LIKE label NOT used; instead use \n-separated lines in a plain label to keep one data-id. fillcolor=#fce4d6. Single node, data-id=tmpl__<name>. The leg-rails STILL render as separate rail nodes connected by dotted membership edges (unchanged from today) — the cluster chrome is REPLACED by this single folder node, eliminating the double-render. Cluster removed entirely; template-membership conveyed by dotted edges only.

### TransferTemplate XOR-group (nested sub-cluster)

```
  XOR group as brace-bracket around its member rails:

  .--.
  |  |---------------------.
  '--+ TransferTemplate_X   |
     | legs: 5, xor: 1×3    |
     '----------------------'
         |       |       |
         v       v       v
         /  .---. .---. .---. \
        |  < V_1 > < V_2 > < V_3 > |     XOR brace ×3
         \  '---' '---' '---' /        'exactly 1 fires'
          ----------v----------
                  brace silhouette
                  (drawn as a single curly-brace shape
                   spanning the XOR members)

  Single-member edge case impossible (validator requires ≥2).
```

**Facts encoded:**
- XOR-group identity (brace silhouette wrapping members — distinct from any other framing)
- member count (×N on the brace)
- 'exactly 1 fires' contract (brace label)
- members typically Variable-direction (their pentagon silhouettes show V)
- parent template association (brace anchored to template's tab silhouette via dotted edge)

**Rendering approach:** shape=cds with style='dashed' rotated 90deg as a brace surrogate — OR a tiny invisible subgraph cluster_xor_<tmpl>_<gi> with style='dashed' rounded edges, label='×N exactly-1', fillcolor=transparent. Since we're killing the template cluster, we KEEP a thin XOR subcluster (the only remaining cluster in the language). data-id on cluster via id='xor__<tmpl>__<gi>' attribute. Members keep their individual rail data-ids. Cluster border is the brace, no fill (fillcolor='none' to avoid competing with member tint surface).

### Chain (edge between rail/template)

```
  Required chain (singleton child):
   parent --⚭⚭⚭⚭⚭→ child       (chain-link motif as edge label)
            chain (req)

  XOR chain (multi children):
            .--⚭⚭⚭⚭→ child_A
   parent --|--⚭⚭⚭⚭→ child_B    (forked, all XOR-style)
            '--⚭⚭⚭⚭→ child_C
            chain (xor ×3)

  Fan-in chain (N parents → 1 child):
   parent_1 --⚭⚭⚭⚭⛮→ child       (funnel glyph ⛮ at target)
   parent_2 --⚭⚭⚭⚭⛮→          chain (fan-in N→1)

  Edge visual: solid line + chain-link motif label '⚭⚭' + arrowhead='normal'
  Color: charcoal #2a2a2a (distinct from control-parent's gray dashed)
```

**Facts encoded:**
- chain semantic (chain-link ⚭ motif in label — distinct from any other edge)
- required vs XOR (label '(req)' vs '(xor ×N)')
- fan-in (funnel ⛮ glyph at arrowhead + 'fan-in N→1' suffix)
- parent kind / child kind (endpoint shapes do the work)
- expected_parent_count (numeric suffix when set)

**Rendering approach:** edge style='solid' (NOT dashed — reserving dashed for control-parent), color='#2a2a2a', penwidth=1.5 (or 2.5 for fan-in). label='⚭⚭ chain (req)' / '⚭⚭ chain (xor ×3)' / '⚭⚭ chain (fan-in 4→1)'. arrowhead='normal' default, 'onormalonormal' for fan-in (kept from today). Per-edge id='chain__<parent>__<sorted-children-csv>' so JS can route entity clicks. Solid+chainlink-motif is the discriminant from control-parent's dashed+'controls'.

### Control-parent edge (subledger → control)

```
  Subledger → control parent (structural, NOT flow):
   subledger ......⌖......▷ parent_role    controls
            (dashed line + ⌖ hierarchy glyph + open arrowhead)

  Control parent that carries limits:
   subledger ......⌖......▷ parent_role    controls §×2
            (§×N suffix indicates limit count on parent)

  Distinct from chain: chain is SOLID + ⚭ motif; control is DASHED + ⌖ motif.
```

**Facts encoded:**
- roll-up semantic (⌖ hierarchy glyph — distinct from ⚭ chain glyph)
- child kind (source endpoint shape does the work)
- parent carries limits (§×N suffix)
- structural-not-flow (dashed line is the structural signal)
- fan-in count on control (handled by the control role's ×N badge, not on the edge)

**Rendering approach:** edge style='dashed', color='#888888' (kept), arrowhead='onormal' (kept), label='⌖ controls' or '⌖ controls §×N'. The ⌖ motif in the label + the DASHED line is the disambiguator from chain's SOLID + ⚭. No data-id needed today; if added later, id='ctrl__<child>__<parent>'.

### LimitSchedule (cap, NOT rendered as node/edge today)

```
  LimitSchedule as standalone clock-face hexagon node:
        .---.
       / §   \
      | $50k  |   ls__ConcentrationMaster__Rail_18
       \ OUT /        (Outbound cap on Rail_18)
        '---'
        hex silhouette, peach-light fill, $-magnitude in center
        OUT/IN tag bottom = direction

  Inbound cap:
        .---.
       / §   \
      | $10k  |   ls__InternalRole_02__Rail_44
       \  IN /        (Inbound cap)
        '---'
        same hex, IN tag

  Edge from cap-hex to rail (dotted, no arrow): the hex 'watches' the rail.
  Edge from cap-hex to control parent (dotted): the hex 'lives on' the control.

  Multiple caps on one control = multiple hex satellites near the role pill.
```

**Facts encoded:**
- cap existence (its own hex node — first-class entity vs today's label decoration)
- cap magnitude (currency value baked into center of hex — $50k visible at-a-glance)
- direction (OUT/IN tag at bottom)
- target rail (dotted 'watches' edge)
- parent control role (dotted 'lives on' edge)
- limit-bearing glyph § in hex top = matches § on control role's pill (visual rhyme)

**Rendering approach:** shape=hexagon, style='filled', fillcolor='#ffe9d6' (peach-light, related to template peach but distinguishable), label='§\n$<magnitude>\n<OUT|IN>' — three-line label. data-id=ls__<parent_role>__<rail>. Two dotted edges from the hex: one to the rail node (style='dotted', constraint=false, no arrow), one to the parent_role node (style='dotted', constraint=false, no arrow). New first-class entity in the diagram — today's label-decoration disappears in favor of this hex satellite.

### Self-loop (single-leg rail on its own leg_role)

```
  Self-loop is just a SingleLegRail whose leg_role appears at BOTH endpoints —
  graphviz renders the curve naturally; the pentagon silhouette + D/C/V letter
  in the rail node IS the disambiguator. Adds a ↻ glyph for explicitness.

         .------.
        /        \
       | ExtRole_4 |
        \        /
         '---+--'
             |
             ↻ (visual curl)
             |
           .---.
          /     \
         < D ↻ Rail_10
          \     /
           '---'
           D = Debit, ↻ glyph confirms self-loop
```

**Facts encoded:**
- direction (D/C/V letter in rail label — explicit, not just arrow-based)
- self-loop (↻ glyph in rail label)
- leg_role identity (the connected role pill)
- anchoring/aggregating/etc. (same border/stripe vocabulary as other rails)

**Rendering approach:** No special shape — reuse SingleLegRail's pentagon/diamond primitive. Add '↻ ' prefix in label when src==dst. graphviz handles the edge curve. data-id=rail__<name>. Color stays #7f6000 on the edge to match the single-leg flow color convention.

### Undeclared / orphan role (data-quality)

```
  Undeclared role (referenced by a rail but no Account/AccountTemplate declares it):
   .  .  .  .  .  .  .  .
  ( ⚠ GhostRole_xx       )      data-id=role__GhostRole_xx, data-state=undeclared
   '  '  '  '  '  '  '  '
     dotted border (distinct from solid/dashed/double)
     ⚠ glyph top-left
     fill stays scope-default (or white if scope unknown)

  Orphan role (declared, no rail references it — L1 view only):
   . . . . . . . . . . . .
  ( Ø OrphanRole_07       )      data-id=role__OrphanRole_07, data-state=orphan
   ' ' ' ' ' ' ' ' ' ' ' '
     dotted border + Ø glyph (no-flow indicator)
```

**Facts encoded:**
- data-quality flag (dotted border + warning glyph — distinct from any healthy state)
- reason (⚠ = undeclared / Ø = orphan)
- scope still attempted via fill (or white when unknown)
- stable data-id for trainer/coverage filter

**Rendering approach:** shape=ellipse, style='filled,dotted', color='#cc6600' (warn-orange border), fillcolor=scope-default or '#ffffff'. label prefix '⚠ ' or 'Ø '. Single node, data-id=role__<name>, data-state injected via id='role__<name>__undeclared' suffix that JS strips for routing but reads for badge state.

## Rail collapse treatment

Bundled rails render as the SAME silhouette primitive as their individual kind (chevron for two-leg, pentagon for single-leg) but with stacked-stroke peripheries scaling with N: peripheries=2 for N=2-5, peripheries=3 for N=6-12, peripheries=4 for N=13+. penwidth modulates 1.5→4.0 across the same range. The bottom-right '×N' badge is always plain text in the label. Member-name splat is GONE — the silhouette stack IS the 'there are layers inside' affordance; clicking the bundle opens a sidebar list (JS-side; the server emits a <title> tooltip with member-names-csv for hover preview).\n\n  Individual rail (N=1):           Small bundle (N=3):              Large bundle (N=17):\n   ____________                     ============                     ================\n   \\           >    Rail_42         \\\\          >>   bundle_2 ×3     \\\\\\           >>>>   bundle_4 ×17\n    >_________/                      >>________//                     >>>=========////\n                                    ============                     ================\n     1pt stroke                      1.8pt, peripheries=2             3.5pt, peripheries=3\n\nThe direction (chevron tip orientation) is preserved across all N — a bundle of westbound rails looks like a thicker westbound chevron. Aggregating bundles (rare) layer the diagonal-stripe fill on top of the stacked silhouette — reads as 'stripey-stack', visually busy enough to flag 'this is a weird collapsed sweep'.

## XOR group treatment

XOR groups render as a thin curly-brace silhouette wrapping their member rails — the ONLY remaining graphviz cluster in the language (every other entity is a single node). The brace is a subgraph cluster_xor_<tmpl>_<gi> with style='rounded,dashed', color='#5a6f9c' (the existing pale-blue tone, now load-bearing as the XOR-specific accent), fillcolor='none' (critical: no fill so member tint surfaces stay paintable for coverage), label='⦇ xor ×N: exactly-1 ⦈' positioned at the top.\n\n          parent template's stacked-tab silhouette\n                          |\n                          v (dotted membership edges)\n                  .--------+--------+--------.\n                 (   .---. .---. .---.        )\n                 (  < V_1 > < V_2 > < V_3 >   )   xor ×3: exactly-1\n                 (   '---' '---' '---'        )\n                  '---------------------------'\n                       dashed brace cluster\n                       no fill, just outline\n                       members keep their individual pentagon-V silhouettes\n                       members keep their individual data-ids\n\nThe brace itself gets id='xor__<tmpl>__<gi>' on the cluster <g> for future XOR-contract filtering. Because the brace has no fill, the operator sees the XOR contract as a 'lasso around alternatives' without any color competition with the member rails' coverage tint. Variable-direction members (validator-required) carry the 'V' letter in their pentagon silhouette — the XOR brace + V-pentagons together read instantly as 'pick one closing leg'.

## Information density demo (Template + 5 rails + 2 chains)

```
Template TransferTemplate_13 (peach folder silhouette) with 5 leg-rails (mix of two-leg chevrons + single-leg pentagons, one XOR group of 2, one aggregating, one bundle) + 2 chains attached (one required, one XOR fan-in). Heavy-fixture-realistic.\n\n                       .--.\n                       |  |--------------------.\n                       '--+                     \\\n                          | TransferTemplate_13 ⊕|         <-- folder silhouette, peach\n                          | key: customer_id     |             ⊕ = chain participant\n                          | completion: bday_end |             metadata visible in label\n                          | legs: 5, xor: 1×2    |\n                          '----------------------'\n                              :       :       :       :       :       <-- dotted membership edges\n                              :       :       :       :       :\n                  __________  :       :       :       :       :\n                  \\         >>:       :       :       :       :\n                   >>=======//          .---.   .---.        ////////      <-- 1 chevron-anchor (Rail_A, chain-anchored peripheries=2)\n                   Rail_A ⊕               < D > < C >        \\⧖//////>>      <-- 2 pentagons (Debit Rail_B, Credit Rail_C)\n                                          Rail_B Rail_C       >//////        <-- 1 aggregating chevron (Rail_E, ⧖ + stripe, ×3)\n                                                  ============              ×3   <-- 1 bundle chevron (bundle_2, peripheries=2, ×2)\n                                                  \\\\         >>\n                                                   >>=======//\n                                                   bundle_2 ×2\n                                          ⦇ xor ×2: exactly-1 ⦈                  <-- brace wrapping Rail_B + Rail_C (XOR group)\n                                          .---.   .---.\n                                         < V > < V >\n                                          '---'   '---'\n                                          Rail_X   Rail_Y                       (these would replace B+C if XOR—schema requires V-members)\n\n              Two chain edges leaving Rail_A:\n              Rail_A ==⚭⚭ chain (req)==► TransferTemplate_27   <-- solid + chainlink, required\n              Rail_A ==⚭⚭ chain (fan-in 4→1)==⛮► TransferTemplate_14   <-- thicker, funnel glyph, fan-in\n\n              Hovering caps (LimitSchedule satellites near Rail_A):\n                  .---.\n                 / §   \\\n                | $50k  |   ls__ConcentrationMaster__Rail_A   <-- hex cap-node, OUT, dotted edges to Rail_A + ConcentrationMaster\n                 \\ OUT /\n                  '---'\n\nReadable simultaneously without label clutter:\n  - Template identity + transfer_key + completion + leg count + xor presence (folder label, 4 facts)\n  - Each rail's kind (chevron vs pentagon silhouette), direction (tip orientation + D/C/V letter), anchoring (border style), aggregating (stripe + ⧖), bundle size (×N + peripheries stack), chain participation (⊕)\n  - Both chain kinds disambiguated at the edge (solid+⚭+(req) vs solid+⚭+⛮+(fan-in))\n  - LimitSchedule first-class with magnitude + direction\n  - XOR contract framed as a brace, member alternatives visible\nApprox 25-30 facts in one composite at this density — each shape contributing 3-5 individually.
```

## Consistency argument

All shapes obey four shared rules: (1) ONE silhouette primitive per type — pill/folder/chevron/pentagon/hexagon/brace, each unmistakable at a glance even when scaled down to 40px wide; (2) fill color is ALWAYS scope (internal-blue / external-yellow / template-peach / templated-mint / cap-peach-light), so an operator scanning the canvas for 'where does the institution end' just looks for the yellow; (3) border treatment is ALWAYS anchoring state (solid / dashed / double-stroke / dotted) — the same vocabulary applies to roles and rails and templates uniformly; (4) corner conventions are fixed — top-left for state glyphs (⊕ / ⚠ / § / ⧖), bottom-right for multiplicity (×N), label-prefix for direction letters (D/C/V), label-suffix for cadence/completion metadata. Once you learn 'chevron = two-leg rail, tip points downstream, stripe means sweep, stacked stroke means bundle, ×N tells me how many', you've learned the whole rail vocabulary, and the SAME corner/border/fill rules teach you roles and templates simultaneously. The language has only 6 silhouette primitives + 4 border styles + 5 fill colors + ~6 glyph marks — a finite, learnable alphabet, not an ad-hoc decoration soup.

## Tradeoffs (honest cost)

- Templates lose their cluster-as-region affordance — the v13.1.1 audit's 'each template is a region you can visually carve out of the canvas' goes away in favor of a single folder-silhouette node with dotted membership edges. The dotted edges to leg-rails will rake across the canvas in heavy fixtures; partially mitigated by graphviz's edge bundling and the constraint=false hint already in place post-CF.3.a, but the operator loses the 'walled garden' feel for each template.
- Direction-as-silhouette (chevron tip / pentagon tip) commits to a specific layout orientation. If dot lays out a westbound rail, the rightward-pointing cds shape contradicts the flow direction visually unless we rotate the shape per-edge — graphviz's orientation= attribute does this but rotation is per-node not per-edge-direction, so a rail with src on the right and dst on the left will visually 'point wrong'. Real cost: ~10-20% of rails in heavy fixture will have a silhouette-vs-edge-arrow contradiction that the operator must reconcile.
- Unicode glyphs (⊕ ⚠ § ⧖ ⌖ ⚭ ⛮ Ø ↻ ⦇ ⦈) require font support in the dot rendering pipeline; @hpcc-js/wasm-graphviz uses system fonts which differ across macOS/Linux/Windows browsers. Risk: a glyph renders as a tofu box on one platform and the state cue disappears. Mitigation: pin a webfont (e.g. Symbola or Noto Sans Symbols) in the studio HTML wrapper.
- LimitSchedule promotion to first-class hex nodes adds 6 extra nodes + ~12 dotted edges to heavy fixture (3.8% more nodes / 5.4% more edges) — increases crossings count, hurts the CF.3.a crossings-reduction win. Real cost: probably +30-50 crossings in heavy_density_v1.
- Bundle expansion-on-click moves member-name discovery to a JS sidebar; if the JS layer isn't loaded (e.g. PDF export, static SVG view), the bundle is an opaque 'bundle_4 ×17' with no way to see members. Member-names-in-<title>-tooltip mitigates for browser hover but not for print/PDF.
- Single-stroke + single-fill commitment means no HTML tables for templates — the metadata rows (transfer_key / completion / leg count) live in a multiline \n-separated label inside one folder node. graphviz's label rendering for multiline plain labels is left-aligned + monospace-ish; looks fine but lacks the visual hierarchy of HTML tables. Templates will look 'denser' textually than today's component+cluster pair.
- The XOR brace remains the ONE cluster in the language — a deliberate exception. Operators reading the visual rules will hit 'wait, why is THIS thing a cluster' and need to learn it. Defensible because XOR-as-brace is the most expressive visual idiom for 'exactly one of these', but it's a learning bump.
- shape=cds and shape=folder are graphviz built-ins but render with slightly different padding/sizing rules than ellipse/box — mixing them on the same canvas may produce uneven visual weights at the same label length. Need a layout pass to tune width=/height= per shape kind to keep them weight-balanced.
