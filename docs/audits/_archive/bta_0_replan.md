# BTa.0 — Phase BTa REPLAN (post-cold-read v1 triage locks)

> **Status:** REPLAN LOCKED 2026-05-30. Locks four cross-cutting design
> decisions before BTa.1-6 fire. Output of BTa.0.

## Headline

Phase BTa addresses 18 of 20 BT cold-read recommendations per the
operator's inline triage on `docs/audits/bt_cold_read.md`. Before
BTa.1+ implementation fires we need to commit to four cross-cutting
shapes that several cells will depend on. This doc locks those
four shapes + names the rejected variants so BTa.0.5's design
mockup session has clear constraints.

## Lock 1: side-panel pattern (foundation for BTa.4/5 diagrams + glossary)

**Decision:** slide-out drawer from the right edge of the viewport.

Combines three operator-confirmed asks:
- P3.7 chain arrow diagram (parent → child visual on the Probe page)
- workflow.3 glossary popover (definitions for L2, Rail, Hook,
  Matview, LimitSchedule, Chain, Slice, Singleton)
- Cross-cutting per-page help text (operator's note on workflow.3:
  "side panel with additional help text will reduce the need for
  a separate doc site")

**Shape:**
- Right-edge drawer, ~30-35% viewport width when open, full
  viewport height
- Triggered by clickable "?" or "Help" affordance next to field
  labels OR by entity-context anchors (e.g. a "?" next to "Slice"
  on the Probe radio group opens the slice glossary entry)
- Content fetched via `hx-get` returning HTML fragments (matches
  existing Studio editor pattern — `?embed=1` query suffix)
- Dismissable: X button top-right + Escape key + click outside
  (the X is keyboard-accessible by default focus trap)
- ARIA: `role="complementary"` on the drawer container; focus
  trap on open; focus returns to the trigger element on dismiss
- Content lifecycle: glossary content is static; entity-specific
  help is rebuilt per open (`hx-get` fires every time)

**Rejected variants:**
- Bottom drawer (eats too much vertical space on the data-dense
  Probe / Triage pages)
- Modal overlay (operator explicitly rejected modals in P1.2 ETL
  Run confirmation — "I HATE modals")
- Sidebar always-pinned right column (the existing two-column
  Probe layout would have to compete; the drawer-on-demand keeps
  the default layout intact)
- Tooltip popovers attached to triggers (don't support multi-
  paragraph help; can't host a diagram)

**Where to put the source:** new `src/recon_gen/common/html/_studio_side_panel.py`
helper exporting `render_side_panel_drawer(trigger_id, content_url)` +
`side_panel_javascript_snippet()` + `side_panel_css_classes`. BTa.4/5
consume it; BTa.6 reuses the same helper for Run-page help text
in-place; BTa.3 reuses it for the landing-page tutorial expansion
if mockups go that way.

## Lock 2: numbered-landing + tutorial banner (BTa.3 combined surface)

**Decision:** replace the 3-equal-cards layout on `/etl/` with a
3-numbered linear flow + a dismissable "First time here?" banner
above it.

The two operator-confirmed asks (P2.1 + workflow.1) live on the
same surface; one design covers both.

**Shape:**
- Banner: `<div role="alert">` above the numbered cards. Dismiss
  button (X). Body: "First time here? Walk the Refresh → Triage →
  Probe loop." with a "Show me how" link that expands into a
  5-step inline checklist (collapsed by default; expands inline,
  not in the side panel — the tutorial belongs to the landing
  page, not a global drawer). Dismissal persists in `localStorage`
  per-deployment (key: `recon-gen-etl-tutorial-dismissed:<deployment_name>`).
- Numbered cards: same three workflows in linear order with
  `1.` / `2.` / `3.` numerals + `→` arrow between each card. Card
  internals stay close to today's shape (title + short description
  + link); the numerals + arrows do the workflow-narrative work.

**Rejected variants:**
- Vertical timeline (eats too much vertical space; the cards-with-
  arrows pattern is the standard onboarding-flow shape and reads
  faster)
- Numbered cards without the arrows (operator emphasized "tell me
  this is a sequence" — numerals alone aren't enough; the arrows
  signal "do this, THEN this")
- Tutorial as a modal (rejected per Lock 1 + operator's modal
  aversion)
- Always-visible tutorial (clutters the page for return visitors;
  dismissable-with-persistence is the right cost/benefit)

## Lock 3: Triage group-by (BTa.4 reshape)

**Decision:** group cards by gap kind (4 collapsible accordion
sections); within each group, sub-sort by row count DESC.

Addresses operator-confirmed P2.7 ("Finding a common group-by
method makes complete sense otherwise its just noise") + P2.8
volume badges + P3.5 distinct color/icon per gap kind + P3.6
columnar sample rows.

**Shape:**
- Top of `/etl/triage`: 4 accordion section headers, one per gap
  kind:
  - `Unmatched rail_name` (N rows total · M cards)
  - `Unmatched template_name` (N rows total · M cards)
  - `Missing LimitSchedule` (N rows total · M cards)
  - `Missing metadata key` (N rows total · M cards)
- Each section's body is a list of cards for that kind, sorted by
  card row-count DESC (biggest gaps first).
- Cards inside a section drop the per-card `declared_rails:` /
  `declared_templates:` block (which was the noise complaint —
  ~60× repeated text). The kind-section header carries the
  declared-set in a collapsible "What does the L2 declare?"
  sub-panel.
- Per-card title gets a volume badge: `<rail name> • 256 rows`.
- Per-kind color/icon: a small left-edge stripe + icon per kind.
  Color choices preserve accessibility (don't rely on
  red-vs-green-only — use shape + label too).
- Sample row block moves from the JSON-ish dump to a tight 2-col
  table (column / value), max 5 rows shown, "+ N more" if cut.

**Rejected variants:**
- Group by entity (one section per rail, with all gaps on that
  rail underneath) — confusing because most rails participate in
  only one gap kind, so most sections would have one card.
  Kind-first is the higher-signal sort.
- Flat list with chip filter at top — keeps the density problem
  on first paint; accordion default-collapse moves the wall of
  cards behind one click.
- Default-expanded accordion — bring back the density problem.
  Default-collapse all 4 sections; show the section counts so the
  operator picks which to open first.

## Lock 4: referer-based back-breadcrumb (BTa.2.5)

**Decision:** query-string carryover, NOT HTTP Referer header.

Operator green-lit P1.5 conditional on "if it's easy based on
referer." Investigation: HTTP Referer is unreliable across form
POSTs (the POST's Referer is the edit form URL, not the page that
sent the operator to the edit form). Query-string carryover is
~5 lines of code, deterministic, and survives the POST → redirect
round-trip cleanly.

**Shape:**
- Triage card CTAs append `?from=/etl/triage` when linking to the
  editor: `/l2_shape/rail/<name>/edit?from=/etl/triage`.
- Editor pages check for `?from=` and render a sticky "← Back to
  Triage" link at the top of the page (sticky = stays visible
  during scroll, like a thin breadcrumb bar).
- Validation: `from` value MUST start with `/etl/` (rail-edit only
  accepts back-links to the ETL Support pages; prevents open-
  redirect to arbitrary external URLs).
- POST handler preserves `?from=` in its redirect target: after
  save, redirect to the read card with `?from=/etl/triage` preserved
  so the back-link survives one save round-trip. Operator can use
  it to "save then go back" in one click.
- Three back-link kinds:
  - `?from=/etl/triage` → "← Back to Triage"
  - `?from=/etl/probe?kind=...&name=...` → "← Back to Probe"
  - `?from=/etl/run` → "← Back to Run"

**Rejected variants:**
- HTTP Referer header (doesn't survive form POSTs cleanly)
- Session storage / in-process dict (Studio is single-user; works,
  but is opaque to the URL — operator can't bookmark/share a
  "back to triage from this rail's edit" state)
- Hidden form field (works but invisible in the URL — same
  bookmarkable-state argument)
- Browser history `back` (operator may have other tabs in their
  history; `?from=` is the explicit + predictable signal)

## Sequencing implications

- **BTa.1 side-panel infra** lands first (Lock 1 → foundation).
- **BTa.2 P1 cluster** lands next (Lock 4's `?from=` plumbing is
  cheap + unblocks the Triage CTA fix).
- **BTa.3 landing rework** lands next (Lock 2). Reuses the side
  panel ONLY if the tutorial expands more than the inline 5-step
  shape suggests (mockup decides).
- **BTa.4 Triage rework** (Lock 3) and **BTa.5 Probe polish** (uses
  Lock 1's side panel for the chain diagram) and **BTa.6 Run polish**
  can run in parallel after BTa.1+2 land.
- **BTa.7 cold-read v3** runs after all the above ship.

## Out of scope for BTa (re-confirmed)

- workflow.2 reverse-link from dashboards → ETL Support (deferred
  to Backlog 2026-05-30 per operator)
- workflow.4 snapshot/restore around Run (challenging non-SQLite)
- P3.1 qsgen-sqlite hover-tip (cfg.yaml readdress phase)
- P3.2 Probe empty-state copy wording nit (defer)

## Next: BTa.0.5

Hand BTa.0 + cold-read § 7-9 (with operator's inline triage marks) +
the BT screenshots to the design-mockup agent. Per
`[[feedback_agent_driven_design_works]]`, brief once
comprehensively. Mockup deliverables per cell:

- (a) Numbered landing + tutorial banner (BTa.3)
- (b) Side-panel surface (BTa.1) — both entity-help + glossary
  variants, since the same drawer hosts both
- (c) Triage card group-by + volume badge + columnar sample +
  color/icon (BTa.4)
- (d) Run page with "Refresh Data" rename + hook-attribution +
  per-stage timings + flash + bell (BTa.2 + BTa.6)
- (e) Probe with radio definitions + filtered dropdown + date chips
  + chain-diagram-in-side-panel (BTa.5)
- (f) "← Back to Triage" sticky breadcrumb on the L2 editor
  (BTa.2.5)

Output: `docs/audits/bta_design_mockups.md`.
