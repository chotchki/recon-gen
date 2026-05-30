# BX Cold-Read v1b — L2 Editor, Implementation Consultant Persona

## 0. Cold-read context

I'm an implementation consultant a midsize credit union just hired to stand up Recon-Gen against their actual chart of accounts and rail mix. I have a real-world banker's mental model (DDAs, GLs, ACH, wires, settlement cycles), I read YAML in self-defense but I won't write it from scratch, and my deliverable is a defensible "this L2 represents how we actually move money" sign-off for the CFO and the model-risk team. I'm coming to this Studio surface for the first time, with no prior briefing past "this is the editor — go declare your institution." The screenshots in this round are an extended walk: a v1 baseline of 19 captures, plus 10 follow-ups I asked for after the first pass exposed gaps (orphan-role behavior, delete confirmation, composite-key URLs, markdown preview, populated read/edit cards). What follows is what landed and what didn't, ordered by surface.

---

## 1. `/` home + diagram (`bx_cold_read_screenshots_v1b/01_home.png`, `02_diagram_full.png`)

### First impressions

The home page is a wall. Six dim entity-kind chips along the top (`account`, `account_template`, `rail`, `transfer_template`, `chain`, `limit_schedule`) plus the singletons (Instance / Theme / Persona) buried into the top nav alongside the runtime dashboard tabs (L1 Dashboard / L2 Flow Tracing / Investigation / Executives). There is no "start here" anywhere on the page. As a consultant standing up an institution, I have no idea what the *first* thing I'm supposed to declare is. The diagram dominates the middle of the screen and looks beautiful, but it's a consumption artifact, not a guidance one — I'm looking *at* an institution-shape, not being walked through *how to build* one.

The fundamental confusion: **this is an editor whose home screen reads like a viewer.** A first-run state would have helped enormously — empty diagram + a numbered "1. Declare your Institution → 2. Pick a Theme → 3. Add your Persona → 4. Declare account roles → 5. Declare account templates → 6. Declare rails → 7. Compose templates from rails → 8. Compose chains → 9. Set limit schedules" flow, with checkmarks as each entity-kind acquires at least one row. The current state assumes I already know the dependency graph between entity kinds, which is exactly the knowledge I'm here to acquire.

### Dependency-order discoverability

Zero signal. The chips are alphabetical (or seem to be), not topological. A banker reads "account" first and thinks "great, let me declare my chart of accounts." But account_template is the right primitive — `account` is the materialization of a role declared on a template, and that requires understanding the rail/template/chain triad before the account list even makes sense. The home offers no warning that picking a chip in the wrong order will surface dropdowns full of orphan-role names you haven't declared yet (see § 8, screenshot 27).

Singletons are still not findable from `/`. The Instance + Theme + Persona links sit in the top nav, indistinguishable in styling from the four dashboard-tab links. A consultant won't notice them at all on first pass. This is the most important fix on the home surface — those three singletons gate everything (Instance carries `role_business_day_offsets` which propagates into every cadence calculation; Theme drives the QS color palette; Persona drives every handbook string) and they should be the first three tiles on the home grid, not hidden in the nav strip.

### Diagram

`02_diagram_full.png` is genuinely good as a *finished-institution snapshot*. 14 nodes, 3 edges in this stock view, color coding by role-class. But:

- Nodes aren't clickable-to-edit. Cold-read instinct: I click a node, I'm dropped on its edit page. Nothing happens.
- "Coverage" and "Trainer" chips have no tooltip. I have no idea what either means until I find the docs (and a consultant won't go find the docs — they'll just shrug and ignore the chips).
- No "(click chips above to add a node)" hint. The relationship between the chip strip and the diagram below isn't obvious. I'd expect dragging a chip into the canvas; instead nothing connects them visually.
- The diagram lives at home but is decoupled from every edit page. When I'm staring at a chain edit form (screenshot 24), I have no inline "here's where this chain sits in the network" mini-diagram. That's the single highest-leverage improvement available — the diagram should be the right rail on every edit page, with the entity-under-edit highlighted.

### Top nav

BUILD vs VIEW separation matters. Today the top nav reads `Studio | Diagram | L1 Dashboard | L2 Flow Tracing | Investigation | Executives | Instance | Theme | Persona | Deploy`. To a consultant that's ten peer items with no hierarchy. I'd group these into two clusters:

- **Build**: Studio, Diagram, Instance, Theme, Persona, Deploy
- **View**: L1 Dashboard, L2 Flow Tracing, Investigation, Executives

With a visible visual break (a separator pipe, a different background tint, or just spacing). Today a consultant clicks "L1 Dashboard" trying to find their declared institution's preview and ends up in a runtime tab that confuses the surface boundary.

---

## 2. List pages (`03`-`08`)

### `03_list_account.png` — account

Solid. Tabular, account_id + role + control_account + descriptor visible at a glance. Counts at the top (somewhere) would help — "13 accounts declared, 3 unmaterialized templates" gives me a completeness number I can quote to the CFO. No "incomplete" or "draft" signal — every row looks done, but I don't yet know how to *check* that, because I don't see required-field-missing badges or validation warnings on this surface.

### `04_list_account_template.png` — account_template

The clearest list page of the set. Three rows, each row carries enough detail that I can read the page top-to-bottom and understand the templating model in 30 seconds. This is the format every list page should aspire to: roles + a one-line description + count of materialized children + last-modified.

### `05_list_rail.png` — rail

21 cards, dense, hard to scan. The cards each carry maybe 8 fields of info but at this density I can't read any of it without zooming. Two fixes:

- **Collapse to a table by default** with name + source_role + dest_role + cadence + 1-line description. Expand-on-click for the full card.
- **Group by source_role or cadence.** When I'm reviewing "all the rails that touch CustomerDDA" I want to see them together, not interleaved alphabetically with ConcentrationToFRBSweep and EnhancedFraudReview.

The composite-key entities (chain, limit_schedule) hide *worse* in lists because the row label is the composite key — which is fine when there are 8 of them, painful when there are 80.

### `06_list_transfer_template.png` — transfer_template

Readable. Good middle ground — name + completion expression + leg refs. I'd add "uses these rails" as a small chip strip per row so I can spot a template that's silently referencing a rail I haven't declared yet.

### `07_list_chain.png` — chain

Cards exposing the composite key `Parent::Child` as the row label. URL-wise this becomes `/l2_shape/chain/ACHOriginationDailySweep::ConcentrationToFRBSweep/edit` (confirmed in screenshot 24). The composite key in the URL is a problem — see § 7.

### `08_list_limit_schedule.png` — limit_schedule

Same composite-key issue but worse: `ParentRole::Rail::Direction`. The page itself is fine as a list; it's the rename-fragility and URL behavior I worry about.

### Cross-list completeness signal

Nothing tells me "your institution is 60% complete" or "5 rails are referenced from templates but not yet declared" or "your persona has no merchant DDAs but you've declared 3 merchant_dda accounts." The lists are passive — they show what exists, not what's missing or inconsistent. For a consultant this is the most-asked question: *how do I know I'm done?* Today the only answer is "click Deploy and see what blows up."

---

## 3. Singletons

### `09_singleton_instance.png` — Instance

Raw YAML textarea. I cannot stress how much this kills the consultant experience. The Instance singleton carries `role_business_day_offsets`, `as_of_date`, `bizday_calendar` (or whatever the equivalents are) — exactly the kind of fields where "missed a comma in the YAML" turns into "every cadence calculation is silently wrong." A consultant standing up an institution will not survive this. Even a flat key/value grid (one row per field, type-aware widget per row, validation per cell) is dramatically better than a textarea. I understand this is BF-pending — flagging it as the highest-priority singleton fix, because Instance gates calendar correctness and that's a sign-off blocker.

### `10_singleton_theme.png` — Theme

The right shape (one form, named fields). But:

- "Series 1" through "Series 10" with hex codes and no preview. As a consultant I have to either trust the defaults or open a color picker, paste hex, save, deploy, switch to the L1 Dashboard tab, see if the chart looks right, come back, tweak, redeploy. Round trip is 5+ minutes. Live preview swatches solve this in five lines of CSS.
- 60+ fields, one Save button. If I tweak one color and save and the institution name is blank because I haven't filled it in, will the save reject all 60 fields? I don't know. No section-level save buttons either.
- "Series" itself is jargon. I'd expect "Chart palette" or "Category colors" — "Series" is the QuickSight internal term and it leaks straight through.

### `11_singleton_persona.png` and `29_persona_filled.png` — Persona

Right shape. "Stakeholders" is a vocabulary mismatch — as a banker I think "the people who care about this dashboard" (the CFO, the AML officer, the model-risk lead). What this field actually wants is **upstream-counterparty display strings** (Fed, ACH operator, card network) — those are *correspondents* or *counterparties*, not stakeholders. The help text in `29_persona_filled.png` does say "Upstream-counterparty display strings" which is correct, but the label says "Stakeholders" — so the field is correctly described and mislabeled at the same time.

"Flavor" is even worse. The help text says "Free-form persona strings (sample customer name, region descriptor, legacy-entity callout)" — fine, but a consultant has no idea **where this surfaces.** Does it appear in dashboards? In the audit PDF? In tooltip strings? In matview row labels? Without a "rendered as: [example]" pointer per field, I'm guessing what to type.

"GL accounts" is great. `code | name | note` triple with explicit examples (`gl-1010 | Cash | optional hint`) is exactly the right shape for a chart-of-accounts entry. This is the model the rest of the singleton should follow.

One UX bug in `29_persona_filled.png` worth flagging: the consultant who pre-captured this round noted that **the Flavor section's input fields are hidden until "+add row" is clicked.** Looking at the screenshot I see the "(add row)" placeholder, which is fine for an empty state — but it should be visually obvious that the section is interactive and not just an empty heading. Stakeholders, Merchants, and Flavor all use the identical "(add row)" empty-state pattern and it took me a moment to realize they were live controls and not section dividers. A `+ Add stakeholder` button styled as a button (not a faint placeholder) would solve this.

---

## 4. Create flows

### Rail (`14_new_rail_picker.png`, `15_new_rail_two_leg.png`, `16_new_rail_single_leg.png`, `20_new_rail_filled_minimal.png`, `21_new_rail_submit_minimal_result.png`)

The picker (14) is the best form-page in the whole set. Subtype-first is the right primitive — "Two-leg" vs "Single-leg" is a model-shape choice, not a field, and forcing it up front means the two-leg form can be more constrained than the single-leg form (which is exactly what happens).

But the two-leg form itself (15) is 20+ fields, and at least half are vocabulary-opaque to a banker:

- **Cadence** — "daily-eod" reads as ops-speak; I'd want "End of business day" with a tooltip showing what it expands to in the cadence calculator.
- **Posted requirements** — "metadata keys that the rail's transactions carry (e.g. ach_trace_number, wire_imad)" is good help text, but the field-name "Posted requirements" buries the lead. Call it **"Required metadata on posted legs"** and link inline to a one-line "what is metadata?" explainer.
- **Metadata value examples** — same issue, also unclear if this is illustrative-only or used at validation time. The help text says "for the data filter (faceted)" which doesn't unblock me. **Where is this surfaced and why does it matter?**
- **XOR groups** — appears in the chain form (18, 24) and is brutal for a banker. The Z.A grammar prose in the chain form helps, but the term "XOR" inside a banking surface is going to be the most-asked question I get from the CFO. "Choose-one alternation" or "either/or" would be friendlier.
- **Source/Destination origin (override)** — what's being overridden? The non-override Origin field above it? When would I do that vs not? The two-line help text isn't enough; this needs a worked example.
- **Max pending age / Max unbundled age** — ISO-8601 duration format ("P1D") is hostile. A duration picker with "1 day" + dropdown {day, hour, business-day} translates to "P1D" under the hood.
- **Bundles activity** — checkbox list of OTHER rail names. As a consultant declaring a NEW rail (TestACHReturn), this list of 20+ existing rails to bundle is overwhelming and the field is utterly opaque without context. The help text "Identifies the foundation rails this rail's transactions carry (e.g. ach_trace_number, wire_imad)" actually describes *Metadata keys*, not bundles — looks like a copy-paste error.

**Filled + submit (20 → 21).** I filled name=TestACHReturn + source_role=CustomerDDA + dest_role=ExternalCounterparty + left everything else empty. Submit landed at `/l2_shape/rail/` (the list) — but the screenshot also shows a **red error banner** that I missed at first glance: *"Reconciler required: non-aggregating single-leg rails pick an existing TransferTemplate (chose the TT's expected_legs) or aggregating fee types need to be in a bundle, or create one of fine. Per SPEC Z.D, an unreconciled single-leg rail's BIN would anchor forever."* So this isn't a successful save with a redirect-to-list — this is a validation rejection that re-rendered the form with the error inline, and the URL happens to be the form-submit endpoint. That's misleading: I had to look twice to spot the banner because the page chrome looks identical to the list page.

Two takeaways:

1. **Form re-renders on validation failure need a distinct visual** — not "looks like the form again" but "looks like the form again WITH errors" — sticky red banner at the top, scroll-to-first-error, inline per-field marks (the banner is great, but the per-field marks are missing).
2. The error message itself is sophisticated and assumes I know what Z.D, "BIN", "anchor", and "reconciler" mean. A consultant won't. **Plain-language error first, spec-section pointer in parens** — "This rail looks like a fee accrual but doesn't bundle into a settlement rail. Either bundle it into one (Bundles activity field) or wire it as a TransferTemplate leg. (See SPEC Z.D for the underlying invariant.)"

**Populated read card vs edit form (22, 23).** Side-by-side, the populated `ACHOriginationDailySweep` read card (22) is *almost-good*. Dense list of label → value pairs, "—" for unset fields, the Description block at the bottom in prose. But the layout is just unstyled text — Name/value pairs on separate lines with indent, no card, no grouping. Compare against the edit form (23) which DOES have card-like structure and clear sectioning — the read view is less polished than its own edit view, which is a smell. Read view should be the *prettier* of the two (it's the page the consultant lands on to review their work), and it should group fields the same way the edit form does (Identity, Topology, Cadence, Bundling, Reconciliation, Description). Right now it's a flat dump.

Also missing from the read card: **a "Used by" section.** I'm on `ACHOriginationDailySweep` — what transfer_templates reference it? What chains include it? What limit_schedules cap it? Without that, the consultant can't reason about blast-radius before clicking Delete (which is the next item).

### Transfer template (`17_new_transfer_template.png`, `26_new_transfer_template_reference_expanded.png`, `28_markdown_preview_TT.png`)

Cleaner than rail. ~10 fields, clear sections (Name, Completion expression, Leg refs, Transfer key, Typical amount range, Description). The `<details>` Reference panel (26) is a good idea — when expanded it explains the completion-expression DSL with examples and the leg-refs cardinality rules. **Bury that panel less.** Default-open on first visit; collapse-by-default only after the user has dismissed it. A consultant on first read needs the help, not a folded triangle they might never click.

The `Completion expression` field deserves autocomplete on `business_day_offset(...)`, `leg_count`, the standard arithmetic operators, and the role names declared in this L2. Right now it's a plain text input. The DSL is the riskiest thing I'll type on this page; getting it wrong won't show up until deploy, and the error message at deploy time will be cryptic.

`28_markdown_preview_TT.png` shows the markdown preview tab working on the description field for `InternalTransferCycle` — I typed `# Heading\n\n**bold** *italic* + list` and switched to Preview. **The preview works.** Heading renders, bold/italic render, the list-prefix text shows up. This is a real win — gives the consultant confidence that institution-prose written here will surface intact in the handbook + audit PDF. The Preview tab should be visible on *every* description field in the editor (it's there in chain too based on 24, good).

### Chain (`18_new_chain.png`, `24_edit_chain_composite_key.png`)

Good compound form. Parent dropdown + per-row child checkbox + fan-in checkbox + epc input is the right shape — it makes the Z.A grammar (one child = required, two+ = XOR) declaratively clickable rather than buried in YAML. The help text under the children list is technically excellent and operationally hostile: "Z.A grammar: one selected = required (every parent firing MUST invoke it). Two+ selected = XOR alternation (exactly one fires per parent firing). For each selected child, the fan-in checkbox + expected-parent-count input let you opt that child into N:1 fan-in (validator C8a requires fan_in children to be TransferTemplates). Mixed-cardinality is supported: one child fan_in while siblings stay 1:1 XOR (AB.6 shape; sasquatch's MerchantSettlementCycle chain is the canonical demo). Empty selection is rejected."

That's three rule citations in two sentences, no examples in-page. A banker won't survive it. **Pull the example into the page** — "Example: a daily ACH origination sweep parents (one child = required: ConcentrationToFRBSweep). A merchant settlement cycle parents three children where one is a fan-in TransferTemplate (the settlement bundle) and the other two are sibling 1:1 alternatives (XOR)." Drop into expandable sections, link out to the SPEC for the validators.

No inline shape-preview. When I check "ConcentrationToFRBSweep" with fan-in unchecked, I can't see the resulting topology mini-diagram. That preview would *immediately* tell me whether I built the chain I meant to build, instead of waiting for Save → list → re-open → mental-compose-the-diagram.

### Limit schedule (`19_new_limit_schedule.png`, `25_edit_limit_schedule_composite_key.png`)

Five fields, every help-text line is gold. **Direction's** help text ("Outbound = money leaving the parent's children (classic send cap). Inbound = money arriving (AML / structuring threshold).") is the best help text in the whole editor. It tells me what the field is for in banker vocabulary AND what use case each value supports. Every help text should aspire to this — the format is *what + when-to-use-which-value*, not just "what."

`Cap` is fine ("Daily $ cap. L1 Limit Breach flags any day exceeding this."). One nit: `12000.0` as the persisted display (25) — should render `$12,000.00` for a currency field. The L2 schema knows this is currency; the edit input should respect that with currency formatting on blur.

### Account (`12_new_account.png`)

Sparse. account_id, role, control_account_id, descriptor fields. Need:

- **Role dropdown should warn when picking an orphan** (see § 8 / screenshot 27). Even better: role should be a typeahead that ONLY surfaces declared role names, with a "+ add role" affordance that takes me to account_template.
- **control_account_id** should be a typeahead over already-declared accounts, not a free-text field. The self-referential FK is genuinely useful but error-prone if I have to remember the exact account_id format.
- **account_id format guidance.** Should it be kebab-case? snake_case? Should it embed the role? The demo shows `test-flobber-1`-style (kebab) and the existing demo rows in `03` use other conventions. Pick one, document it in help text, validate on save.
- **No "this role expects N accounts; you've declared M" callout.** If account_template declares `CustomerDDA{count: 10}` and only 7 accounts of that role exist, the create form should surface that gap as a positive nudge: "CustomerDDA expects 10, you have 7 declared. [+ Quick add 3 more]".

### Account template (`13_new_account_template.png`)

Cleanest of the entity-create forms. roles list + per-role count + role-name field. The form actually models the template intent (declare a role and how many should exist). This pairs nicely with the account list page — if I declare `account_template{role: CustomerDDA, count: 10}` and then only see 7 CustomerDDA rows in the account list, the gap should be visible. Today it isn't.

---

## 5. Edit flows (post-save)

### Read card vs edit form (22, 23)

The two screenshots are the same rail viewed two ways. The read card (`22`) is an unstyled vertical dump: `Name / Source role / Destination role / Origin / Source origin (override) / ...` with values indented underneath each label and em-dashes for empty fields. The edit form (`23`) wraps the same data in a card with proper spacing, sectioning, and visual hierarchy.

Key gaps in the read card:

- **No sectioning.** The 16-ish fields are presented as a flat list. The edit form groups them logically (Identity / Topology / Cadence / Reconciliation / Bundling / Sample data / Description) and the read card should mirror that grouping.
- **No "Used by" back-references.** `ACHOriginationDailySweep` is referenced by the `InternalTransferCycle` template (visible because its Bundles activity field includes it) and by chains and limit_schedules. None of those back-references appear on the read card. Before I click Delete I want to see them.
- **No diagram inset.** Even a 200px mini-version of the diagram with this rail highlighted would be enough to orient me — "ah, that's the sweep that fans into the FRB concentration."
- **No "rendered as" links.** The L1 invariants this rail participates in (chain_completion, fee_aggregation, etc.) should each be one-click navigation to the L1 Dashboard with the relevant filter applied. Today the editor surface is hermetically sealed from the runtime.
- **`Edit` / `Delete` chrome is minimal but unstyled.** The `EditDelete` text mashing in `22` looks like a rendering bug — the two actions need at least a separator. As styled this looks like a single word "EditDelete" or two no-style links that ran together.

What's good: the read card loads fast, every field is unambiguously labeled, and the em-dash treatment for empty fields is correct (better than blank or "null"). The Description prose at the bottom renders in clean paragraph form with inline-code styling for entity references. Solid foundation; needs the polish layer.

### Persona filled (29)

The persona form as captured here is the empty form (the title bar reads "Persona", not "Persona — filled"); the stakeholders/merchants/flavor sections all show the "(add row)" empty-state. Interpretation: when the previous-round consultant filled 3 stakeholder strings, the form probably looked identical except for those three lines being populated. The point that came across is that **the empty-state "(add row)" pattern is too subtle.** A muted placeholder line that you have to click to discover is interactive is hostile to first-read. Make it a button: `+ Add stakeholder`.

The Institution section at the top is good — Name / Acronym / Region / Legacy entity, all with clear help text and explicit "surfaces as" pointers ("dashboard titles + audit footer", "(e.g. 'Pacific Northwest')", etc.). That pattern works; extend it down through the other sections.

### Markdown preview (28)

Already praised in § 4. The Preview tab is on the description field for `InternalTransferCycle` and renders correctly. Real win. **Make sure every entity kind's description field has it** — I see it on rail edit (23) and chain edit (24) but haven't verified the singletons.

---

## 6. Composite-key navigation (24, 25)

### Chain edit (24): URL = `/l2_shape/chain/ACHOriginationDailySweep::ConcentrationToFRBSweep/edit`

The composite key in the URL is exactly the smell I called out in round 1. Two concrete problems:

1. **Rename fragility.** If a consultant renames `ACHOriginationDailySweep` to `ACHDailySweep` to match the institution's internal naming, every URL pointing to a chain that referenced the old name breaks. Bookmarks dead, audit links dead, links shared with the CFO dead.
2. **URL-encoding surprises.** `::` survives URL encoding fine, but the SECOND a role or rail name contains a non-ASCII character, a space, or a `/`, the URL-encoded form becomes unreadable in the address bar and copy/pasted links lose round-trip safety. Defensive: a consultant DOES try to name something `Customer DDA - Tier 1` and the URL escapes it; the form might still work but the bookmark won't survive a paste into Slack.

**Recommendation:** opaque short IDs in the URL (`/l2_shape/chain/c_42x9k/edit`), with the composite key shown in the breadcrumb + page title. The composite key stays as the human-readable label *everywhere it should be human-readable*, but URLs are URL-shaped, not banker-shaped. Same fix applies to `25_edit_limit_schedule_composite_key.png` which shows `/l2_shape/limit_schedule/DDAControl::CustomerOutboundACH::Outbound/edit` — same problem, three keys deep.

### Chain edit form itself (24)

The form looks fine. Parent dropdown is populated, Children checklist with fan-in + epc-inputs renders cleanly. Description preview is wired. The grammar prose is dense (covered in § 4). The breadcrumb / page title shows the composite key, which is correct and consultant-friendly.

What I'd add: **a "where does this chain show up" link** — "This chain materializes the L1 invariant `chain_completion` for parent `ACHOriginationDailySweep`. View on L1 Dashboard →". Right now the editor is hermetically sealed from the runtime; a single deep-link per entity-edit page would tie them together.

### Limit schedule edit (25)

Tight, well-structured. Each field has its help text and value is populated. `$12,000.00` should render currency-formatted (it currently shows `12000.0`). Otherwise this is one of the better edit pages.

---

## 7. Cross-cutting

### Description markdown

Confirmed working across rail/chain/limit_schedule (Edit/Preview tab). Win. Extend to singletons. Document the supported subset (full GFM? CommonMark? subset?) in a help-icon tooltip on the Edit tab so consultants don't try to embed images or tables and silently lose them.

### Form state predictability

Across the 11 form pages in the screenshot set (account, account_template, rail-picker, rail-two-leg, rail-single-leg, transfer_template, chain, limit_schedule, Instance, Theme, Persona), the form-state model is inconsistent in three ways:

1. **What does Save do?** On rail-create with valid input → redirect to list (assumed; not screenshotted). On rail-create with invalid input → re-render form with banner (`21`). On account-create with orphan-role-but-otherwise-valid → redirect to `/` (`27`). On rail-edit save → unknown but the edit form has a Save button (`23`) so presumably some redirect happens. **There are at least three different post-save behaviors visible.** Pick one and stick: success → entity read card, failure → re-rendered form with banner + per-field marks.

2. **Are unsaved changes warned?** Click "← back to Studio" from `23` with edits pending. Does the form warn me? I don't know — the screenshot doesn't show that. `beforeunload` handler would be the standard browser hook. Without it, a consultant who clicks the wrong nav link loses 10 minutes of form fill.

3. **Is there a draft state?** I declared `test-flobber-1` with an orphan role in `27`. Did it save? Is it in draft? Is it in "saved but invalid"? Is the YAML now mutated and the diagram doesn't reflect it because the diagram is cached? The lifecycle of an entity from form-input to deployed-and-rendered isn't visible. A "Draft / Pending validation / Saved / Deployed" status indicator per entity would help.

### Validation invisibility (27)

`27_create_account_orphan_role_result.png` — I created `test-flobber-1` with role `FlobberCustody`, a role NEVER declared in any account_template. Submit landed at `/` (the home/diagram page) — and **the new account is not visible on the diagram** in the screenshot. Did it save? Did it reject silently? Did it save with a warning I missed? The form didn't say.

This is a five-alarm fire. The whole point of the L2 declarative model is invariant integrity — if I can introduce an orphan role through the account form and the editor neither blocks me nor warns me, the model's promise is broken from the consultant's perspective. **Required:**

- Role field on account create/edit is a typeahead over declared roles ONLY, with explicit "no matches — declare this role on an account_template first?" empty state.
- If a role *does* get in through some other path, the account list shows the row with a red "orphan role" badge, and the diagram surfaces it as a disconnected node with a warning tint.
- Save-time validation rejects orphan-role accounts with a banner like the one in `21_new_rail_submit_minimal_result.png` (which I do credit for at least *trying* to enforce, even if its text is jargon-heavy).

### Save behavior

`21` showed the form re-render-with-banner pattern on validation failure. `27` showed redirect-to-`/` on apparent success (or apparent success — actually unclear). I want consistency: success ALWAYS redirects to the read card for the newly-saved entity, never to `/`. Landing on `/` after a save means I lose context — I don't know which entity I just saved, and I have to navigate back to verify it took.

### Delete confirm (30)

I clicked Delete on `CustomerInboundACH` (which is referenced by templates and chains). The result page is **the read card** for `CustomerInboundACH` — same view as `22_read_card_rail_ACHOriginationDailySweep.png` for the other rail. No confirm dialog appeared. No "are you sure?" interstitial. No "this rail is referenced by 3 templates and 2 chains — deleting will break them" warning. The screenshot looks like the page that comes BEFORE the delete fires, but the consultant who captured it noted they clicked Delete.

Two possibilities:

1. The Delete link is a no-op / not wired (least likely — it's there in the chrome).
2. Delete fires immediately on click with no confirmation, and the deleted-entity's read card is what the redirect lands on — but the entity is gone in the YAML/store and only the cached page render is showing.

Either way: **Delete needs a confirm step, and a delete with downstream references needs a blocking error.** A consultant CANNOT accidentally orphan three transfer_templates by misclicking. The Z.A grammar enforces it eventually but the friction-point is wrong — fail at delete time, in-form, not at deploy time three steps later.

Specific ask: confirm modal with the text "CustomerInboundACH is referenced by: InternalTransferCycle (transfer_template), [2 chains]. Deleting will cascade-delete OR require re-pointing references. [Cancel] [Delete and cascade] [Delete and re-point...]" — gold-plated, but anything is better than the current "click and pray" state.

### Orphan-role acceptance (27)

Already covered. The single most important integrity gap in the editor.

### The Reference panel pattern

Appears as a collapsed `<details>` element at the top of every create/edit form (visible in `15`, `17`, `18`, `19`, `23`, `24`, `25`, `26`). When collapsed (default) it shows a `▶ ⓘ Reference` strip. When expanded (26) it surfaces an explanatory blurb keyed to the entity kind — e.g., for transfer_template: "A TransferTemplate is a multi-leg event — several Real Firings that the L1 layer expects to balance to expected_net by completion. Settlement cycles, return reconciliations, anything that's not just one leg firing on its own. Required: name, expected_net (often 0 for fully-balanced cycles, fees may sum to a non-zero target), completion (the deadline expression like business_day_offset(leg_ref.t) is edited after creation."

This is great content buried behind a click most consultants won't make. Three fixes:

- **Default-open on first visit per entity kind.** Persist a dismissed flag in localStorage; re-collapse only after the consultant explicitly clicks the triangle.
- **Inline a 2-3 sentence summary above the form even when collapsed.** The triangle should reveal *more* depth, not the *entire* explanation.
- **Link out from each Reference panel to the corresponding SPEC section.** Today the prose is the only explainer; a "Read more in SPEC Z.D" link gives the consultant a path to depth when they want it.

### "back to Studio" + "list all X" header links

Every edit/create form has a header strip with breadcrumb-style nav (visible in `23`, `24`, `25`, `26`). The pattern is consistent which is a real win. One nit: "back to Studio" reads as "back to the Studio app" not "back to the entity-kind list" — a banker may not know that "Studio" IS the entity-kind list home. Re-label to "← Studio home" + the existing "→ list all X" stays as-is. Two visible breadcrumb steps would make this clearer (`Studio › Rails › Edit ACHOriginationDailySweep`) instead of two flat back-links.

### Field-level vocabulary

A vocabulary table summarizing the non-landing terms in the editor, with the consultant-friendly translation:

| Editor term | What it means | Banker translation |
| --- | --- | --- |
| Stakeholders (Persona) | Upstream counterparty display strings | **Correspondents** / **Counterparties** |
| Flavor (Persona) | Free-form persona strings surfaced in handbook prose | **Sample / display strings** |
| Series 1..10 (Theme) | QS chart category color slots | **Chart category palette** |
| Cadence (Rail) | When the rail fires | **Firing schedule** |
| Posted requirements (Rail) | Metadata keys required on posted legs | **Required metadata fields** |
| Metadata value examples (Rail) | Faceted filter sample values | **Filter sample values** |
| Bundles activity (Rail) | Which other rails this rail bundles into a settlement | **Settles into** |
| Source/Destination origin (override) (Rail) | Overrides the default ledger-side origin per leg | **Per-leg origin override** |
| XOR groups / XOR alternation (Chain) | "Either/or" sibling-child semantics | **Either/or alternatives** |
| epc (Chain children) | Expected parent count for fan-in | **Expected parent firings** |
| fan-in (Chain children) | Many-parents-to-one-child cardinality | **Many-to-one rollup** |
| Z.A / Z.D / AB.6 / C8a (everywhere) | SPEC section references | (Hide these or relegate to parenthetical) |
| BIN (rail error message) | Bundle Identification Number — anchor for reconciler | (Don't use raw; explain it) |
| daily-eod | Daily end-of-day | **End of business day** |
| P1D | ISO-8601 1-day duration | **1 day** |

---

## 8. Workflow critique — full "stand up a new institution" loop

What I'd actually do if I sat down for the first time, hour by hour:

### Hour 1 — Disorientation

1. **Land on `/`.** Confused. Click the Diagram tab. See a populated diagram for an institution I didn't declare. Realize this is the demo state — but nothing on the page tells me "this is a demo" or "click here to clear and start fresh." Look for "New institution" or "Reset" or "Empty L2". Don't find it. Read the chip strip across the top: `account / account_template / rail / transfer_template / chain / limit_schedule`. Six entity kinds, alphabetical, no story. I'm probably 5-10 minutes in and I haven't done anything yet.

2. **Look for documentation.** The top nav has no "Docs" or "Help" link visible in the screenshots. Bail to whatever README the consulting engagement gave me. Spend 15-30 minutes reading SPEC.md or equivalent to understand the entity-kind dependency graph.

### Hour 2 — Singleton hunt

3. **Find the singletons in the top nav.** Click Instance. Land on the raw YAML textarea (`09`). Bail; this isn't something I can do without an engineer. Mark as "follow-up with engineering."

4. **Open Persona** (`11`, `29`). Fill Institution name + Acronym + Region + Legacy entity. Read the Stakeholders / Merchants / Flavor section headers — pause on "Stakeholders" because the word doesn't fit a counterparty list. Read the help text, realize it's correspondents, type a few. Skip Flavor because I genuinely don't know what surfaces from it. Skip Merchants because I don't know if these are display strings or whether they have to match account_ids elsewhere. Save. Page reloads (or redirects to `/`? unclear from the screenshot set).

5. **Open Theme** (`10`). 60+ fields, no preview. I skip it entirely and accept the defaults — I'll come back after I see the dashboards rendered. Decision happens in <30 seconds because there's no way to evaluate the choices without a render.

### Hours 3-4 — Account declaration

6. **Open account_template** (`04`, `13`). Three rows visible in the demo. I read the existing rows to understand the shape. Declare the roles I know I have: `CustomerDDA{count: ~thousands}`, `MerchantDDA{count: ~hundreds}`, `ConcentrationMaster`, `ExternalCounterparty`, `ACHOrigSettlement`, `CashDueFRB`. Counts are deliberate guesses — I don't know if these are upper bounds, expected, materialized, or hints.

7. **Open account** (`03`, `12`). Declare a few accounts. Use my new roles. Realize partway through that I don't know if I should declare ONE account per role or N where N matches account_template.count. Realize also that the role field is a free-text input (or appears to be from `12`), so I could type `CustomerDDA` or `customer_dda` or `CustomerDda` and have no idea which one wins. Probably go look at the existing demo rows for casing. Probably get it wrong on the first one and not notice.

### Hours 4-12 — The rail tangle

8. **Open rail** (`05`, `14`, `15`, `16`). Click "+ new" → picker (`14`) is the best UX moment of the day. Pick Two-leg. Land on the 20-field form (`15`).

9. **Try to declare `CustomerInboundACH` first** because it's the simplest customer-facing flow. Spend 15-30 minutes per rail filling in fields. The Bundles activity checklist references rails I haven't declared yet. Decision: declare bundles-target rails first (`ACHOriginationDailySweep`, the settlement sweep) and come back. Close form. Open new rail form. Realize that rail ALSO references rails (via Bundles activity again) and I'm in a chicken-and-egg loop.

10. **Hit the orphan-role problem on rail too.** Source role dropdown shows declared roles (good), but if I haven't declared every role yet, the rails I declare reference roles that don't yet exist. Save validation may or may not catch this — the rail validation banner in `21` caught the reconciler-missing case, but it's unclear whether orphan-role-on-rail is also caught.

11. **The form-fill experience itself.** ~5 of the 20 fields have obvious values. ~5 are vocabulary I half-understand (Cadence, Posted requirements). ~5 are vocabulary I don't understand without docs (Bundles activity, Source/Destination origin override, Metadata value examples). ~5 are duration / numeric fields that look fine but I'm not sure about defaults. Per-rail fill time at first read: **20-30 minutes**. Per-rail fill time after the third rail (when I've internalized the vocabulary): **5-10 minutes**. For 20-25 rails: **~3-5 hours of pure fill time**, plus ~2 hours of dependency-untangling.

12. **Hit the rail save banner** at least once on the way through (`21`). The first time the banner appears I think I broke something; the second time I realize it's the validator. By the fifth time I've learned to scroll to the top of the form after every save just to check.

### Hour 12-14 — Transfer templates

13. **Open transfer_template** (`06`, `17`, `26`). Much easier — fewer fields, the Reference panel (when expanded) explains the DSL. I burn ~10-15 minutes per template figuring out the Completion expression DSL on the first one; subsequent ones are ~3-5 minutes.

### Hour 14-16 — Chains

14. **Open chain** (`07`, `18`, `24`). Wire chain children to parents. Re-read the Z.A grammar prose three times for the first chain. Compose 3-5 chains.

15. **Realize I've been declaring chains using parent rails I might want to rename.** The composite-key URL means renaming a parent rail breaks every chain URL that references it. Note to self: don't share chain URLs with the CFO until I'm sure the rail names are final.

### Hour 16-17 — Limit schedules

16. **Open limit_schedule** (`08`, `19`, `25`). Five fields. ~2 minutes per schedule. Easiest entity in the editor.

### Hour 17+ — Deploy, iterate, panic

17. **Click Deploy.** Whatever happens, I have no completeness signal beforehand. If Deploy succeeds I check L1 Dashboard. If charts look wrong (likely) I have to figure out whether it's data, theme, or topology. Whatever I tweak, I'm doing it without a "diff view" — I save the L2 and the only way to see the impact is re-deploy and visually compare.

### Pain points in order of severity

- **The rail × bundle dependency tangle is the #1 friction.** Hours 4-12 are dominated by this. Solve by allowing forward-declarations (a rail can reference another rail that doesn't exist yet, with a "draft" badge until the referent appears), or by a workflow that surfaces a dependency graph and lets me declare in a guided order, or — minimally — by sorting the chips on `/` topologically and adding a "what's blocking" indicator per chip.

- **Orphan-role tolerance is the #2 integrity gap.** Hours 3-4 produce orphan-role accounts; hours 4-12 produce orphan-role rails. Solve by typeaheads + save-time validation + diagram badges. The fact that screenshot 27 shows me creating `test-flobber-1` with role `FlobberCustody` and silently landing on `/` is the worst thing about the editor right now — every other friction-point at least makes me angry IN the moment.

- **Singleton-Instance YAML textarea is the #3 fix.** Hour 2 burns up on Instance and Theme because both are unusable in different ways. Solve with structured form (Instance) + live preview (Theme).

- **No completeness signal anywhere.** Hours 17+ start with crossing my fingers. Solve with a sticky right-rail "Institution scorecard": X accounts of expected Y, A rails declared B referenced-but-missing, C templates, D chains, E limit_schedules, all greened or yellowed against the topology validator's expectation. This is the difference between "I think I'm done" and "the editor agrees I'm done."

- **No live preview on Theme.** Hour 2 + iterations in hour 17+. Solve with swatches per color slot + a "preview chart" panel that uses the current theme on a stock visual.

- **No save-took confirmation.** Throughout. Solve with a toast notification on every save + redirect to the read card.

- **Delete is a footgun.** Latent until hours 17+ when I start refactoring my mistakes; first time I click Delete on a referenced entity I lose progress. Solve with confirm + reference-check.

### Estimated end-to-end timing

At current UX: **2 working days for a 10-rail small institution, 1 working week for a 20-25 rail midsize.** With P1 + P2 recommendations landed: **half a day small, 1-2 days midsize.** The single highest-leverage change is the dependency-order guidance on `/` — that alone collapses hours 4-12 from "tangle" to "checklist." Second highest leverage: typeaheads + orphan validation. Third: Instance singleton form.

---

## 9. Top concerns + non-landing concept names

Top concerns (ordered by severity, with persona reaction):

1. **Orphan-role accept-and-redirect** (`27`). "I made an account with a role that doesn't exist and nothing told me. I now can't trust this editor to keep me out of trouble. Where else is the silent-accept happening?"
2. **Instance singleton = raw YAML** (`09`). "I'm being asked to hand-edit YAML for the field that controls every cadence calculation in my institution. If I miss a comma the audit numbers are wrong and nobody will know until quarter-end."
3. **Delete fires without confirmation or reference-check** (`30`). "I clicked Delete on a rail used by three templates and the editor said nothing. I'm scared to click anything on a populated institution now."
4. **Composite keys in URLs** (`24`, `25`). "Every link I share with the team will rot the moment we rename anything. Audit links need to be stable."
5. **20-field rail form with vocabulary mismatch** (`15`, `16`). "Half the field labels assume engineering vocabulary I don't have. Form-fill time is hours per rail, not minutes."
6. **No dependency-order guidance on `/`** (`01`). "I don't know what to do first. I'm a consultant — I should be able to read this UI and immediately know."
7. **No completeness signal across the editor.** "How do I know I'm done? Today the only test is Deploy."
8. **Validation-fail re-render looks too similar to list-page navigation** (`21`). "I thought my save succeeded. The error banner is there but the page chrome doesn't change enough."
9. **Singletons buried in top nav alongside runtime tabs.** "I would never have found Instance / Theme / Persona without help."
10. **No "Used by" back-references on read cards** (`22`). "Before I edit or delete, I want to know what depends on this thing."

Non-landing concept names: see vocabulary table in § 7. The big-five non-landing terms are **Stakeholders, Flavor, Series, Cadence, Posted requirements**, plus the rail form's **Bundles activity** and the chain form's **XOR / epc / fan-in** cluster.

---

## 10. What's good (genuine wins)

- **Diagram (`02`).** Beautiful as a consumption artifact. With node-click-to-edit + inline-per-edit-page, this becomes the spine of the editor.
- **Subtype picker for rail (`14`).** Right primitive. Constrains the downstream form correctly.
- **account_template list (`04`).** Information density and readability are exactly right; should be the format for every list.
- **limit_schedule's Direction help text (`19`, `25`).** The gold standard for help-text format (what + when-to-use-which-value).
- **Markdown preview on description fields (`28`).** Wired, working, gives confidence that institution prose carries through.
- **Persona's GL accounts triple (`11`, `29`).** Exactly the right shape for chart-of-accounts entry.
- **Rail save validation actually catches the BIN-anchor case (`21`).** The error MESSAGE is jargony, but the validation itself ran and rejected — that's the integrity floor working.
- **Chain form is genuinely sophisticated (`18`, `24`).** The per-row fan-in checkbox + epc input directly models Z.A grammar in clickable form, which is harder than it looks.
- **Edit forms are more polished than read cards (`22` vs `23`).** Unusual order — read should be prettier — but the edit form's sectioning + cards is the right template for the read view to inherit.
- **Institution section in Persona (`11`, `29`).** Every field has "surfaces as" pointer help text. This is the help-text format the whole editor should converge on.
- **Top nav exists.** Foundation for a BUILD/VIEW split rather than greenfield.

---

## 11. Recommendations

### P1 — sign-off blockers (5)

1. **Replace Instance singleton YAML textarea with a structured form** (`09`). Fields, types, validation per field, single Save with banner-on-failure.
2. **Orphan-role guardrail** (`27`). Role inputs become typeaheads over declared roles only; save-time validation rejects orphan roles with banner; account list + diagram surface orphans with red badges.
3. **Delete confirmation with reference-check** (`30`). Modal listing downstream references; block delete OR offer cascade/re-point; never silent-delete.
4. **Persona "Stakeholders" → "Correspondents" rename** (`11`, `29`). Vocabulary mismatch is causing miscategorization at fill time; the label drives the fill quality.
5. **Save-success always redirects to the new/edited entity's read card** (`21`, `27`). No more landing on `/` after save — consultant loses context.

### P2 — workflow improvements (8)

1. **Home screen with "Start here" flow and singleton tiles** (`01`). Numbered dependency order, completeness checkmarks, singletons promoted out of the top nav.
2. **Top nav BUILD/VIEW split** (`01`). Visual separator between editor surfaces and runtime dashboard tabs.
3. **Diagram nodes clickable-to-edit + inline mini-diagram on every edit page** (`02`, `24`). Single highest-leverage UX move.
4. **Theme live-preview swatches + section-level save** (`10`). No more deploy-to-see-color-change loop.
5. **Rail list collapse-to-table + group-by-source_role** (`05`). 21 dense cards become a scannable table; toggle to expanded card view.
6. **Composite keys behind opaque IDs in URLs; keys stay in breadcrumbs/titles** (`24`, `25`). URL stability survives rename + non-ASCII chars.
7. **"Used by" back-references on every entity's read card** (`22`). Before edit/delete, consultant sees blast-radius.
8. **Vocabulary pass** (rail's Posted requirements / Bundles activity / Cadence / Origin overrides; chain's XOR / fan-in / epc). Banker translations + worked examples per field.

### P3 — polish (10)

1. **Read-card visual upgrade** — match the edit form's sectioning (`22`).
2. **Inline currency formatting** on Cap and similar `currency=True` fields (`25`).
3. **Duration picker for P1D / P3D / PT1H** fields instead of raw ISO-8601 (`15`, `16`, `23`).
4. **`<details>` Reference panels default-open on first visit** (`17`, `26`); collapse-by-default after dismissal.
5. **Per-field "surfaces as:" pointers** on Persona Flavor/Stakeholders/Merchants (`29`), and on Theme fields (`10`).
6. **"+ Add stakeholder" button styled as button**, not placeholder line (`29`).
7. **Completion-expression DSL autocomplete** on transfer_template form (`17`).
8. **Inline shape-preview** on chain form when children toggled (`18`, `24`).
9. **Plain-language error messages** with SPEC section pointers in parens, not as the entire message (`21`).
10. **Coverage / Trainer chip tooltips** on the diagram (`02`).

---

## Closing

The bones are solid — the entity-kind decomposition is right, the chain form's modeling is sophisticated, the markdown preview works, the diagram is beautiful. The shortfall is in the surface: dependency-order guidance, validation transparency, vocabulary, and the singleton-Instance YAML textarea. A v2 editor that landed the P1 fixes (5 items) plus 4-5 of the P2 workflow improvements would take consultant-time-to-stand-up-an-institution from "best-case 2 days, realistically a week with hand-holding" to "half a day for a small institution, 1-2 days for a complex one." That's the business case.
