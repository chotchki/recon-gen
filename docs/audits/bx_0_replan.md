# BX.0 — Phase BX REPLAN (L2 Editor cold-read + address cycle)

> **Status:** REPLAN LOCKED 2026-05-30. Output of BX.0. Scopes the
> two-persona cold-read (BX.0.5a + BX.0.5b), inventories the route
> surface, locks Phase BF reconciliation rules, and names the
> follow-on cells BX.0.6 / BX.0.7 / BX.0.8 will produce.

## Headline

The L2 Editor has never been cold-read. BT's cold-read (`bt_cold_read.md`)
caught it tangentially via the Triage CTA destination — but the
operator's full editing flows (rail create / template edit / chain
composite-key navigation / singleton forms) were unexplored. **Phase
BX inverts BT's "design-first, cold-read-second" cadence to
"diagnose-first, design-second"**: BX.0.5a + BX.0.5b discover the
gaps; BX.0.6 + BX.0.7 + BX.0.8 plan the fixes; BX.1..N implement;
BX.M validates.

## Two-persona decision (operator-locked)

Per AskUserQuestion 2026-05-30, the operator picked **both personas**
(option C). The L2 Editor is genuinely serving two different
operational shapes, and one cold-read can't surface both.

### Persona A — ETL Engineer bounce-and-fix (`BX.0.5a`)

**Background:** Same first-time ETL Engineer from BT's cold-read.
Hands-on Python + SQL, never seen Recon-Gen before today. Got here
via a Triage card's "Open Rails editor" CTA (post-BTa.2.4, the link
points to `/l2_shape/rail/<offending-name>/edit` directly, not the
list home).

**Scenario:** They're on the edit page of the entity Triage flagged.
They need to:
1. Confirm the L2 says what Triage claims it says
2. Make ONE edit (rename a rail, add a metadata_key, fix a typo)
3. Save + bounce back to Triage to re-check the gap is gone

**UX lens:** **Minimize friction in the bounce-and-fix loop.**

**Pages they hit:** the edit page they landed on, the save flow, the
back-breadcrumb path (post-BTa.2.5, if it lands).

**What they DON'T see:** the `/` home, the list pages, the new-form
pages, the singletons. They might never visit the L2 Editor as a
top-level destination.

### Persona B — L2 author building from scratch (`BX.0.5b`)

**Background:** Implementation consultant standing up a new institution
in Recon-Gen for the first time. Or a developer at the institution
who's been told "configure this for our shop." Spent the last 2 hours
reading SPEC.md + the handbook. They know what an "L2" is, conceptually.
They've never used the editor.

**Scenario:** They start at `/`. They need to:
1. Configure the singletons (institution name, theme, persona block)
2. Add accounts + account templates for the institution's chart
3. Add rails (the bulk of the work — 20-30 per institution)
4. Add transfer templates that wrap multi-leg rails
5. Add chains where one rail's firing triggers another
6. Add limit schedules per (parent_role, rail, direction)
7. Eventually deploy + validate the dashboards come up green

**UX lens:** **Discoverable + complete.** They want guided creation,
not blank-form-staring. They need to know which fields are required,
which combinations are valid, which entities depend on which others
(rails before templates before chains).

**Pages they hit:** every page. `/` home, all 6 kind list pages, all 6
kind new pages, several edit pages, all 3 singleton pages, the
deploy button.

**What they DON'T see:** Triage / Probe / Run (BT/BTa surface) —
they get there later, after their first deploy fails.

## Route inventory (full L2 Editor surface)

From `_studio_editor_routes.py::make_editor_routes` + the singleton
routes:

| Route | Methods | Persona A | Persona B |
|---|---|---|---|
| `/` | GET | – | ✓ home |
| `/l2_shape/{kind}/` | GET | – | ✓ list |
| `/l2_shape/{kind}/` | POST | – | ✓ create |
| `/l2_shape/{kind}/new` | GET | – | ✓ new form |
| `/l2_shape/{kind}/{entity_id}` | GET | – | ✓ read card |
| `/l2_shape/{kind}/{entity_id}` | POST/PUT | ✓ save | ✓ save |
| `/l2_shape/{kind}/{entity_id}` | DELETE | – | ✓ delete |
| `/l2_shape/{kind}/{entity_id}/edit` | GET | ✓ edit form | ✓ edit form |
| `/l2_shape/instance/` | GET/POST | – | ✓ instance singleton |
| `/l2_shape/theme/` | GET/POST | – | ✓ theme singleton |
| `/l2_shape/persona/` | GET/POST | – | ✓ persona singleton |
| `/preview/markdown` | POST | – | helper (description preview) |
| `/diagram` | GET | – | ✓ (topology context) |

**Six entity kinds:** `account`, `account_template`, `rail`,
`transfer_template`, `chain`, `limit_schedule`.

**Field-richness ranking (drives screenshot priority):**

1. `rail` — TwoLegRail / SingleLegRail union, 15+ fields, four
   free-form textareas (`metadata_keys`, `posted_requirements`,
   `metadata_value_examples`, `leg_rail_xor_groups_text`).
   **Most likely to surface friction.**
2. `transfer_template` — 7 fields including the `leg_rail_xor_groups`
   structured-picker question (BF.3 in flight).
3. `chain` — composite-key `parent::children-csv` addressing (per
   `_entity_id` in `_studio_editor_routes.py`); per-child fan_in flag
   (AB.6).
4. `limit_schedule` — composite-key `parent_role::rail::direction`
   addressing (AB.1).
5. `account_template` — simpler, but the `instance_id_template` +
   `instance_name_template` placeholders are non-obvious.
6. `account` — simplest.

**Singletons (low-cardinality but high stakes):**

- `instance` — top-level description + role_business_day_offsets
- `theme` — structured form per BF.8 (color pickers)
- `persona` — structured form per BF.7 (institution / stakeholders /
  merchants / flavor / gl_accounts)

## Phase BF reconciliation rules (locked)

Phase BF has 6 cells in flight addressing known L2-editor defects:

| BF cell | Touches | BX reconciliation rule |
|---|---|---|
| BF.0 spike | per-field migration audit | **Keep until BX.0.5b lands.** If BX.0.5b's cold-read aligns with BF.0's audit, fold into BX.0.7 plan. If BX.0.5b finds different priorities, BF.0's audit is still useful as a field inventory reference. |
| BF.1 subtype-requirements banner | `/l2_shape/rail/new?subtype=<...>` + edit | **Likely subsumed by BX.0.7's design pass** — BX.0.5b will probably flag the subtype confusion. Hold BF.1 until BX.0.6 triage decides. |
| BF.2 BB.2 sub-form completeness | `_render_reconciler_section` | **Likely subsumed by BX.0.7.** BX.0.5b will probably probe the create-new-reconciler-with-rail sub-form and surface its own gaps. |
| BF.3 `leg_rail_xor_groups` picker | rail create form | **Possibly subsumed** — depends whether BX.0.5b flags the textarea as friction or not. |
| BF.4 remaining-textareas pickers | rail / transfer_template | **Same as BF.3.** |
| BF.5 driver alignment | tests/e2e | **Keep regardless of BX.** Driver-side cleanup is orthogonal to the operator-facing cold-read. |
| BF.10 composite-scalar field validation | rail / transfer_template | **Likely keep** — narrow scope (4 specific fields); BX.0.5b probably won't dive deep enough to flag. |
| BF.6 verify + close | phase exit | **Hold until BX.0.7** decides which BF cells survive. |

**The lock rule:** BX.0.7 (REPLAN-with-triage) makes the final call on
each BF cell — keep / collapse-into-BX / cancel. Until then, no BF
work fires (no point starting BF.1 if BX.0.7 might cancel it).

## Cold-read fixture setup

Same fixture both passes:

- Studio against `run/config.sqlite.yaml` + `run/sasquatch_pr.yaml`
- sqlite demo db pre-populated (the existing `run/spec_example.sqlite`
  has 12,844 rows from prior `data apply`)
- Port 8765 (matches BT.6's setup)
- Background process, killed at end of each pass

Screenshots saved per pass:

- `docs/audits/bx_cold_read_screenshots_v1a/` (bounce-and-fix)
- `docs/audits/bx_cold_read_screenshots_v1b/` (author)

Iterative loop: agent requests button-pushes, Claude takes Playwright
screenshots, agent reacts.

## BX.0.5a scope (bounce-and-fix, ~60-90 min)

**Setup:** Pre-pick an entity the BT cold-read flagged. From
`bt_cold_read.md`'s Triage section, `Unmatched rail_name` cards
included rail names like `ach` (a value that landed but isn't in
the L2). The bounce destination is wherever BTa.2.4's deep-link
fix points to (e.g. `/l2_shape/rail/` list page, or
`/l2_shape/rail/new` to add the missing rail).

**Initial screenshots:**

1. Triage card with CTA (carry over from BT.6 screenshots)
2. Landing page the CTA lands on (today: `/l2_shape/rail/` list)
3. The "+ Add" button → `/l2_shape/rail/new` form
4. Fill in the form + screenshot pre-submit
5. POST → screenshot post-save (redirect destination)
6. Try to bounce back to Triage — screenshot every step of the
   return path

**Agent iterates:** "what does the read card look like after save?
how do I edit a typo in the new entity I just added?
does the back-breadcrumb work?"

**Output:** `docs/audits/bx_cold_read_v1a_bounce.md` (~200-400 lines).

## BX.0.5b scope (author, ~90-120 min)

**Setup:** Same fixture, but the persona walks every kind from
scratch. They land at `/` and explore.

**Initial screenshots:**

1. `/` home (lists all entities + diagram)
2. `/l2_shape/account/` list
3. `/l2_shape/account_template/` list
4. `/l2_shape/rail/` list
5. `/l2_shape/transfer_template/` list
6. `/l2_shape/chain/` list
7. `/l2_shape/limit_schedule/` list
8. `/l2_shape/instance/` singleton
9. `/l2_shape/theme/` singleton
10. `/l2_shape/persona/` singleton
11. `/l2_shape/rail/new?subtype=two_leg`
12. `/l2_shape/rail/new?subtype=single_leg`
13. `/l2_shape/transfer_template/new`
14. `/l2_shape/chain/new`
15. `/l2_shape/limit_schedule/new`

**Agent iterates:** "show me what saving a rail looks like + the
validation errors when I leave required fields blank + the edit
view of an existing rail + the diagram update + the description
field's markdown preview toggle."

**Output:** `docs/audits/bx_cold_read_v1b_author.md` (~500-800
lines).

## BX.0.6 / BX.0.7 / BX.0.8 framework

- **BX.0.6** — Operator triages BOTH cold-reads inline (same pattern
  as `bt_cold_read.md`'s inline comments).
- **BX.0.7** — REPLAN-with-triage. For each green-lit recommendation,
  pick (a) BX.X cell name + (b) whether it subsumes a BF cell.
  Final output: enumerated BX.1..N + updated BF status table.
- **BX.0.8** — Agent-driven design mockups for design-heavy fixes
  only. Output: `docs/audits/bx_design_mockups.md`.

## Cold-read v2 (BX.M)

Same iterative-screenshot pattern as both v1 passes, run after
BX.1..N ship. Persona: pick whichever of A/B has more lingering
findings post-implementation (probably B — larger surface = more
chances for missed corners). If both have lingering findings,
two passes again.

## Out of scope for BX

- Browser-based diagram authoring (drag-and-drop on the topology) —
  separate UX phase if it ever happens.
- L2 yaml direct-edit textarea — that's the bypass for power users;
  not on the cold-read path.
- Multi-user concurrent editing (locking, conflict resolution) —
  Studio is single-user by design.
- Studio-as-API (programmatic access) — out of scope; CLI handles
  that path.
