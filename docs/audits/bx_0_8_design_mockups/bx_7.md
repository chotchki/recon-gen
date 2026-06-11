# BX.7 — Top-nav BUILD / VIEW split + color grouping

> Cell scope: pick the visual treatment + color palette for the
> BUILD / VIEW (+ Reference) groups in the always-on Studio top nav.
> Direction (the split itself) is locked by operator §1; this doc
> resolves only the visual / palette / accessibility surface.

## Current state

Screenshots reviewed (paths under `/tmp/bx_0_8_screenshots/`):

- `topnav_home.png`, `topnav_list_account.png`, `topnav_edit_account.png`
  — all three crop just below the global top nav and show the
  page-title strip only ("L2 Editor" / "Accounts" / "Edit account ·
  gl-1010-cash-due-frb"). The actual top-nav bar (BS.3 + BTa.7
  upgrade) is OFF the top of each frame, which is itself a finding
  (a freshly-arrived consultant who scrolls and never sees the full
  nav loses the chrome's information entirely — but that's BX.6's
  problem, not BX.7's).
- Code path: `common/html/render.py::emit_top_nav` + `build_top_nav_entries`.
  Today's nav: `Recon-Gen | Diagram | L2 Editor | ETL Support |
  Training ‖ <heavy bar + "Dashboards" chip> ‖ L1 Dashboard | L2 Flow
  Tracing | Investigation | Executives ‖ <heavy bar + "Reference" chip> ‖
  Docs ┄┄ [?]`.

Already shipped at BTa.7:

- Heavy-bar separator + tiny uppercase group-label chip between groups
  (`Studio` / `Dashboards` / `Reference`).
- Thin separator between same-group entries.
- Active-page underline + accent tint background (`bg-accent/5`).
- Group labels reuse the **same `text-accent` + `bg-accent/10`**
  treatment regardless of which group — *no color differentiation
  by role*. **This is the gap BX.7 closes.**

The BTa.7 cold-read finding ("uniform divider made grouping
invisible") was solved monochromatically. Operator §1's read of v1b
upgrades the ask: *colour-code the groups* so the pre-attentive
layer carries the BUILD-vs-VIEW boundary without forcing the eye to
parse the group-label chip text.

## Constraints from BX.0 / BX.0.7 locks

1. **BX.0.7 §BX.7 (direction lock).** BUILD half = L2 Editor / Diagram /
   ETL Support / Training. VIEW half = L1 Dashboard / L2 Flow Tracing /
   Investigation / Executives. Reference (Docs) is a third group.
   *Whether* to split is settled — *how* is open.
2. **BX.0.7 §BX.7 ("accessible contrast").** Anything we pick must hit
   WCAG AA (4.5:1 for body text, 3:1 for non-text UI). Pure hue-coded
   grouping fails this for ~5% of male users (deuteranopia) without
   a redundant non-colour cue.
3. **Cold-read v1b §1 Top nav.** Operator explicitly endorsed
   "I like this segmentation, maybe group the top nav parts and
   color code". Color is *additive* signal, not replacement — the
   group-label chip + heavy separator stay.
4. **Memory `feedback_browser_drivers_user_facing_locators`.** Group
   identity must surface as a stable `data-nav-group="build|view|ref"`
   attribute on each entry — drivers + screenshots can't anchor on
   Tailwind utility classes. (Today's `entry.group` is "authoring" /
   "viewing" / "reading"; one rename of those tokens to `build` /
   `view` / `ref` happens as part of this cell so internal/external
   vocabulary matches the operator-facing label.)
5. **Memory `project_design_north_stars` — CPA-readable terms.** Group
   labels must read as banker vocabulary. The current labels —
   *Studio* / *Dashboards* / *Reference* — pass for the View+Ref
   half but **Studio** is unprintable for a banking consultant.
   Rename to **Build** / **View** / **Reference** in this cell
   (matches the locked operator framing word-for-word).
6. **Cross-renderer parity (`project_app2_parity_for_offline_iteration`).**
   The nav is App2-only chrome (QS dashboards open inside their own
   embed; QS itself has no concept of a "BUILD half"). Parity gate
   is therefore "does App2 render the grouped nav at all" — no QS
   side to diverge.
7. **L2-theme override (`common/theme.py::DEFAULT_PRESET`).** The
   default palette is *blues + greys*. Each L2 instance can override
   `theme.accent` to anything — so the group hues must derive from
   the active theme tokens (`accent` / `success` / `warning`), not
   hard-coded swatches, or every brand-overridden Studio will land
   on wrong-feeling colors. The DEFAULT_PRESET ships
   `accent=#2E5090` (dark blue), `success=#2E7D32` (green),
   `warning=#E65100` (amber).

## Directions

Five mockups, all framed as ASCII renders of the full nav strip with
*active page = "L2 Editor"*. All keep the heavy-bar + group-label
chip from BTa.7 and only vary the colour treatment.

### Direction 1 — Tinted-band underline per group

Each group gets a 2-px colored underline that runs the full width of
its band of entries; the underline is the group's hue (Build = accent
blue, View = success green, Reference = neutral grey). Group label
chip keeps its existing `bg-accent/10` tint; entries themselves are
untinted. Active page wins a heavier 4-px underline in the group hue
(same shape as today's `border-b-2 border-accent` active treatment,
just multi-coloured).

```
+-------------------------------------------------------------------------------+
| Recon-Gen | [BUILD] Diagram | L2 Editor* | ETL Support | Training            |
|           |________________________________________________                   |
|           |   (2px blue band — Build group)                                   |
|                                                                               |
| ‖ [VIEW] L1 Dashboard | L2 Flow Tracing | Investigation | Executives          |
|        |__________________________________________________                   |
|        |   (2px green band)                                                   |
|                                                                               |
| ‖ [REF] Docs                                                          [?]    |
|        |______                                                                |
|        |   (2px grey band)                                                    |
+-------------------------------------------------------------------------------+
```

| Axis | Score |
| --- | --- |
| Effort | **Low** — single 2px `border-b` swap + per-group CSS var |
| Risk | **Low** — additive on existing BTa.7 chrome |
| User-mental-model fit | **High** — underline reads as a "bucket" without color literacy |
| Accessibility | **High** — underline is the redundant non-color cue; passes AA on its own |
| Cross-renderer parity | **N/A** — App2-only |

### Direction 2 — Group-label chip color + nothing else

Keep entries fully neutral; only the uppercase group-label chip
(`BUILD` / `VIEW` / `REF`) gets a colored background. Build = solid
blue (`bg-accent text-accent-fg`), View = solid green
(`bg-success text-success-fg`), Reference = neutral grey
(`bg-secondary-bg text-secondary-fg`).

```
+-------------------------------------------------------------------------------+
| Recon-Gen ‖[█BUILD█]‖ Diagram | L2 Editor* | ETL Support | Training           |
|           ‖[█VIEW█] ‖ L1 Dashboard | L2 Flow Tracing | Investigation |Execs   |
|           ‖[ REF ]  ‖ Docs                                          [?]       |
+-------------------------------------------------------------------------------+
```

| Axis | Score |
| --- | --- |
| Effort | **Lowest** — one Tailwind class swap on the group-label span |
| Risk | **Low** — change scoped to 3 chips |
| User-mental-model fit | **Medium** — color is on the label, not the items; less pre-attentive |
| Accessibility | **High** — solid chip easily hits AA; chip text is the redundant cue |
| Cross-renderer parity | **N/A** |

### Direction 3 — Per-entry tinted background

Every entry in a group gets a faint group-tinted background
(`bg-accent/5` for Build, `bg-success/5` for View, `bg-secondary-bg`
for Reference). Active page upgrades to `bg-accent/15` (or
group-equivalent). Group labels stay as today.

```
+-------------------------------------------------------------------------------+
| Recon-Gen [BUILD] [ Diagram ][ L2 Editor* ][ ETL Support ][ Training ]        |
|                   <--all entries have very pale blue tint-->                  |
|                                                                               |
|           [VIEW]  [ L1 Dashboard ][ L2 Flow Tracing ][ Investigation ][ Exec ]|
|                   <--all entries have very pale green tint-->                 |
|                                                                               |
|           [REF]   [ Docs ]                                            [?]     |
|                   <--neutral grey-->                                          |
+-------------------------------------------------------------------------------+
```

| Axis | Score |
| --- | --- |
| Effort | **Low-Med** — per-entry class branch in `emit_top_nav` loop |
| Risk | **Medium** — large tinted area reads "highlighted"; risks competing with active-page tint |
| User-mental-model fit | **High** — entire group reads as a bucket |
| Accessibility | **Medium** — 5% tint at AA contrast can drop below 3:1 on white; needs verification per-theme |
| Cross-renderer parity | **N/A** |

### Direction 4 — Left-border stripe per group

Each entry carries a 3-px left border in its group hue. Effectively a
"colored gutter" that runs the length of the group band. Group label
chip retains its tint. No entry-body color change.

```
+-------------------------------------------------------------------------------+
| Recon-Gen ‖[BUILD]‖█Diagram |█L2 Editor* |█ETL Support |█Training             |
|           ‖[VIEW] ‖█L1 Dashboard |█L2 Flow Tracing |█Investigation |█Execs   |
|           ‖[REF]  ‖█Docs                                              [?]     |
|                    ^                                                          |
|                    └── 3px left border per entry in group hue                 |
+-------------------------------------------------------------------------------+
```

| Axis | Score |
| --- | --- |
| Effort | **Low** — `border-l-[3px]` per entry, var-driven hue |
| Risk | **Low-Med** — risks reading as "selected"; tab-style left-stripe is a known UI pattern that may clash with the existing active-page underline |
| User-mental-model fit | **Medium** — works once recognized, but the visual conflicts with the underline-as-active convention |
| Accessibility | **High** — solid 3-px stripe meets the 3:1 non-text bar with room |
| Cross-renderer parity | **N/A** |

### Direction 5 — Stacked two-row nav (BUILD over VIEW)

The grouping becomes structural, not chromatic: BUILD goes on row 1,
VIEW on row 2, with Reference + [?] tucked into the right gutter of
row 2. Each row carries a thin tinted left-edge label
("BUILD" / "VIEW") so the row's identity stays present after the eye
has scanned past it.

```
+-------------------------------------------------------------------------------+
| Recon-Gen | [BUILD] | Diagram | L2 Editor* | ETL Support | Training           |
|           | [VIEW]  | L1 Dashboard | L2 Flow Tracing | Investigation | Execs  |
|                                                                  [Docs] [?]  |
+-------------------------------------------------------------------------------+
```

| Axis | Score |
| --- | --- |
| Effort | **Medium-High** — two-row layout rewrite + active-row chrome; touches the side-panel-drawer's vertical placement |
| Risk | **High** — doubles nav vertical height (~80px → 120px) on every page; eats screen real estate before any work surface |
| User-mental-model fit | **Highest** — structural separation is the strongest possible BUILD/VIEW signal; banker reading is unambiguous |
| Accessibility | **High** — no reliance on color; row position is the cue |
| Cross-renderer parity | **N/A** |

## Recommendation

**Direction 1 — Tinted-band underline per group.**

Rationale, in order of weight:

1. **Cheapest + least-risky way to honour the operator's "color code"
   ask** without re-architecting the nav. BTa.7 already shipped the
   group-label chip + heavy bar; D1 layers a 2-px colored underline
   under each band, picking up the existing accent-underline pattern
   the active-page treatment already uses. Code change is per-entry
   `border-b-[2px]` with a CSS-var-driven hue (`var(--nav-group-build)`,
   etc.) — about ~30 lines in `emit_top_nav`.
2. **Redundant non-color cue is already there.** The group-label chip
   text (BUILD / VIEW / REFERENCE) and heavy separator both survive
   regardless of color literacy — so D1 passes WCAG by construction,
   not by palette luck.
3. **Hue derives from theme tokens.** `accent` for Build, `success`
   for View, `secondary_fg` for Reference. L2-theme overrides
   automatically re-tint without per-brand engineering. The
   DEFAULT_PRESET's `success` green (`#2E7D32`) lands at 5.4:1 against
   white — passes AA as a 2-px non-text bar (only needs 3:1).
4. **Active-page treatment scales cleanly.** Today's
   `border-b-2 border-accent` becomes "heavier-than-the-group-band"
   (`border-b-4` in the group's hue) — the active-page signal stays
   inside its bucket, which reinforces "where you are = which mode".

D2 is the runner-up if implementation budget is *truly* zero — it's
~5 lines of code — but the color signal lives only on the label
chip, so the operator scanning the entries doesn't get the
pre-attentive boundary they asked for. D5 (stacked) is the
"strongest possible signal" answer but the vertical-real-estate cost
is real (every Studio page loses ~40px above the fold) and Phase BS
explicitly fought to get the nav into one row — re-stacking now
walks that back without enough new evidence.

## Open questions

- Meta Comment from bx_6: Theme is way overly complicated it may be worth toning down what users can tweak to a primary/secondary/logo. The rest could be a standard accessible color palette. As far as the exact colors, personally I don't care about the details as much as I care about legibility/accessiblity.

1. **Reference / Docs hue — neutral grey or its own color?** D1 picks
   neutral on the theory that Reference is "not a destination, it's a
   sidecar". If the operator reads Reference as a peer destination
   (it currently holds one entry — Docs — but BTa.1 may add more),
   pick a third color (amber `warning`?). Easy to swap; just CSS
   var.
2. **`secondary_fg` token vs explicit grey.** `secondary_fg`
   (DEFAULT_PRESET = `#4A4A4A` dark grey) might be too dark as a
   2-px band on white surface (6.3:1 — fine for text, but visually
   "heavy"). Alternative is `#8C8C8C` medium grey which lands at
   3.3:1. Test both visually before locking.
3. **Group hue on `--accent` overrides.** If an L2 sets
   `theme.accent = #C62828` (red) and `theme.success = #4A7DC7`
   (blue), the Build band reads red and View reads blue — which
   inverts a banker's "blue = ok, red = warning" intuition. Should
   the group-hue mapping (Build/View/Ref → which theme token) be
   *fixed* in code, or operator-configurable in the L2 `theme:`
   block as `theme.nav_group_build`, `theme.nav_group_view`,
   `theme.nav_group_reference`? Recommend deferring the
   configurable axis to a follow-on cell; ship D1 with the fixed
   `accent` / `success` / `secondary_fg` mapping first and see
   whether any L2 override actually surfaces a problem.
4. **Rename `entry.group` token values from `authoring/viewing/reading`
   to `build/view/ref`.** Source-of-truth lives in
   `render.py::build_top_nav_entries`. Renaming is one edit + a
   `data-nav-group="..."` attribute add for test anchors (memory
   `feedback_browser_drivers_user_facing_locators`). Operator
   confirmation that "build/view/ref" matches their mental model
   before we burn the tokens in.
5. **Group-label chip text — keep `Studio` or rename to `BUILD`?**
   Today's chip says "Studio" / "Dashboards" / "Reference". The
   operator §1 comment frames the buckets as "Build" / "View" — so
   the chip text should match. Lock the rename in this cell or
   leave the chip text alone and rely on color + position for the
   bucket identity? Recommend the rename (`BUILD` / `VIEW` /
   `REFERENCE`) — cheaper than asking the operator to mentally
   translate every visit.
    - Comment: I'm good with this.
