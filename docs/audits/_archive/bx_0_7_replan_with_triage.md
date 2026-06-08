# BX.0.7 — REPLAN with operator triage (post-cold-read v1b + BXa)

> **Status:** REPLAN LOCKED 2026-05-30. Output of BX.0.7. Enumerates
> the BX.1..N implementation cells from the operator's inline triage
> on `bx_cold_read_v1b_author.md`, factoring out items already
> resolved by Phase BXa (persona nuke). Names items gated by BTa.1
> (side-panel infra) vs independently-launchable; flags BX.0.8 design
> mockup needs.

## Headline

23 cold-read recommendations + 2 §1-§2 inline comments triaged.
After BXa absorbed 3 items (P1.4 + P3.5 + P3.6 — all persona-related
and now moot), the BX backlog is:

- **5 items ship as-is** (operator agreed straight, no design twist)
- **5 items need design work** (mockups or operator-direction calls)
- **5 items gated by BTa.1 side-panel** (vocabulary defs + plain-
  language errors + chip tooltips — they consume the side-panel
  infrastructure)
- **2 items deferred** (operator marked "defer for now" / "TBD")
- **1 NEW item from operator comments §1 + §2** (BUILD/VIEW top-nav
  grouping + Account vs AccountTemplate 1:1-vs-1:N distinction)

## BX cell enumeration

### Independent (can fire on defaults — minimal design risk)

- **BX.1 — Delete-confirm inline banner with timed auto-cancel**
  (P1.3, operator: "if we could design it inline with a timed auto
  cancel I'd be way happier"). Banner appears at top of the
  edit/list page on Delete click; 5-sec countdown; explicit Cancel
  button + auto-dismiss. NO modal. Reference-check listing
  downstream consumers before allowing the destructive action.
  Estimated 2-3h.

- **BX.2 — Save-success always redirects to read card** (P1.5).
  Edit POST handler 303s to `/l2_shape/<kind>/<entity_id>` (read
  card) instead of `/`. Create POST 303s to the new entity's read
  card too. Touches the editor save flows. Estimated 1-2h.

- **BX.3 — Rail list table view + group-by-source_role** (P2.5).
  Toggle on `/l2_shape/rail/` between dense table (default) and
  today's card grid. Sortable columns; row click opens read card.
  Estimated 2-3h.

- **BX.4 — Read-card visual upgrade matching edit-form sectioning**
  (P3.1). Today's read cards are flat; they should mirror the edit
  form's sectioning so toggling between view + edit doesn't feel
  like two different shapes. Estimated 1-2h.

- **BX.5 — Inline currency formatting on cap fields** (P3.2).
  `currency=True` annotations on the relevant ColumnSpec fields
  (Cap on LimitSchedule, similar). Reuses existing
  `cents_to_dollars_sql` / format helpers. Estimated 30-60 min.

### Need design work (operator agreed in principle but called out twist)

- **BX.6 — Home with "Start here" flow + singleton tiles** (P2.1,
  operator: "we will need design work, especially where should this
  relate to the diagram?"). Numbered dependency order
  (singletons → accounts → rails → templates → chains → limits) +
  completeness checkmarks per kind + singletons promoted to
  top-level (not buried under accordion). Open question: how does
  the diagram relate — embedded? sidebar? linked?
  **BX.0.8 mockup needed.** Estimated 3-4h after mockup.

- **BX.7 — Top nav BUILD/VIEW split + grouping/color** (P2.2 +
  §1 operator comment "I like this segmentation, maybe group the
  top nav parts and color code"). BUILD half (L2 Editor / ETL
  Support / Training), VIEW half (L1 Dashboard / L2 Flow Tracing /
  Investigation / Executives), Docs as a third group. Color-code
  by group with accessible contrast.
  **BX.0.8 mockup needed.** Estimated 2-3h after mockup.

- **BX.8 — Diagram node link-to-edit + inline mini-diagram on edit
  pages** (P2.3, operator walked back to: "link to edit page; the
  click-to-filter is in tension with this"). Diagram nodes get a
  hover "→ Edit" link (NOT click-to-jump, which would collide with
  the click-to-focus behavior). Per-entity edit page gets a small
  inline diagram showing "this is where you sit" — current shape
  preview with the edited entity highlighted.
  **BX.0.8 mockup needed.** Estimated 4-5h after mockup.

- **BX.9 — Theme live-preview + section save** (P2.4). Each color
  picker pair (bg/fg) shows a live preview chip with actual
  contrast. Per-section Save button (Identity / Data colors / UI
  palette / Brand assets) so operator doesn't lose all-or-nothing
  on a long form. Estimated 3-4h.

- **BX.10 — Composite-key opaque IDs in URLs** (P2.6, operator:
  "deeper implied impact since the name is the id... don't want
  this bleeding into the shape yaml"). URLs use opaque hashes
  (e.g. `/l2_shape/chain/h_a3f2e1/edit`); composite-key human
  identifiers stay in breadcrumbs/titles. The YAML keeps using
  name-based identifiers (no shape change). Mapping table lives
  in-memory at request time.
  **BX.0.8 mockup needed** (URL shape + breadcrumb design).
  Estimated 4-6h after mockup.

- **BX.11 — Account vs AccountTemplate 1:1-vs-1:N distinction**
  (§2 operator comment: "we're not saying that accounts are
  DIFFERENT than templates"). Visual + copy that surfaces the
  cardinality distinction (one singleton-account == one institution
  GL row; one template == N instantiated DDAs). List page header
  prose + a small annotation per kind.
  **BX.0.8 mockup probably needed.** Estimated 2-3h after mockup.

### Gated by BTa.1 side-panel (consume the infrastructure)

These don't fire until BTa.1 ships:

- **BX.12 — Vocabulary pass: side-panel definitions for opaque
  terms** (P2.8 — operator routed to side-panel). Per-field `[?]`
  triggers next to Rail's `Posted requirements` / `Bundles
  activity` / `Cadence` / `Origin overrides`; Chain's `XOR` /
  `fan-in` / `epc`; LimitSchedule's `Direction`. Each opens the
  BTa.1 side panel with the banker-translation. Single
  `GLOSSARY: dict[str, str]` source of truth in
  `_studio_side_panel.py` shared with the top-nav glossary
  (operator's §2.b drift concern). Estimated 2-3h.

- **BX.13 — Per-field "surfaces as:" pointers** (P3.5 — operator
  routed to side-panel + "persona still needs research." Persona
  research done; the pointers now apply to `institution_name` +
  `description` markdown + `theme.*` color fields). Side-panel
  help text per field shows "this surfaces on: <list of pages>"
  so operator knows what's affected by a change.
  Estimated 1-2h.

- **BX.14 — Plain-language error messages with SPEC pointers in
  parens** (P3.9 — operator: "is this a good sidebar doc help
  item?"). Validator errors today read as SPEC-section codes
  (`R12`, `C8a`); rewrite to plain English + parenthetical
  `(SPEC §R12)` at the end. Long-form explanation lives in the
  side panel via a `[?]` next to the error message.
  Estimated 1-2h.

- **BX.15 — Coverage / Trainer chip tooltips** (P3.10 — operator:
  "is this a good sidebar doc help item?"). The diagram's
  `Coverage` + `Trainer` toggle chips get `[?]` icons opening the
  side panel with what they do + when to use them.
  Estimated 30-60 min.

- **BX.16 — Inline shape-preview on chain form** (P3.8). When
  the operator toggles children checkboxes, a tiny inline
  parent → child arrow diagram updates. Could live in side panel
  OR inline; defer to BX.0.8 design pass.
  Estimated 2-3h.

### Defaults-ship polish (cluster — one cell)

- **BX.17 — Polish cluster** (P3.3 + P3.7 + P3.4). Three small
  items that operator agreed to but don't need design work, bundle
  for efficiency:
  - **P3.3 Duration picker** with common picks (P1D / P3D / PT1H)
    + free-form fallback (operator: "a couple common picks with
    the fallback of free form would be wise")
  - **P3.4 Reference panels default-open on first visit, MVP
    gate "only show if NOTHING is defined yet"** (operator: "Sure
    but the challenge will be where to persist that. Maybe as an
    MVP only show if NOTHING is defined yet"). Per-kind list page
    is non-empty → Reference panel default-collapsed; empty list
    → default-open. Zero persistence needed.
  - **P3.7 Completion-expression DSL autocomplete** on
    transfer_template form (operator: "Probably would help the end
    user a lot"). Datalist or inline JS dropdown showing the v1
    vocabulary (`business_day_end`, `business_day_end+1d`,
    `month_end`, `metadata.<key>`).
  Estimated 3-4h total.

### Deferred (operator marked defer / TBD)

- **DEFER: P3.6 `+ Add stakeholder` button styled as button**
  (operator: "TBD depending on the persona research"). Persona
  research done via BXa. The button no longer exists (form
  deleted); skip entirely. Note: GL accounts' "+ Add row" remains
  but isn't operator-visible-broken per the cold-read.

- **DEFER: P3.5 "surfaces as:" on Persona Stakeholders / Flavor /
  Merchants** (operator: "TBD depending on the persona research").
  Persona fields nuked by BXa. Apply the pattern to
  `institution_name` + `theme.*` instead per BX.13 above.

(Both P3.5/6 already factored out from BX cell list.)

### Already absorbed by BXa (do not enumerate)

- **P1.1 Instance singleton structured form** — done in BXa.2
- **P1.4 Persona Stakeholders → Correspondents rename** — moot (field
  removed)
- **P3.5 "surfaces as:" pointers on Stakeholders/Flavor/Merchants**
  — moot (fields removed)
- **P3.6 + Add stakeholder button styling** — moot (form removed)

### Cold-read v2 cell (phase exit)

- **BX.18 — Cold-read v2** (same iterative-screenshot pattern per
  `[[feedback_cold_read_iterative_screenshots]]`). After BX.1..17
  ship, re-walk the L2 Editor with the implementation-consultant
  persona; assert headline P1s closed; flag new findings.
  Operator iteration + sign-off → phase exit. Estimated 90-120 min.

## Sequencing

Total estimate: **~35-50h** focused work + 90-120 min cold-read v2.

Suggested order:

1. **BX.0.8 — Agent-driven mockup session** for the 4 design-needed
   cells (BX.6 home / BX.7 top-nav / BX.8 diagram / BX.10 composite
   URLs / BX.11 Account-vs-template). One mockup doc covers all.
   ~90-120 min agent work + operator review.
2. **BX.1-5 in parallel** with BTa.1 implementation (different
   files surface, no collision).
3. **BX.17 polish cluster** in parallel with the above.
4. **BX.6-11 implementation cells** (waited on BX.0.8 mockup).
5. **BX.12-16 gated cells** (wait until BTa.1 side-panel ships).
6. **BX.18 cold-read v2** at the end.

Phase BX implementation can run in parallel with BTa — different
file surfaces (BX is /l2_shape/*, BTa is /etl/*). The only shared
dependency is BTa.1 (side-panel infra) blocking BX.12-16.

## BX.0.8 mockup brief (handed to agent next)

Mockup scope: 5 design-needed cells (BX.6 + BX.7 + BX.8 + BX.10 +
BX.11). Pattern matches BTa.0.5 — comprehensive single-pass
briefing per `[[feedback_agent_driven_design_works]]`; ASCII
wireframes per cell; per-cell "Data sources" sub-section where
needed; one or two open questions per cell for operator review.

Output: `docs/audits/bx_design_mockups.md`. ~600-900 lines
expected.

## BF reconciliation (final lock from BX.0)

BX.0 promised to re-evaluate Phase BF cells (BF.0-6 + BF.10) at
BX.0.7. Verdict:

| BF cell | Status | Verdict |
|---|---|---|
| BF.0 spike | pending | **CANCEL** — superseded by BX.0.5b cold-read findings |
| BF.1 subtype-requirements banner | pending | **CANCEL** — superseded by BX.13 per-field surfaces (the rail-subtype prose belongs in the side panel) |
| BF.2 BB.2 sub-form completeness | pending | **KEEP** — cold-read didn't probe deep enough to validate or refute; orthogonal to BX |
| BF.3 leg_rail_xor_groups picker | pending | **KEEP** — narrow free-form-textarea→picker; operator-facing fix per BB.2 |
| BF.4 remaining-textareas pickers | pending | **KEEP** — same shape as BF.3 |
| BF.5 driver alignment | pending | **KEEP** — orthogonal to BX (driver-side) |
| BF.10 composite-scalar fields validation | pending | **KEEP** — narrow scope (4 specific fields) |
| BF.6 verify + close | pending | adjusts to **close BF.2-5 + BF.10 only** (BF.0 + BF.1 cancelled) |

Net: BF shrinks from 8 cells to 6 (BF.0 + BF.1 cancelled by
BX.0.7); the remaining BF cells stay as-is + can fire any time.

## Out of scope for BX (re-confirmed)

- BX.0.5a bounce-and-fix cold-read — deferred until BTa.2.4 lands
  (need the fixed deep-link first)
- Browser-based diagram authoring (drag-and-drop) — separate UX
  phase
- L2 yaml direct-edit textarea — power-user bypass; not on cold-
  read path
- Multi-user concurrent editing — Studio is single-user
- Studio-as-API — CLI handles that
