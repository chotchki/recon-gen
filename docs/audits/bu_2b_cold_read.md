# BU.2b — 20-entry registry HTML cold-read

**Date:** 2026-05-30
**Reader:** Claude (HTML source, 62 rendered files in `/tmp/bu_html_coldread/`)
**Scope:** Trainer surface after BU.2b 20-entry registry expansion + BU.4 stage 1 polish.
**Method:** Source HTML cold-read (no clicks). Cross-checked against `bu_0_replan.md` Locks 1-11, `bu_cold_read.md` prior findings, and the SoT docs (`L1_Invariants.md`, `L2_Triage_Gaps.md`).

---

## Headline verdict: **SOFT-PASS-WITH-FIXES**

The shared `_render_plant_page` / `_render_tour_page` / accordion-landing data-drive holds across all 20 entries — the abstraction is real and the BU.1.10 form-reset bug, BU.4-stage-1 breadcrumb fix, and reset-copy rewrite are confirmed. **The surface is structurally healthy.**

But it is **not externally shippable** because of two compound-at-20x typed-source defects that affect 11 of 20 kinds:

- **P1-A** — markdown attr-list anchors leak into every title surface (`Per-direction flow cap {: #5-per-direction-flow-cap}`).
- **P1-B** — internal phase numbers leak into every title surface (`(AB.2.3)`, `(AB.3.3)`, `(AB.4.7)`, `(AB.6.5)`).

These root in `InvariantSection.title` parsing (Lock 8's typed source) — fix once, fixes all 11. The brief said "if its root is in the typed source — flag the SOURCE." Both are typed-source bugs.

Beyond P1, there's a meaningful P2 cluster around: missing `kind_qualifier` discrimination for the 4 shared-section pairs (Lock 8 explicitly designed this and it isn't rendering), empty "What to do" body for every L1 entry (the parser is dropping the SoT's `**What to do:**` blocks), and a few asymmetric / implementation-y form primitives.

**Counts:** 4 P1, 14 P2, 9 P3 = 27 findings. Tight against the brief's 30-50 expected.

---

## P1 — block release

### P1.1 — Mkdocs attr-list anchors leak into 11 plant pages + landing + tour titles

**Where:** every `plant_*.html` and `tour_*.html` for `limit_breach_outbound`, `limit_breach_inbound`, `chain_parent_disagreement`, `xor_group_missed`, `xor_group_overlap`, `fan_in_missing_parent`, `fan_in_extra_parent`; same trail visible in `landing.html`.

**What's wrong:** the parsed title leaves the mkdocs attribute-list syntax intact:
- Browser tab title: `Studio · Training · Per-direction flow cap {: #5-per-direction-flow-cap}`
- `<h1>`: `Per-direction flow cap {: #5-per-direction-flow-cap}`
- Landing accordion entry: same text
- Tour page tab title: same text

SoT source (`src/recon_gen/docs/L1_Invariants.md:138`): `### 5. \`{{ l2_instance_name }}_limit_breach\` — Per-direction flow cap {: #5-per-direction-flow-cap}`. The `{: #anchor}` syntax is python-markdown's `attr_list` extension — it should be stripped during section parsing, never rendered as title.

**Fix:** in `common/handbook/invariants.py` (the title parser), strip trailing `\s*\{:\s*#[^}]+\}` after the section-numbering + matview-name + ` — ` split.

### P1.2 — `(AB.x.y)` phase markers leak into 7 titles + landing + tour

**Where:** all chain-coherence titles — `chain_parent_disagreement` (AB.2.3), `xor_group_missed` / `xor_group_overlap` (AB.3.3), `fan_in_missing_parent` / `fan_in_extra_parent` (AB.4.7), `multi_xor_missed` / `multi_xor_overlap` (AB.6.5). Same root.

**What's wrong:** title parser strips `(M.2b.X)` (stuck_pending / stuck_unbundled SoT lines carry these and render clean) but keeps `(AB.x.y)` shapes — likely a regex anchored on certain phase-letter prefixes. Violates the user's "no sprint archaeology in operator copy" rule.

**Fix:** broaden the parenthetical-suffix strip in the title parser to any `\(\s*[A-Z]{1,3}[a-z]?\.\d+(?:\.\d+)?(?:\.\d+)?\s*\)\s*$` shape. Fixes P1.2 in one place.

### P1.3 — Every L1 entry's "What to do about it" body is empty

**Where:** `plant_drift.html:38`, `plant_ledger_drift.html`, `plant_overdraft.html`, `plant_limit_breach_*.html`, `plant_stuck_*.html`, all chain-coherence pages, `plant_supersession_audit.html`. 15 of 20 entries.

**What's wrong:** the page renders the section frame `<h2>What to do about it</h2><p class="text-sm m-0"></p>` with empty `<p>`. SoT has the content: `L1_Invariants.md:67-70` for drift literally says "Diff the day's transactions for `account_id` against the stored balance — the gap is missing or duplicated postings on that account-day…". L2 Triage entries (`phantom_rail`, `phantom_template`, `missing_metadata_key`, `uncovered_rail`, `uncovered_template`) DO render this section correctly because their parser (`common/handbook/l2_triage_gaps.py` per Lock 8) extracts `**What to do:**` paragraphs.

So `InvariantSection`'s parser isn't extracting the `**What to do:**` paragraph from `L1_Invariants.md` even though the same shape exists at the L2 Triage parser. Bug is on the L1 side of the typed-section parsing.

**Fix:** mirror the L2 Triage parser's `**What to do:**` paragraph-extraction logic into `common/handbook/invariants.py`. The SoT prose is already written; the renderer just needs to pull it through.

### P1.4 — Shared-section `kind_qualifier` is not rendered for 8 chain-coherence sub-kinds

**Where:** title duplicates across 4 pairs:
- `plant_limit_breach_outbound.html` / `plant_limit_breach_inbound.html` — both titled `Per-direction flow cap {: #5-per-direction-flow-cap}`
- `plant_xor_group_missed.html` / `plant_xor_group_overlap.html` — both `Multi-mode template variant XOR violation (AB.3.3) {: #xor-group-violation}`
- `plant_fan_in_missing_parent.html` / `plant_fan_in_extra_parent.html` — both `Fan-in chain parent-set mismatch (AB.4.7) {: #fan-in-disagreement}`
- `plant_multi_xor_missed.html` / `plant_multi_xor_overlap.html` — both `Chain XOR alternation violation (AB.6.5)`

**What's wrong:** Lock 8 (BU.0 round-4) explicitly designed this: "the registry row adds a `kind_qualifier: str | None` field (e.g. `\"missed firing\"` / `\"overlap firing\"`) that the renderer appends to the title (`<h1>{section.title} — {entry.kind_qualifier}</h1>`)." Either the qualifier wasn't added to the 8 affected registry rows, OR the renderer isn't appending it.

Without this, the operator landing on `plant_limit_breach_inbound` sees the SAME `<h1>` they'd see on `plant_limit_breach_outbound`. The ONLY discriminator is one form-field label change ("Inbound flow ($)" vs "Outbound flow ($)") — and for the 3 chain-coherence pairs the form fields are IDENTICAL (`days_ago` only), so the operator literally cannot tell which sub-kind they're planting except by reading the URL.

This is the brief's exact P1 priority-1 scenario: "registry kind `limit_breach_outbound` resolves to section title `Per-direction flow cap` — does the plant page tell the operator which DIRECTION they're planting?" Answer: no.

**Fix:** Implement Lock 8's `kind_qualifier`. Suggested copy:
- `limit_breach_outbound` → `Outbound direction`, `limit_breach_inbound` → `Inbound direction`
- `xor_group_missed` → `Missed-firing variant`, `xor_group_overlap` → `Overlap-firing variant`
- `fan_in_missing_parent` → `Missing-parent variant`, `fan_in_extra_parent` → `Extra-parent variant`
- `multi_xor_missed` → `Missed-firing variant`, `multi_xor_overlap` → `Overlap-firing variant`

Render: `<h1>{section.title}{ — qualifier if any}</h1>`. Browser tab title same shape.

---

## P2 — next polish cycle (BU.4 stage 2 or BU.6)

### P2.1 — Empty-primitive entries render an inputs-less form that looks broken

**Where:** `plant_missing_metadata_key.html:23-25`, `plant_uncovered_rail.html`, `plant_uncovered_template.html`. The `<form>` block is literally `<form>\n  \n  <div>button</div>\n</form>` — a blank gap then the submit.

**What's wrong:** the operator sees "Plant scenario" header, a section box, no fields, a button. Reads as "wait, the form failed to load" rather than "this plant has no operator-tunable knobs."

**Fix:** when `len(entry.primitives) == 0`, render a one-line `<p class="text-xs text-secondary-fg">This plant has no operator-tunable parameters — click Plant to seed the canonical scenario.</p>` between the form heading and the submit button.

### P2.2 — Post-POST banner echoes implementation kind, not what was actually planted

**Where:** all 20 `plant_*_after_post.html` — every one renders `Planted <kind> successfully` with the bare kind string. E.g. `Planted limit_breach_outbound successfully` — uses the registry kind, NOT the title and NOT the submitted kwargs.

**What's wrong:** brief explicitly asked "does the banner echo intelligent kwarg-name-and-value pairs?" — no. After planting `drift` with `days_ago=5, delta_money=75.00` the operator gets `Planted drift successfully`. No confirmation of WHAT they planted.

**Fix:** echo title + form values: `Planted "Sub-ledger drift" — days ago: 5, drift amount: $75.00`. Use the primitive's `label` (not `name`), format Decimal with `$` prefix when the primitive is `PrimitiveDecimalField`.

### P2.3 — `phantom_template` is asymmetric with `phantom_rail` — missing `template_name` field

**Where:** `plant_phantom_template.html:23-25` exposes only `count`. `plant_phantom_rail.html:23-25` exposes `count` + `rail_name`.

**What's wrong:** help text on the count field reads "How many transactions to plant with the unrecognized template_name" — but operator can't pick the template_name. Forces them to discover the hardcoded default in source.

**Fix:** add a `PrimitiveStringField name="template_name"` to the `phantom_template` registry entry, mirroring `phantom_rail`'s shape. Default like `legacy_wire_template` for symmetry with `legacy_card_swipe`.

### P2.4 — Form field names use implementation vocab where labels translate

**Where:** several plant pages — `name="money"` on overdraft, `name="delta_money"` on drift, `name="amount_money"` on stuck_pending / stuck_unbundled.

**What's wrong:** human label is correct ("Stored balance ($)", "Drift amount ($)", "Amount ($)"). But the HTML `name=` attribute is the kwarg name — and that name leaks into the post-POST banner (P2.2 above) when we add kwarg echo, AND into the form URL on POST failures. `money` / `delta_money` / `amount_money` read as internal kwarg conventions, not user vocabulary. A future tooltip / error message that references the kwarg name will surface this.

**Fix:** rename the plant_function kwargs to operator-aligned names (`stored_balance`, `drift_amount`, `amount_dollars`), OR keep kwarg names + render the label-driven echo (cheaper, ties to P2.2).

### P2.5 — Days-ago help-text inconsistency

**Where:** drift says "Business-day offset from today for the drift cell (0 = today, 1 = yesterday, ...)". Every other Days-ago help is a one-liner: "Business-day offset for the cap-breach cell" / "for the disagreeing-parent legs" / "for the overdrawn cell" / etc.

**What's wrong:** only drift carries the (0=today, 1=yesterday) gloss — that's the most useful operator detail (else they have to remember the convention). Other 14 days_ago help-texts skip it.

**Fix:** add `(0 = today)` parenthetical to the shared days_ago primitive's help_text base, then per-entry append the cell-shape phrase. Or hoist the convention gloss into the page-level help section.

### P2.6 — `count` defaults vary unexplainedly (3 vs 2)

**Where:** `plant_phantom_rail.html` defaults `count=3`; `plant_phantom_template.html` defaults `count=2`.

**What's wrong:** no semantic reason — both are "how many phantom rows." Visually inconsistent without justification.

**Fix:** pick one canonical default (3 reads better — clearer it's not a one-off accident); update both registry entries.

### P2.7 — Default days_ago values differ per kind without operator-visible reason

**Where:** `drift=5`, `ledger_drift=5`, `overdraft=6`, `limit_breach_outbound=4`, `limit_breach_inbound=3`, `stuck_pending=30`, `stuck_unbundled=30`, `chain_parent_disagreement=1`, `xor_group_missed=0`, `xor_group_overlap=1`, `fan_in_missing_parent=4`, `fan_in_extra_parent=3`, `multi_xor_missed=6`, `multi_xor_overlap=5`, `supersession_audit=3`.

**What's wrong:** the per-kind default is sensible IN ISOLATION (each plant_function has reasons) but the operator clicking through 20 entries sees a confusing scatter. Aging defaults to 30 because aging only fires past the threshold; pending-aging at days_ago=5 wouldn't fire. But this rationale isn't surfaced.

**Fix:** add per-entry "Why this default?" gloss in the help_text — at minimum on the L1 Aging pair (where 30 vs 5 looks weird without context).

### P2.8 — `limit_breach_outbound` cap-amount help text leaks demo-specific dollar amounts

**Where:** `plant_limit_breach_outbound.html:25` — "Default $15,000 comfortably exceeds the standard demo cap of $10,000."

**What's wrong:** "the standard demo cap of $10,000" is a sasquatch-fixture-specific number; if an integrator's L2 carries a $50,000 cap on the picked rail, the planted $15,000 won't breach anything and the dashboard will stay clean — silent plant failure. The plant_function should pick a cap-exceeding amount from the L2's actual `LimitSchedule.cap`, not assume $10k.

**Fix:** make `cap_breach_amount` derive its default from `picked_LimitSchedule.cap × 1.5` at render time (the design said primitives expose `default_picker(L2Instance)`); drop the hardcoded comparison from the help text. Same shape for the inbound entry.

### P2.9 — `uncovered_rail` and `uncovered_template` tour destinations point at `/etl/run`, not `/etl/triage`

**Where:** `tour_uncovered_rail.html:28` iframes `/etl/run`; `tour_uncovered_template.html` same. Compare to `tour_phantom_rail.html` which correctly iframes `/etl/triage`.

**What's wrong:** Coverage gaps surface on the Coverage panel inside `/etl/triage` (Lock 0's §0.5 matrix puts them on the same surface as the unmatched-rail / unmatched-template gaps — both are "L2 declaration vs runtime evidence" mismatches). `/etl/run` is the matview-refresh trigger UI; an operator landing there doesn't see Coverage data. The tour iframe is pointed at the wrong sheet.

**Fix:** retarget the `uncovered_rail` + `uncovered_template` registry entries' `tour_destination` to `/etl/triage` (or `/etl/triage#coverage` if that page has anchor support). Cross-check Lock 3's tour-destination mapping table in `bu_0_replan.md:570`.

### P2.10 — Seven chain-coherence entries dump to one tour destination (`l1-sheet-todays-exceptions`)

**Where:** `tour_chain_parent_disagreement.html`, `tour_xor_group_missed.html`, `tour_xor_group_overlap.html`, `tour_fan_in_missing_parent.html`, `tour_fan_in_extra_parent.html`, `tour_multi_xor_missed.html`, `tour_multi_xor_overlap.html` all iframe `/dashboards/l1_dashboard/sheets/l1-sheet-todays-exceptions`.

**What's wrong:** legitimate per Lock 3 (Today's Exceptions IS the unified L1 chain-violation surface). BUT: with 7 kinds landing on the same long sheet, the operator has no way to tell whether their planted XOR-overlap actually surfaced or whether they're looking at a residual fan-in row. Tour iframes without anchor-fragments was already flagged as P2#5 in `bu_cold_read.md` — this is the compound-at-7x version of that.

**Fix:** either (a) add `#filter=xor_group_violation` style URL params to each registry tour_destination so Today's Exceptions auto-filters to the planted kind, OR (b) split Today's Exceptions into per-violation-class anchor sections + use `#<anchor>` deep-links. Either way: don't ship 7 kinds tour-pointing at the same un-anchored long page.

### P2.11 — `Supersession Audit` title is the only Title-Case kind name on the landing

**Where:** `landing.html:30` — `<span class="text-sm font-semibold">Supersession Audit</span>`. Every other section title uses sentence case ("Sub-ledger drift", "Per-direction flow cap", "Unmatched rail_name").

**What's wrong:** SoT (`L1_Invariants.md:449`) has `## Diagnostic surface — Supersession Audit` — the parser is correctly pulling "Supersession Audit" as the title. But the convention drift is visible. Either the SoT should be sentence-case for consistency OR every section title should be Title Case.

**Fix:** decide convention + update the SoT line.

### P2.12 — Family-pretty-label + category-pretty-label is duplicative in breadcrumb

**Where:** `plant_*.html:15` — `<span>L1 invariant · L1 Conservation</span>`, `<span>L2 Triage · L2 Triage gaps</span>`, `<span>L2 Coverage · L2 Coverage gaps</span>`.

**What's wrong:** the category and family encode the same axis ("this is an L1 invariant in the L1 Conservation family"). Two pills both saying "L1" is redundant. BU.4 stage 1 already touched the breadcrumb (the enum-leak fix) so this is in scope for stage 2.

**Fix:** drop the first segment when family starts with the category's pretty label. Render: `L1 Conservation` / `L2 Triage gaps` / `L2 Coverage gaps`. Single-pill, no duplication.

### P2.13 — Family-card density: 7 families × 20 entries × open-by-default = crowded landing

**Where:** `landing.html` — all 7 `<details ... open>` accordions render expanded; total visible rows ≈ 20 entries + 7 family bars = 27 rows on first paint.

**What's wrong:** the BU.0 mockup spec said default-collapsed accordion. Open-by-default works at 3-4 entries; at 20 with 7 families it's a wall of mono-link text. Operator opens the page → sees overwhelming list → loses scent of "where do I start?"

**Fix:** default-collapsed (`<details>` without `open`); add a per-family count badge in the summary (`L1 Chain coherence (7)`); keep the first family open OR add a "Expand all" link.

### P2.14 — `missing_metadata_key` body copy renders raw markdown backticks

**Where:** `plant_missing_metadata_key.html:19` — `Each row is a \`(template_name, metadata_key)\` pair where the template declares the key as required (via its \`transfer_key\` fields)...`. Same for `phantom_rail` body copy etc.

**What's wrong:** the description is markdown-from-SoT but rendered as raw text. Backticks display literally instead of becoming `<code>`. SoT-doc cross-references like `Rail.name` look like prose junk rather than code identifiers.

**Fix:** run the body paragraph through a small markdown→HTML pass (backticks → `<code>`, asterisks → `<em>`, double-asterisks → `<strong>`). Keep it scoped to inline formatting; don't open the door to full markdown block parsing on the trainer surface.

---

## P3 — backlog

### P3.1 — Banner has no close button, no auto-dismiss

`plant_*_after_post.html` post-POST banner stays in place forever. Pre-flagged as P2#8 in `bu_cold_read.md`; lives in BU.1.7 follow-up scope. Note for completeness only.

### P3.2 — `supersession_audit` body description is also empty

Same root as P1.3 but for the `Diagnostic surface` section (`L1_Invariants.md:449`) which isn't part of "the seven L1 SHOULD-constraints" header set. Parser may not cover the diagnostic-surface region. Low-priority because the form fields ARE labeled clearly.

### P3.3 — `count` field max=100 is unjustified

`plant_phantom_rail.html:24` — `max="100"`. Why 100? The triage volume badge maxes at... unknown. If 100 is the badge's overflow threshold ("100+"), say so in help text; if it's arbitrary, drop the cap or raise it to 10000.

### P3.4 — Reset banner copy reads identical on landing-pre-plant and landing-after-cycle

`landing_reset_done.html:24` — `Demo DB wiped and reseeded clean. Pick a kind below to plant exactly one scenario; the dashboard tour will show ONLY your plant.` Reads fine when arrived via reset, awkward if the operator just refreshed the page and the banner persisted. (Probably doesn't persist; HTML cold-read can't verify.)

### P3.5 — Tour iframe page title doesn't echo the planted kind

`tour_drift.html:5` — `<title>Studio · Training · Sub-ledger drift · Tour</title>`. Browser-tab readers see "Tour" — would be more scannable as `Tour: Sub-ledger drift`. Cosmetic.

### P3.6 — `data-test-training-family` attribute uses pretty labels with spaces

`landing.html:25` — `data-test-training-family="L2 Triage gaps"`. Spaces in test selectors work but require quoting. Could be `data-test-training-family="l2_triage_gaps"` for cheaper selector authoring. Low impact; tests presumably already work.

### P3.7 — No "(0 = today)" gloss on `days_ago` outside drift

See P2.5; demoting to P3 if P2.5 is taken as the canonical fix.

### P3.8 — `min="0" max="90"` on days_ago everywhere

15 entries copy `min="0" max="90"`. The plant_function may not validate beyond 90; if the integrator wants to seed 180 days back the form blocks them. Minor; integrators don't typically use trainer for >90-day seeds. Capture as "is 90 the right max?" decision, not a bug.

### P3.9 — Plant page lacks any indication of which L2 instance is being planted into

Browser tab title is `Studio · Training · <kind>` — no L2 instance name. Operator running two instances (sasquatch_pr + spec_example) on the same Studio server has no way to confirm which L2 their plant lands in. The Studio header (not shown in plant pages because plant pages don't extend the global chrome) would normally carry this; verify whether plant pages should inherit the Studio nav.

---

## What HTML cold-read CAN'T surface (operator should walk these visually)

1. **Reset button progress indicator.** `BU.1.7` was supposed to add an in-flight progress UI on Reset. The static HTML shows the button before/after but not the during-state. Verify the disabled+spinner shape works.

2. **Banner auto-dismiss timing.** P3.1 above — visual-only.

3. **Tailwind class resolution.** A `text-accent` / `bg-success/10` class that doesn't actually resolve to a color shows as transparent in the rendered page but looks fine in source. Take a screenshot.

4. **Accordion `<details>` JS behavior.** HTML cold-read assumes default browser `<details>` semantics; if any JS hijacks it, can't verify here.

5. **iframe load behavior.** Tour iframes — does the embedded `/dashboards/l1_dashboard/sheets/<sheet>` actually render inside the iframe, or does X-Frame-Options block it? Doesn't show in source.

6. **Mobile / narrow-viewport layout.** Plant page `max-w-3xl mx-auto` + a 100% form will look fine on desktop; on iPad-portrait the side-by-side fields might wrap awkwardly. Not visible in source.

7. **Post-plant matview-refresh trigger.** When does refresh actually happen? Source shows "Plant this scenario" button but the underlying side-effect chain (plant → refresh → tour) is invisible to HTML inspection. Walk the loop manually for at least one entry per family.

8. **Form-field default-picker behavior across L2 instances.** All 62 HTML files were rendered against one L2 (presumably sasquatch_pr); switching to spec_example might surface picker-discovery bugs (`_pick_template(l2)` returning None for a too-thin L2) — cold-read can't see this.

9. **Mid-action navigate-away protection.** Reset is destructive (Lock 6); if operator clicks Reset then closes the tab mid-flight, what's the state? Not visible.

10. **The Locks 9 anti-drift tests' actual pass/fail.** The brief assured they're covering the contract; HTML cold-read can't verify they ARE running, just trust the brief.

---

## Confirmed-holding from prior cold-read

- **BU.1.10 form-reset fix:** confirmed. `plant_drift_after_post.html` preserves the submitted (bogus `demo_value_42`) values. No reset to defaults.
- **BU.4 stage 1 breadcrumb fix:** confirmed. Breadcrumbs read `L1 invariant · L1 Conservation` (pretty labels, not enum values). P2.12 above is the next-iteration "drop the duplication" follow-up, not a regression.
- **BU.4 stage 1 reset-copy rewrite:** confirmed. Button reads `↻ Reset to clean baseline`; no `BTa.8` phase leak in the reset surface. Helper text reads "Wipes the demo DB and reseeds it clean."
- **BU.2a typed-section migration:** confirmed at the framework level. The L2 Triage entries (phantom_rail, phantom_template, missing_metadata_key, uncovered_rail, uncovered_template) carry rich body + "what to do" copy sourced from the typed L2TriageGapSection. P1.3 + P1.4 are LIVE bugs in the migration's L1 + shared-section sub-kind handling, NOT regressions of the typed-section migration itself.
- **BU.1.9 LimitSchedule structural gap:** out of scope per the brief.

---

## Recommendation

**Don't ship externally until P1.1-P1.4 land.** All four root in typed-source / typed-section code (`common/handbook/invariants.py` + the registry's `kind_qualifier` field). One coordinated fix branch covers all four — estimated <2 hours work. The shared `_render_plant_page` shell doesn't need touching.

After P1: surface is shippable internally. P2 cluster groups naturally into BU.4 stage 2 (12 of 14 are renderer / registry-row tweaks); P2.9 + P2.10 are tour-destination registry edits that should land with P1 since they're one-line registry-row changes.

**The 20-entry expansion holds.** No new architectural defects surfaced; every P1 here is a typed-source bug compound at scale, not a registry-pattern bug. The Lock 7 + Lock 8 design IS doing its job — when the typed source improves, all 20 entries improve. The bugs the cold-read found are exactly the bugs the design predicted would be findable in one place.
