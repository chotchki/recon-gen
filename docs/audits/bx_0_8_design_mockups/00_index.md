# BX.0.8 Design Mockup Index

Five Studio-surface cells got design mockups in BX.0.8. Each has a
sibling `bx_<N>.md` doc with 5 directions + tradeoff matrix +
recommendation + open questions. **Read this index first; the per-cell
docs are the deep dive.**

## Per-cell links

- **[BX.6 — Home page "Start here" flow](bx_6.md)** — re-order
  `_HOME_SECTIONS` into dependency order, promote singletons (Instance
  + Theme) out of the accordion stack into a dedicated tile strip,
  add completeness checkmarks per kind. **Rec: Direction A** (smallest
  defensible move, fits the 3-4h budget). 5 open questions, all
  resolvable inline.
- **[BX.7 — Top-nav BUILD/VIEW color split](bx_7.md)** — BTa.7 already
  shipped the heavy-bar + uppercase group-label chip; this cell only
  picks the color treatment + finalizes the group-label rename. **Rec:
  Direction 1** (2-px tinted underline per group, theme-token driven).
  4 open questions, the main one being the palette mapping under
  L2-theme overrides.
- **[BX.8 — Diagram node link-to-edit + mini-diagram on edit pages](bx_8.md)**
  — preserve the click-to-focus lock, add a hover-revealed "Edit"
  affordance, embed a per-entity mini-diagram on each edit page so the
  consultant doesn't lose spatial context. **Rec: Direction D2** (hover
  badge + inline server-rendered SVG mini-diagram). 6 open questions,
  most consequential being mini-diagram click semantics.
- **[BX.10 — Composite-key opaque IDs in URLs](bx_10.md)** —
  URL-only fix for chain/limit_schedule, YAML untouched per operator
  lock. **Rec: Direction E** (6-char content hash + human slug suffix,
  GitHub-issues pattern with 301-redirect on stale slug). 5 open
  questions, the biggest being whether single-key kinds piggy-back.
- **[BX.11 — Account vs AccountTemplate 1:1-vs-1:N distinction](bx_11.md)**
  — surface the cardinality contract that today's identical chrome
  hides. **Rec: Direction D5** (layered: section-header prose + card
  badge + read-card cardinality band — say it three times). 6 open
  questions, the biggest being instance-count source (live query vs
  static declaration).

## Cross-cutting tensions

These are the seams where a lock in one cell forces a decision in
another. Operator should resolve these BEFORE locking individual
directions; otherwise a per-cell pick ricochets.

1. **BX.6 home framing × BX.7 nav vocabulary.** BX.7's recommended
   group-label rename (`Studio / Dashboards / Reference` →
   `BUILD / VIEW / REFERENCE`) shows up immediately on BX.6's home
   header prose ("The diagram link in the top nav" — which nav group
   carries it?). Diagram lives in BUILD per BX.0.7. If BX.7 ships the
   rename, BX.6's header prose should say "**BUILD → Diagram**" not
   just "the diagram link." **Resolve BX.7 nav rename first**, then
   BX.6 inherits the vocabulary.
2. **BX.6 home × BX.8 diagram embedding.** BX.6 Direction A
   deliberately punts on home-embedded diagram (CF.3.l lock — diagram
   was promoted to its own top-nav surface to escape iframe-cascade
   fragility). BX.8 then re-introduces the diagram inline, but on
   *edit pages*, not the home. If operator picks BX.6 Direction C
   (two-pane with diagram preview), BX.8's mini-diagram becomes
   redundant on the home-arrived path and creates two diagram-render
   pipelines (home preview + edit-page mini). **Locking BX.6 A keeps
   BX.8's mini-diagram the only inline-diagram surface.**
3. **BX.7 color × BX.10 breadcrumb chrome.** BX.10's breadcrumb shows
   `← back to Chains` + composite-key strip. If BX.7's color hue
   applies to nav only (D1), breadcrumb stays neutral. If a future
   cell extends the BUILD/VIEW palette to page chrome (header strip,
   back-links), BX.10's composite-key strip needs to decide whether
   it picks up the BUILD hue (it's authoring) or stays neutral. **No
   blocker today** — flag for the follow-on cell that extends color
   beyond the nav.
4. **BX.10 opaque IDs × BX.8 mini-diagram node click.** BX.8 Direction
   D2 + open question 2: "click-on-mini-diagram-node → navigate to
   /diagram?focus=<node-id>". Node ids for composite-key entities
   today are the human composite (`rail__CustomerInboundACH`). Once
   BX.10 lands opaque IDs, the diagram's `?focus=` query-param shape
   changes too — does focus take the hash or the composite? **Lock
   BX.10's URL shape first**, then BX.8 wires `?focus=h_a3f2e1` (or
   keeps focus on human ids and only the `/edit` URL goes opaque —
   either is defensible but needs an explicit pick).
5. **BX.11 card badge × BX.4 read-card upgrade.** BX.11 D2/D5 promote
   the existing rail-subtype badge slot for `Singleton` / `Pattern · N
   instances`. BX.4 (read-card upgrade, not part of BX.0.8 but on the
   same surface) may want to add its own badges in the same slot. **If
   BX.4 is in flight or planned**, coordinate badge taxonomy so the
   slot doesn't become a free-for-all (3+ badges on one card title is
   noise).
6. **BX.6 completeness rule × BX.11 instance count.** BX.6's typed
   `compute_home_completeness` helper proposes "count > 0 AND no
   validator errors". BX.11 D5 introduces a separate
   `instance_count_by_role` helper hitting `<prefix>_daily_balances`.
   These are two different "count" notions on the same page. **Surface
   them with distinct vocabulary** ("declared" for completeness, "runtime
   instances" for BX.11) — else operator reads `Accounts (16
   declared)` next to `Account templates (3 patterns → ~12 050
   runtime instances)` and asks "why isn't 16 a 'pattern' too?".
7. **`data-*` anchor taxonomy across all five cells.** Per
   `feedback_browser_drivers_user_facing_locators`, every cell invents
   anchors: BX.6 `data-step="N"` / `data-singleton="<kind>"` /
   `data-completeness="<kind>"`; BX.7 `data-nav-group="build|view|ref"`;
   BX.8 `data-role="diagram-edit-link"` / `data-role="mini-diagram-self"`;
   BX.10 `data-role="composite-key"`; BX.11 `data-role="cardinality-cue"`
   / `data-role="instance-count"`. **No collisions today**, but no
   convention either (`data-step` vs `data-role` vs `data-nav-group`).
   Recommend operator lock on a single attribute convention
   (`data-role="<noun>"` preferred — already the majority pattern) so
   App2Driver helpers can pattern-match instead of remembering five
   shapes.
8. **Per-template DB queries (BX.11) × Studio-no-DB mode.** BX.11 D5
   adds per-render DB hits for instance counts; BX.6 Direction A's
   completeness check (option `b` — validator-error count) is
   DB-free. Studio runs against a cfg without `demo_database_url` in
   the offline-iteration path (per
   `project_app2_parity_for_offline_iteration`). **BX.11's degradation
   path (badge becomes `Pattern` with no count) needs an explicit
   test fixture**; BX.6 has no such concern.

## Recommended review order (which to lock first)

Locking these in this order keeps cross-cutting tensions resolved by
the time their dependents fire.

1. **BX.7 (nav color + group-label rename).** Smallest, most
   self-contained, vocabulary lock that BX.6's home header prose
   references. ~2-3h impl after lock. No dependencies on the other
   four cells.
2. **BX.10 (opaque IDs URL shape).** URL shape is referenced by BX.8's
   `?focus=` query-param (open question 2) and by BX.6's "→ View
   diagram" prose (no direct dep but shape matters for any future
   "deep-link to a chain edit" in the home). ~4-6h impl after lock.
3. **BX.6 (home page Start-here).** Inherits BX.7's nav vocabulary;
   stands alone otherwise. ~3-4h impl. The typed
   `compute_home_completeness` helper this cell introduces is the
   foundation any future home-status widget reuses — get it landed
   early.
4. **BX.11 (Account vs AccountTemplate distinction).** Inherits BX.6's
   badge slot conventions (if BX.6 changes accordion chrome, BX.11's
   D5 card badges need to live inside the new shape); inherits the
   `data-role=` anchor convention from cross-cutting tension 7. ~5-7h
   impl (D5 is layered).
5. **BX.8 (diagram link-to-edit + mini-diagram).** Touches the most
   surfaces (diagram.js + every edit page); benefits from having
   BX.10's opaque-ID URL shape locked (mini-diagram node-click target
   format) and BX.7's BUILD-group hue locked (mini-diagram self-node
   accent should match the BUILD hue, not the global accent). ~4-5h
   impl.

Total estimated impl: **18-25h** if Recommendations are taken as-is.
Operator can deflate by skipping BX.11 D5's D4 layer (drops to D1+D2,
~3-4h) or BX.10 D5's slug suffix (drops to D, ~2h). No cell on the
critical path of any other; if one slips, the others ship.

## Open operator decisions

The decisions that genuinely need operator weigh-in (not just
implementer judgement calls). Grouped by cell, with my recommended
default where I have one — operator can take the default and move
or weigh in to override.

**BX.6:**
- Completeness rule for `set` badge: count>0 / +validator / +expected_count.
  **Default: validator-error rule** (option b).
- Confirm `account_template` precedes `account` in topological order.
  **Default: yes** (the materialization-from-role argument).
- Empty-state wizard treatment (Direction D) or universal numbered
  list. **Default: universal list**, follow-up cell for wizard if
  first-clone UX matters.
- Singleton tile copy strings. **Default: ship the proposed strings**
  inline ("Sets institution-wide identity..." / "Drives the QS color
  palette...").
- Persona singleton — returning post-BXa? **Default: design for 2
  tiles, expand if Persona comes back.**

**BX.7:**
- Reference hue: neutral grey or its own color? **Default: neutral
  grey** (`secondary_fg` token).
- Configurable group-hue mapping (`theme.nav_group_build`) or
  hardcoded `accent / success / secondary_fg`. **Default: hardcoded
  for v1**; revisit if an L2 override surfaces a real problem.
- Rename `entry.group` token values `authoring/viewing/reading` →
  `build/view/ref`. **Default: rename** (matches operator framing).
- Rename group-label chip text from `Studio/Dashboards/Reference` →
  `BUILD/VIEW/REFERENCE`. **Default: rename**.

**BX.8:**
- Click-on-mini-diagram-node behavior: navigate to focused full
  diagram / navigate to edit / no-op. **Default: navigate to focused
  full diagram** (consistent with the main diagram's
  "click-focuses-not-edits" lock). Operator must explicitly lock —
  default is a footgun.
- Mini-diagram on `limit_schedule` edit pages (no clean topology
  projection). **Default: skip mini-diagram on limit_schedule edit**;
  rail-link in body suffices.
- Edit-page back-link target when arrived from diagram: change to
  `← back to diagram focused here` via `?from=diagram`. **Default:
  yes** (mirrors `?from=triage`).

**BX.10:**
- Hash length 4 vs 6 chars. **Default: 6 chars** (collision detect at
  cache build is the safety net).
- Single-key kinds also opaque-ID. **Default: no**, keep scope tight
  to composite-key kinds only.
- Stale-bookmark fuzzy-match heuristic on 410. **Default: no fuzzy
  match** — clean 410 with current-chains list.
- Composite-key breadcrumb visibility: inline strip vs side-panel
  tucked. **Default: inline strip** (CPA needs to cross-ref to YAML).
- Copy-ID button on breadcrumb. **Default: ship**.

**BX.11:**
- Instance-count source: live transactions / live daily_balances /
  static template declaration. **Default: live daily_balances** with
  fallback to "—" when no DB.
- `Singleton` vocabulary on badge: keep / rename to `GL line` /
  `Ledger row`. **Default: keep `Singleton`** (matches existing
  glossary).
- Subledger-control accounts (parent_role set): `Singleton` or
  `Singleton · rollup` badge. **Default: `Singleton`** (avoid a third
  state; body's Parent role row carries the rollup signal).
- Per-template count zero state copy. **Default: `Pattern · awaiting
  first ETL`** when count=0.
- Side-panel glossary: add `pattern` term mirror to `singleton`.
  **Default: yes, one-line add** to `_side_panel.py::GLOSSARY`.

**Cross-cutting (not in any individual cell):**
- `data-*` attribute convention: standardize on
  `data-role="<noun>"` across all five cells (today some use
  `data-step` / `data-nav-group` / `data-singleton`). **Default:
  prefer `data-role="..."` as the canonical anchor**; cell-specific
  attrs only when they carry value beyond identity (e.g.,
  `data-completeness="set|empty|partial"` is a state, not just an
  anchor).
- Vocabulary disambiguation between BX.6 "declared count" and BX.11
  "runtime instance count". **Default: BX.6 reads `(16 declared)` or
  similar explicit qualifier**; BX.11 reads `(3 patterns → ~12 050
  runtime instances)` per its D1 prose.

## Post-lock autonomous-impl readiness

Once the operator locks directions + answers the open decisions above,
how implementable is each cell by a follow-on autonomous run?

| Cell | Direction | Autonomous? | Reason |
|---|---|---|---|
| BX.7 | D1 | **Yes — fully** | CSS-var + class change scoped to `emit_top_nav`; renames mechanical; no DB / external dep; ~2-3h. |
| BX.10 | E | **Yes — with one judgement call** | Hash + slug + 301 + 410 mechanical. Judgement: hash-collision-detect failure mode (assert + crash at cache build per design). Operator should pre-confirm that's the right loudness; otherwise autonomous. ~4-6h. |
| BX.6 | A | **Yes — with one design choice** | `compute_home_completeness` typed helper is straightforward. Open question: completeness rule (option b vs c) is the only fork; if operator picks b in the lock, fully autonomous. ~3-4h. |
| BX.11 | D5 | **Mostly — needs DB fixture verification** | D1 + D2 + D4 layered. The `instance_count_by_role` helper needs live-DB testing; fallback path (no DB) needs explicit fixture. Autonomous if operator pre-locks the instance-count source (open question 1) and the zero-state copy (open question 4). ~5-7h. |
| BX.8 | D2 | **No — needs visual verification loop** | Mini-diagram graphviz-tiny-canvas layout is empirical (the `size="6,4!"` / `ratio="compress"` tuning happens by looking at the output). Hover-badge SVG injection on graphviz output is finicky. Cold-read-style screenshot loop recommended. ~4-5h with screenshot iteration. |

**Net:** 4 of 5 cells are autonomous-ready after operator picks +
open-question answers. BX.8 needs a human-in-the-loop screenshot
iteration; everything else can run end-to-end without further
operator input.

**Pre-flight gotcha:** BX.7's pre-flight screenshots
(`topnav_*.png`) all crop above the actual top nav — the bar exists
in code but is not in any of the supplied frames. Implementer should
re-capture nav-inclusive screenshots before final visual sign-off;
the BX.7 doc designed against code, not the image.
