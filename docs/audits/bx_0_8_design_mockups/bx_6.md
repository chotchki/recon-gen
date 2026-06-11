# BX.6 — Home with "Start here" flow + singleton tiles

> **Cell brief (from PLAN.md):** Home with "Start here" flow + singleton
> tiles + dependency-order numbering + completeness checkmarks per
> kind + singletons promoted out of accordion. **Open Q:** how to
> relate to the diagram.
>
> Estimated 3-4h implementation after this mockup lands. Touches
> `_studio_routes.py::_render_home_page` + `_HOME_SECTIONS` ordering +
> the singleton accordion treatment.

## Current state

Screenshots:
`/tmp/bx_0_8_screenshots/home.png`,
`/tmp/bx_0_8_screenshots/topnav_home.png` (the top of the home page is
identical to the entity-list page chrome — same nav, same banner),
`/tmp/bx_0_8_screenshots/diagram_focus.png` (the diagram surface BX.6
must relate to).

What's on screen today at `/`:

- **Header strip** — "L2 Editor" h1 + one-paragraph prose: "Each
  section below is a kind of building block in this institution's
  L2 shape. Expand one to browse its entries — search the summary or
  click a card title to see the detail. The diagram link in the top
  nav shows how the kinds connect."
- **Eight accordion sections** (per `_HOME_SECTIONS` +
  `_HOME_SINGLETONS`) in this order:
  1. Accounts (16) — open by default per the CG.14 lock
  2. Account templates (3) — collapsed
  3. Rails (21) — collapsed
  4. Transfer templates (3) — collapsed
  5. Chains (9) — collapsed
  6. Limit schedules (variable) — collapsed
  7. Theme (set) — collapsed singleton
  8. Instance settings (set) — collapsed singleton
- Each accordion summary has: chevron, label + count, search input,
  `+ Add` link, `↗` link to the dedicated `/l2_shape/<kind>/` page.
- Standalone-mode banner at the very top (orthogonal — set by `cfg`).

What's NOT on screen but the cold-read flagged hard:

- **No dependency-order signal.** Sections are in declaration order
  (accounts first) but a Persona B implementation consultant reads
  alphabetically and picks the wrong starting point.
- **No completeness signal.** Counts exist but "16 accounts" doesn't
  tell the consultant whether 16 is enough. No "your institution is
  60% complete" anywhere.
- **Singletons (Theme + Instance settings) buried at position 7-8** in
  the same accordion shape as the entity collections. They look like
  "two more entity kinds" rather than "one-time configuration that
  gates everything downstream."
- **Diagram is one top-nav click away** but visually disconnected.
  The home doesn't say "the diagram is your map." The diagram doesn't
  say "click an entity to edit it." The two surfaces share a top nav
  and nothing else.

The current home reads like a viewer: dense, alphabetical, no
narrative. The cold-read called this out as the #6 sign-off concern.

## Constraints from BX.0/0.7 locks

From `_archive/bx_0_replan.md` (BX.0):

- **Two personas, both first-class.** Persona A bounces from Triage to
  one edit page and back; Persona A may never visit `/`. Persona B
  starts at `/` and walks every kind. **BX.6 optimizes for Persona B
  without breaking Persona A's drive-by path.**
- **Singletons gate everything.** Instance carries
  `role_business_day_offsets` (every cadence calc); Theme drives the
  QS palette; Persona drives every handbook string (per BXa.1 the
  persona singleton is gone but Instance + Theme remain). They are
  not optional polish — they are first-three-things-you-touch.
- **Diagram is a consumption artifact, not a guidance one.** Beautiful
  finished snapshot; useless before there's anything to consume.

From `_archive/bx_0_7_replan_with_triage.md` (BX.0.7):

- **BX.6 lock — operator agreed in principle:** "we will need design
  work, especially where should this relate to the diagram?"
  Direction MUST propose an answer.
- **BX.7 lock — top nav BUILD/VIEW split.** Today `build_top_nav_entries`
  already tags entries with `group="authoring" | "viewing" | "reading"`,
  but the visual grouping is BX.7's job. **BX.6 must not duplicate
  BX.7's surface — singleton tiles on the home page belong to the
  authoring flow, NOT the top nav.** The operator's §1 comment on
  the cold-read explicitly says "group the top nav parts and color
  code"; that's BX.7, not BX.6.
- **BX.8 lock — diagram nodes link-to-edit (operator walked back the
  click-to-jump because it collides with click-to-focus).** Per-entity
  edit page gets a mini-diagram. **BX.6 can reference the diagram but
  must not pre-commit to BX.8's mini-diagram embedding shape.**
- **BX.11 lock — Account vs AccountTemplate 1:1-vs-1:N distinction.**
  Visual + copy on list pages. **BX.6 should hint at the distinction
  in the section labels but the deep treatment belongs to BX.11.**
- **`[[project_design_north_stars]]`** — CPA-readable banking
  terminology + minimum table count. The home page should NOT add
  new entity-kind divisions; it should re-order + re-frame what
  already exists.
- **`[[feedback_browser_drivers_user_facing_locators]]`** — every new
  surface needs `data-*` anchors (e.g., `data-step="1"`,
  `data-singleton="theme"`, `data-completeness="<kind>"`), NOT
  Tailwind utility classes, so `App2Driver` can address them without
  re-implementing CSS-class selectors.
- **`[[feedback_invariants_in_types]]`** — completeness rules should
  live in a typed function (e.g., `compute_home_completeness(instance)
  -> HomeCompleteness` with explicit `kind → state` mapping), not
  scattered template conditionals.

## Directions

Five directions, ordered from minimal-touch to ambitious-redesign.

---

### Direction A — "Re-order + promote singletons, keep accordions"

**Thesis:** Smallest defensible move. Re-order `_HOME_SECTIONS` into
topological/dependency order, promote singletons out of the accordion
stack into a dedicated "Start here" strip above the entity sections,
and add a completeness checkmark to each section summary. Diagram
stays in the top nav with a `→ View diagram` link in the home header
prose. No new layout primitives.

**Mockup:**

```
┌─────────────────────────────────────────────────────────────────┐
│ [top nav — BX.7 handles BUILD/VIEW grouping]                    │
├─────────────────────────────────────────────────────────────────┤
│ L2 Editor                                                       │
│ This is where you declare your institution's shape. Start with  │
│ the singletons below, then add building blocks in order.        │
│ → View diagram | → SPEC reference                               │
├─────────────────────────────────────────────────────────────────┤
│ START HERE — institution-wide configuration                     │
│ ┌─────────────────────┐  ┌─────────────────────┐                │
│ │ Instance settings ✓ │  │ Theme ✓             │                │
│ │ Name, acronym,      │  │ Colors + branding   │                │
│ │ description         │  │ for dashboards      │                │
│ │ [Edit]              │  │ [Edit]              │                │
│ └─────────────────────┘  └─────────────────────┘                │
├─────────────────────────────────────────────────────────────────┤
│ BUILDING BLOCKS — in dependency order                           │
│ ▾ 1. Account templates (3) ✓        [+ Add] [↗ open]           │
│   ... lazy-loaded list ...                                      │
│ ▸ 2. Accounts (16) ✓                [+ Add] [↗ open]           │
│ ▸ 3. Rails (21) ✓                   [+ Add] [↗ open]           │
│ ▸ 4. Transfer templates (3) ✓       [+ Add] [↗ open]           │
│ ▸ 5. Chains (9) ✓                   [+ Add] [↗ open]           │
│ ▸ 6. Limit schedules (0) ⚠          [+ Add] [↗ open]           │
└─────────────────────────────────────────────────────────────────┘
```

Singleton tiles use `data-singleton="instance" | "theme"` anchors.
Numbered prefix added to each section summary. Checkmark glyph (✓ /
⚠ / ✗) is the new completeness indicator — rules computed in a
typed `compute_home_completeness(instance) -> Mapping[EntityKind,
Literal["set", "empty", "partial"]]` helper.

**Completeness rules (proposed):**
- `set` — count > 0 AND no orphan-role refs from this kind
- `empty` — count == 0
- `partial` — count > 0 but some references are unresolved (orphan
  roles, missing reconcilers — same checks the validator runs)

**Tradeoffs:**

| Axis | Score | Notes |
|---|---|---|
| Effort | **Low** (3-4h matches estimate) | Existing accordion stays; new tile strip + numbering + checkmark glyph. |
| Risk | **Low** | No new primitives. Drop-in change to `_render_home_page`. |
| Mental-model fit | **Medium** | Numbering + "Start here" framing solves the disorientation problem. Singletons promoted to the top is correct. |
| Accessibility | **High** | Pure HTML; numbered headings work for screen readers; checkmark glyph has aria-label. |
| Cross-renderer parity | **N/A** (Studio-only surface) | Home page is not part of the dashboard render pipeline. App2Driver doesn't address `/`. |

---

### Direction B — "Numbered checklist with progressive disclosure"

**Thesis:** Frame the home as an explicit 6-step checklist. Each step
is a card that expands inline to show the current entries +
quick-add. Singletons are steps 1 + 2 (pre-flight). Diagram is a
dedicated step 7 ("Verify shape") that links to the diagram with a
"return here" breadcrumb. Heavier visual restyle but the dependency
order is unmissable.

**Mockup:**

```
┌─────────────────────────────────────────────────────────────────┐
│ [top nav]                                                       │
├─────────────────────────────────────────────────────────────────┤
│ Build your L2 — 6 of 6 steps complete  ████████████████ 100%   │
├─────────────────────────────────────────────────────────────────┤
│ ✓ Step 1 — Instance settings                          [Edit]    │
│   SNB · Sasquatch National Bank · Pacific Northwest             │
│                                                                 │
│ ✓ Step 2 — Theme                                      [Edit]    │
│   3 colors customized, defaults elsewhere                       │
│                                                                 │
│ ✓ Step 3 — Account templates (3)              [+ Add] [Expand]  │
│   CustomerDDA · MerchantDDA · DDAControl                        │
│                                                                 │
│ ✓ Step 4 — Accounts (16)                      [+ Add] [Expand]  │
│   13 internal · 3 external · 0 orphans                          │
│                                                                 │
│ ✓ Step 5 — Rails (21)                         [+ Add] [Expand]  │
│   18 two-leg · 3 single-leg · 21/21 reconciler-resolved         │
│                                                                 │
│ ✓ Step 6 — Templates + chains + limits (15)   [+ Add] [Expand]  │
│   3 templates · 9 chains · 3 limit schedules                    │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│ → Step 7 — Verify shape on diagram                              │
│   Look at the rendered topology for orphan nodes or surprises. │
└─────────────────────────────────────────────────────────────────┘
```

Singletons get equal step-billing (steps 1 + 2). Steps 4-6 carry the
entity-collection sections with a "Expand" toggle that does the
existing lazy-load fetch. Step 6 collapses three related kinds
(transfer_template, chain, limit_schedule) into a single step —
**risky terminology call** but matches how a banker thinks: "compose
the verbs," then "cap them."

`data-step="N"` anchors per step; `data-progress="<N>/<total>"` on
the top bar.

**Tradeoffs:**

| Axis | Score | Notes |
|---|---|---|
| Effort | **Medium-High** (6-8h) | Progress bar + step cards + the merged step 6 layout are all new. Lazy-load wiring stays. |
| Risk | **Medium** | Collapsing transfer_template / chain / limit_schedule under one step is a vocabulary decision that ripples — if the operator wants them separately discoverable later, undoing this is painful. |
| Mental-model fit | **High** | "6 of 6 steps complete" is the completeness signal the cold-read explicitly asked for ("how do I know I'm done?"). |
| Accessibility | **Medium** | Progress bar needs `role="progressbar"` + value text; otherwise fine. |
| Cross-renderer parity | **N/A** | Studio-only. |

---

### Direction C — "Two-pane: dependency tree (left) + diagram preview (right)"

**Thesis:** Split the viewport. Left pane carries the numbered
dependency tree with completeness checkmarks (same content as
Direction A's section list). Right pane embeds the diagram as a
read-only preview. Clicking a tree node scrolls the diagram preview
to that node's group; clicking a diagram node scrolls the tree to
that section. **This is the direction that explicitly answers the
open question: the diagram lives on the home page as the visual map
of progress.**

**Mockup:**

```
┌─────────────────────────────────────────────────────────────────┐
│ [top nav]                                                       │
├─────────────────────────────────────────────────────────────────┤
│ L2 Editor — SNB Sasquatch National Bank                         │
├──────────────────────────────────┬──────────────────────────────┤
│ DEPENDENCY ORDER                 │ INSTITUTION SHAPE            │
│                                  │                              │
│ ⚙ Instance ✓        [Edit]       │ ┌──────────────────────────┐ │
│ ⚙ Theme ✓           [Edit]       │ │                          │ │
│                                  │ │   [DDAControl]           │ │
│ 1. Account templates (3) ✓       │ │      |                   │ │
│    [+ Add] [Open list ↗]         │ │   [CustomerDDA]          │ │
│ 2. Accounts (16) ✓               │ │      |                   │ │
│ 3. Rails (21) ✓                  │ │   ... etc ...            │ │
│ 4. Transfer templates (3) ✓      │ │                          │ │
│ 5. Chains (9) ✓                  │ └──────────────────────────┘ │
│ 6. Limit schedules (0) ⚠         │ → Open full diagram          │
│                                  │                              │
│ ⚠ 1 step needs attention         │ Click a node to edit         │
└──────────────────────────────────┴──────────────────────────────┘
```

The diagram preview is an `<iframe src="/diagram?layer=1&compact=1">`
re-using the existing diagram surface with a compact CSS flag. The
two panes are coupled by a small JS bridge (postMessage from iframe
on node click → parent scrolls the tree section; tree click does NOT
need to drive the iframe — diagram is read-only here).

`data-pane="tree" | "diagram"` anchors; `data-step-row="<kind>"` per
left-pane row.

**Tradeoffs:**

| Axis | Score | Notes |
|---|---|---|
| Effort | **High** (10-14h) | iframe-coupling + compact diagram CSS + responsive split layout (mobile?) + the cross-pane scroll bridge. Likely overruns the 3-4h estimate by 3x. |
| Risk | **Medium-High** | Diagram-in-home was tried + removed (CF.3.l promoted Diagram to its own top-nav surface specifically because the home iframe-cascade-reload was fragile). Re-adding it backtracks that lock unless the compact preview is materially different from the full diagram. |
| Mental-model fit | **High** | Answers the open question directly. Diagram + checklist live together; the "where am I in the institution" question is one glance away. |
| Accessibility | **Low-Medium** | Two-pane layouts are hard at narrow viewports; iframe-coupling is screen-reader-hostile (the diagram is SVG and labels are sparse — see existing diagram-focus screenshot). |
| Cross-renderer parity | **N/A** | Studio-only. |

---

### Direction D — "Empty-state-first; demoted accordion for populated state"

**Thesis:** The home renders dramatically differently based on
`compute_home_completeness`. When the institution is empty (fresh
clone, no L2 declared), the home is a guided one-page wizard with
inline forms for the singletons + first account template + first
rail. When the institution is populated (≥1 entry in every kind), the
home is a dense status dashboard with a "what's missing" callout +
quick-jump to whichever kind needs attention. Numbered dependency
order shows up only in the empty state; populated state assumes the
operator knows the shape.

**Empty-state mockup:**

```
┌─────────────────────────────────────────────────────────────────┐
│ L2 Editor — Welcome                                             │
│ You haven't declared any building blocks yet. Let's start.      │
├─────────────────────────────────────────────────────────────────┤
│ Step 1 of 6 — Tell us about your institution                    │
│ ┌─────────────────────────────────────────────────────────────┐ │
│ │ Institution name:  [_________________]                      │ │
│ │ Acronym:           [_________________]                      │ │
│ │ Description:       [____________________________________]   │ │
│ │                                            [Save and continue] │
│ └─────────────────────────────────────────────────────────────┘ │
│                                                                 │
│ Coming next: 2. Theme · 3. Account templates · 4. Accounts ·    │
│              5. Rails · 6. Compositions                         │
└─────────────────────────────────────────────────────────────────┘
```

**Populated-state mockup:**

```
┌─────────────────────────────────────────────────────────────────┐
│ L2 Editor — SNB · 95% complete · last edit: 2026-06-10          │
├─────────────────────────────────────────────────────────────────┤
│ ⚠ 1 issue to resolve                                            │
│ • Limit schedules (0/N expected). [+ Add first one]            │
├─────────────────────────────────────────────────────────────────┤
│ ⚙ Instance ✓  ⚙ Theme ✓                          [→ View diagram] │
├─────────────────────────────────────────────────────────────────┤
│ ▾ Account templates (3) ✓        [+ Add] [↗ open]              │
│ ▸ Accounts (16) ✓                [+ Add] [↗ open]              │
│ ▸ Rails (21) ✓                   [+ Add] [↗ open]              │
│ ▸ Transfer templates (3) ✓       [+ Add] [↗ open]              │
│ ▸ Chains (9) ✓                   [+ Add] [↗ open]              │
│ ▸ Limit schedules (0) ⚠          [+ Add] [↗ open]              │
└─────────────────────────────────────────────────────────────────┘
```

`data-state="empty" | "populated"` on the root; the wizard form-cards
use `data-step="1" | "2" | ...` anchors.

**Tradeoffs:**

| Axis | Score | Notes |
|---|---|---|
| Effort | **High** (10-14h) | Two distinct layouts + branching + the inline forms in the empty state are forms that already exist on `/l2_shape/instance/` etc — but inlining them on `/` means duplicating the form-render code or routing the POST to the existing endpoint with a different redirect target. |
| Risk | **Medium** | Empty state is rare (only on a fresh clone before `data apply`). Most operator time is in the populated state. Designing two surfaces and using one 95% of the time may be over-investment. |
| Mental-model fit | **Very High** for first-time consultants; **Medium** for returning operators (they want the dense status, which is fine). |
| Accessibility | **High** in populated state (same as Direction A); **High** in empty state (one form per page). |
| Cross-renderer parity | **N/A** | Studio-only. |

---

### Direction E — "Sidebar checklist, body stays as today"

**Thesis:** Minimal touch to the body. Add a sticky left sidebar (or
top strip) that renders the 6-step dependency checklist with
completeness checkmarks + step numbers. Sidebar is the persistent
guidance; body retains the existing accordion. Singletons get their
own pinned card at the top of the sidebar (above step 1). Diagram
gets a "→ View diagram" link as the final sidebar item. This is
"Direction A's information architecture without restructuring the
body" — a smaller blast radius.

**Mockup:**

```
┌──────────────┬──────────────────────────────────────────────────┐
│ START HERE   │ L2 Editor                                        │
│              │ Each section below is a kind of building block.. │
│ ⚙ Instance ✓ │ ▾ Accounts (16)              [+ Add] [↗ open]   │
│ ⚙ Theme ✓    │   ... lazy-loaded list ...                       │
│              │ ▸ Account templates (3)      [+ Add] [↗ open]   │
│ BUILD ORDER  │ ▸ Rails (21)                 [+ Add] [↗ open]   │
│ 1. Templates ✓ │ ▸ Transfer templates (3)   [+ Add] [↗ open]   │
│ 2. Accounts ✓│ ▸ Chains (9)                 [+ Add] [↗ open]   │
│ 3. Rails ✓   │ ▸ Limit schedules (0) ⚠      [+ Add] [↗ open]   │
│ 4. Templates ✓ │ ▸ Theme ✓ [Edit]                              │
│ 5. Chains ✓  │ ▸ Instance settings ✓ [Edit]                    │
│ 6. Limits ⚠  │                                                  │
│              │                                                  │
│ → Diagram    │                                                  │
│              │                                                  │
│ 1 step needs │                                                  │
│ attention    │                                                  │
└──────────────┴──────────────────────────────────────────────────┘
```

The sidebar items are anchor links — click `2. Accounts ✓` → scroll
to + auto-open the Accounts accordion. The body accordion stays
exactly as today (singleton sections included), so the diff is
minimal.

`data-sidebar-step="<kind>"` for each link.

**Tradeoffs:**

| Axis | Score | Notes |
|---|---|---|
| Effort | **Low-Medium** (4-6h) | Sidebar grid (CSS) + completeness computation + the anchor-link scroll behavior. Body unchanged. |
| Risk | **Low** | Body is byte-identical to today; sidebar is pure addition. Easy to A/B if needed. |
| Mental-model fit | **Medium-High** | Sidebar gives the dependency-order guidance the cold-read asked for. But the body still LOOKS the same as today (alphabetical accordion) — half the disorientation persists. The user sees the right order in the sidebar AND the wrong order in the body, which may be more confusing than today, not less. |
| Accessibility | **High** | Sidebar = nav landmark; anchor links work for screen readers. |
| Cross-renderer parity | **N/A** | Studio-only. |

---

## Recommendation

**Pick Direction A.**
- Comment: I concur

Reasoning:

1. **It fits the 3-4h budget** in PLAN.md. Directions B / C / D will
   blow through it (likely 2-4x), and the BX phase already estimates
   35-50h total — one cell that bloats means another deferred.
2. **It directly addresses the cold-read's headline asks** (dependency
   order, completeness checkmarks, singletons-out-of-accordion)
   without inventing new layout primitives.
3. **It answers the diagram open question conservatively but
   defensibly:** the diagram stays in the top nav (BX.7's BUILD
   group); the home references it from the header prose ("→ View
   diagram") and lets BX.8 handle the deeper diagram-as-spine
   work (per-edit-page mini-diagram + node-click-to-edit). Putting
   the diagram on the home page (Direction C) re-litigates the
   CF.3.l promotion lock and risks the iframe-cascade-reload
   fragility that motivated removing it.
4. **It is incrementally upgradeable.** If the operator wants
   Direction E's sidebar later, the typed `HomeCompleteness` helper
   from Direction A drops into a sidebar consumer in 2h. If the
   operator wants Direction B's progress bar, it sums `set | empty
   | partial` counts in one line. The data model leads, the
   presentation follows.
5. **`data-step="N"` + `data-singleton="<kind>"` +
   `data-completeness="<kind>"` anchors** give App2Driver a
   stable address surface per
   `[[feedback_browser_drivers_user_facing_locators]]`. Cold-read
   v2 tests in BX.18 can assert on the numbered order without
   coupling to Tailwind utility classes.

**On the diagram open question specifically:** the home's job is
guidance; the diagram's job is shape verification. They are different
mental modes — making the consultant flip between them is fine.
**Reject the in-home embedded diagram (Direction C)** because:

- CF.3.l removed it deliberately;
- BX.8 is already chartered to wire the diagram into per-edit pages
  (a higher-leverage placement — the consultant is staring at
  ONE rail and wants to see ITS context, not the whole institution);
- the home-page completeness summary (`6 of 6 ✓` or `5 ✓ + 1 ⚠`) IS
  the abstraction the diagram would otherwise visualize, at
  one-tenth the rendering cost.

The header prose gets a single sentence: "The institution shape is
rendered on the **[Diagram](/diagram)** surface — visit there once
you've added a few building blocks to see them connect."

## Open questions

For the operator to weigh in beyond direction pick:

1. **Completeness rules — what's the bar for `set`?** Three options:
   - **a. Count > 0.** Cheapest. "16 accounts exists" = ✓. Misses
     orphan-role and missing-reconciler cases.
   - **b. Count > 0 AND no validator errors for this kind.** Matches
     the validator's existing per-kind error categorization. More
     useful, more code (need to map validator-error class →
     EntityKind).
   - **c. Count > 0 AND no validator errors AND L2-declared
     `expected_count` met.** Requires adding `expected_count` to
     account_template etc. — a YAML shape change, blocked by
     `[[feedback_no_silent_defer]]` if we want it.

   Recommendation: **b** for v1; **c** as a follow-up cell if the
   operator wants the "13 of expected 20 declared" granularity from
   the cold-read.

- Comment: Agree on b

2. **Dependency order — is account_template really first?** The
   cold-read claims account_template should precede account (per
   `account` being the materialization of a role declared on a
   template). But Direction A's mockup shows that order. Confirm —
   it's a non-obvious vocabulary call (a banker reading
   "account_template" might NOT realize they declare the role here,
   not on the account form). BX.11 will clarify the relationship in
   the list page header; BX.6 just orders them.

- Comment: I believe accounts and account_templates are equal. Named accounts are easier to understand and then account templates are a natural extension of it.
- Comment: That said the split hides and important truth, out of accounts/account templates comes role which is what is really used everywhere. It may be far better to treat accounts and account templates in the editor as variants on roles, make role the grouping and similar to rails (1 vs 2) make the user choose 1:1 and 1:N on add. that gives us space to explain better.

3. **Should the empty state get special treatment?** Direction D
   makes the case for a wizard-mode-on-empty layout. Direction A
   ignores empty state (renders the same numbered list with all
   `empty` markers). Cold-read assumed populated state. If
   first-clone UX matters, the typed `HomeCompleteness` from
   Direction A gates the wizard-mode switch with zero added work
   in v1 and a small follow-up cell.

- Comment: I don't think we should do a wizard to start, my role reframe above I think helps a LOT on that front.

4. **Singleton tile copy — what's the one-sentence sub-line?** The
   mockup shows "Name, acronym, description" / "Colors + branding
   for dashboards." Both are placeholder. Real copy should reflect
   the consultant's "what does this gate?" question:
   - Instance: "Sets institution-wide identity used in every
     dashboard title + the audit PDF footer."
   - Theme: "Drives the color palette + dashboard
     styling. Defaults work — visit only when ready to brand."

   Operator call: ship those two strings as authored above or
   workshop them.

- Comment: I took quicksight out. I would also comment that theme is crazy hard to tweak properly. Most users will have just a primary/secondary color and a logo. Theme may be due for a trim which would make other parts of the app way easier too.

5. **Persona singleton — is it coming back?** `_HOME_SINGLETONS`
   shows Persona was nuked (BXa.1 deleted the persona form routes).
   If BXa's deliverable is final, the singleton strip carries two
   tiles forever. If Persona returns post-research, the strip needs
   a third tile and ordering becomes a question (Persona before or
   after Theme?). Operator decides whether to leave the door open
   in the layout (3 tiles' worth of horizontal space) or commit to
   two.

- Comment: I don't think persona has value, it was just needless flavor to the docs and we are steadily trimming those in favor of inline text (and no one reads the docs really).
