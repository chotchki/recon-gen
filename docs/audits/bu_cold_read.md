# BU vertical-slice cold-read (BU.1.5)

> **Status:** DRAFT 2026-05-30 — cold-read sign-off pass on the BU.1
> + BU.1.6 vertical slice (registry-driven `/training/`, single
> `phantom_rail` entry, clean-baseline reset). Operator drove the
> Studio walk; agent triaged on the resulting screenshots in
> `/tmp/bu_coldread/`. Scope: validate the registry-driven
> abstraction holds the pedagogical premise BEFORE BU.2+ scales it
> to all 21 kinds. P1 findings pause BU.2; P2 inform BU.2 polish;
> P3 queue to backlog.
>
> **Slice under test:** ONE `PlantKindEntry` (`phantom_rail`) wired
> end-to-end through landing + plant form + tour iframe + reset.
> NOT yet built: the other 20 entries, the 5 needs-build plants,
> the L1/L2 family-stripe visual grouping, the sub-nav strip, the
> Before/After tour toggle, the `[?]` glossary triggers, the status
> pills, the docs-export gate.

---

## 1. Cold-read context

### 1.1 What BU.1 + BU.1.6 actually ships

- `common/l2/plant_registry.py` — typed registry primitives
  (`PlantCategory` / `PrimitiveStringField` / `PrimitiveIntField`
  / `TourDestination` / `DashboardCheck` / `PlantKindEntry`) +
  `PLANT_REGISTRY` (1 entry: `phantom_rail` under `L2 Triage gaps`
  family, `L2_TRIAGE` category) + `get_entry` / `entries_by_family`.
- `common/html/_studio_training_v2.py` — `resolve_section` dispatch
  + L1 / L2FT / L2 Triage adapters; `render_training_landing` (one
  accordion family iterating `entries_by_family`);
  `render_training_plant_page` (canonical template walks
  `entry.primitives`); `render_training_tour_page` (iframes
  `entry.tour_destination.primary_url`); `coerce_form_to_kwargs`.
- Routes: `GET /training/`, `GET|POST /training/plant/{kind}`,
  `GET /training/tour/{kind}`, `POST /training/reset` (BU.1.6 — calls
  `run_deploy_pipeline` directly so it SKIPS the BTa.8 demo-gap
  overlay; lands on `/training/?reset=1` for the success banner).
- 22 unit tests (17 BU.1 + 5 BU.1.6) covering the registry shape,
  render shells, form coercion, route smoke, and Lock 9
  `dashboard_check` (plant → `detect_gaps` → planted rail surfaces
  as `unmatched_rail`).

### 1.2 What's explicitly NOT built yet (so don't ladder as P1)

- **20 of 21 registry entries** — only `phantom_rail` is wired.
  Landing shows ONE family with ONE card. No L1 group at all.
  BU.2b enumerates the other 16 existing-primitive entries; BU.3.x
  ships the 5 needs-build plants.
- **Visual L1/L2 group stripes** — Lock 1's blue/amber group
  treatment is BU.4 polish.
- **Sub-nav strip with `[L1]`/`[L2]` badges + "other kinds:" left
  rail** — also BU.4.
- **Before/After toggle on the tour page** — the tour iframe just
  shows the live destination; no toggle, no caption strip. BU.4.
- **`[?]` glossary triggers, `● not planted` status pills, "Show
  defaults' reasoning" details, browser-tab pulse on success** —
  all BU.4 polish.
- **Reset progress indicator** — operator surfaced this during the
  drive; tracked as BU.1.7 (compact-by-default reuse of BTa.9's
  streaming infra).
- **Docs export gate (Lock 9 Test 5 byte-identity)** — BU.5.

### 1.3 The Trainer's first reaction to a 1-kind landing

A real Trainer arriving on `/training/` would react in two waves.
First wave is the abstraction-checker: "Wait, there's supposed to
be 21 of these. Is this broken, or am I looking at a slice?" The
intro paragraph says "Pick a kind below; each plant page lets you
tune the scenario then jump to the dashboard sheet that should
light up" — that text implies there's a kind ZOO down there. The
single card lonely under one accordion is jarring on first paint
EVEN IF you know it's a slice. P2 friction on the cold-read
context message itself: when you're in vertical-slice mode, the
intro should say so (something like "Vertical slice — only
`phantom_rail` is wired today; BU.2b unlocks the other 20"), OR
the slice gets a banner that pins the BU.2b ETA, OR the landing
just hides the family accordion chrome (a single card doesn't
need an accordion). Pick one. The fix expires when BU.2b lands
the other 16 entries, but right now any cold-reader bumping into
the landing assumes "uh, did half the surface get wiped?"

Second wave is the registry-checker: "OK, ONE card — what does
adding the next kind cost?" Operator squinting at the surface
would notice that the card chrome, the form chrome, the tour
chrome are all generic. That's the load-bearing claim of Lock 7,
and it does seem to hold from the screenshots (see §4 below).
That's a genuine win and worth saying out loud before the
nitpicks: the abstraction is observably doing what it promised on
the smallest possible non-trivial example. **This passes the
vertical-slice purpose** — if BU.2b can populate 16 more entries
purely by adding registry rows without touching `_studio_training_v2.py`
once, the round-3+4 design holds. The cold-read finds DO NOT
threaten that load-bearing claim; they're shape polish.

---

## 2. Per-screenshot reactions

### 2.1 Shot 01 — `/training/` landing, pre-reset, accordion open

What the operator sees: top nav (Recon-Gen / Studio / L2 Editor /
ETL Support / Training [bolded] / Dashboards / L1 Dashboard / L2
Flow Tracing / Investigation / Executives / Reference / Docs /
`[?]`). Page header `Training` h1 + intro paragraph + the orange
`↻ Reset to clean baseline` button with the caption "Wipes the
demo DB + reseeds without the BTa.8 demo-gap overlay. Plant after
reset so the dashboard tour shows ONLY your scenario." Below it,
ONE accordion `▼ L2 Triage gaps` already expanded, with one row:
`phantom_rail   Phantom rail`.

Things that work well at first glance:

- The reset button is loud (warm-orange, button-shaped, sits
  immediately under the intro where the eye lands first after
  reading the framing). Matches Lock 4's "destructive but
  frequent" styling intent.
- The reset caption is the right copy at the right size — plain
  prose explanation of what the button does AND why you'd press
  it before planting. The "Plant after reset so the dashboard
  tour shows ONLY your scenario" sentence is teaching the
  pedagogical premise inline. Good.
- Mono-font `phantom_rail` kind badge matches the existing
  `_studio_training` pane's convention (the carry-over from BS-era
  styling). No surprises.

Friction at first glance:

- **The accordion is already expanded with one row inside.**
  Accordion chrome implies "I'm hiding things; click me to
  reveal." A 1-row accordion that's already open is chrome with
  nothing to chrome. The Lock 1 mockup says default-collapsed
  for all families on first paint — this is open. **P3** for the
  vertical slice (drift from spec), but the right fix when
  BU.2b lands is the spec default (default-collapsed unless any
  plants exist in the family).
- **No status pill on the card** — the spec calls for
  `● not planted` / `● planted <hh:mm>` / `⚠ planted but reset
  since`. The cold-read screenshot shows none. Likely
  intentional skip in the slice (status pill needs the
  `<prefix>_training_state` KV row, which isn't built yet), but
  every cold-reader will notice the affordance is missing
  because the kind-badge sits on the row alone — there's
  vertical space to its right that's screaming for a pill.
  **P2.** Track for BU.4 polish.
- **No CTAs on the row at all.** The mockup shows
  `[ Plant this → ]` + `[ Take the tour → ]` side-by-side on
  every card. The cold-read shows the row as a bare label —
  it's only when you grep the rendered HTML or hover over
  `phantom_rail` that you find out the kind name is itself the
  link to the plant page. **P2 trust gap** — the row LOOKS
  like a static list-item, but it's actually a hyperlink with
  no visual affordance signaling that. Add explicit `Plant`
  and `Tour` buttons (or at minimum an `→` glyph) so the
  operator doesn't have to discover-by-hovering.
- The intro copy uses "end-user" (lowercase, hyphenated) but
  the BU.0 lock language uses "End User" (capitalized, no
  hyphen) per the persona-vocab convention. **P3** terminology
  drift; trivial fix when BU.4 lands.
- No counts on the family header — Lock 1's spec calls for
  `▼ <Family name> (<N> kinds · <M> planted)`. Slice shows
  bare `▼ L2 Triage gaps`. **P3** — also expires with BU.4.

The Trainer's load-bearing question after this screen: "What does
the kind LOOK like in the dashboard? What surface does it land
on?" The card body doesn't tell them. The mockup says the card
shows the SHOULD-statement + the `**Action.**` remediation. Slice
shows neither. **P2** — the slice's card is content-thin even by
slice standards. Adding the section body to the card render is
TWO LINES of template change (`{section.body}` /
`{section.what_to_do}`) per Lock 8's render contract, and it'd
make the slice card actually look like a card not a row.

### 2.2 Shot 02 — `/training/?reset=1` after clicking reset

What the operator sees: same landing, plus a green success
banner across the top: `✓ Clean baseline. Demo DB wiped + reseeded
without the bundled-demo gap overlay. Pick a kind below to plant
exactly one scenario; the dashboard tour will show ONLY your
plant.` The card is unchanged below it.

Things that work well:

- **The banner copy is excellent.** It names the action (wipe +
  reseed), the deliberate omission (NO bundled-demo overlay),
  and the pedagogical premise (plant exactly one scenario,
  dashboard shows ONLY your plant). Reads as Trainer-first
  pedagogy, which is the persona. This is the BU.1.6 ship doing
  its job: the banner IS the contract.
- Green soft-tint border + checkmark glyph is the standard
  success-flash chrome from BTa.6 — no new visual language
  introduced. Reuse is the right call.
- The `?reset=1` URL parameter makes the success state
  bookmarkable / refreshable for testing; sensible URL design.

Friction:

- **No timing data in the banner.** BTa.6's flash convention
  shows duration ("✓ Refreshed in 12.4s"). This banner skips
  it. Cold-read flag: the reset took silently somewhere
  between 5 and 30 seconds (cold-reader can't tell from a
  static screenshot, but the BU.1.7 carve-out notes the
  silence is jarring). Adding `(took ~12.4s)` to the banner
  copy is one .format string and makes the wait-then-banner
  cycle feel less like staring at nothing. **P2** — folds
  into BU.1.7's progress-indicator work.
- **The banner doesn't auto-dismiss.** The spec calls for
  10-second auto-dismiss + a close button. Slice shows
  neither. **P3** — once BU.4 polish lands, this banner stays
  forever until the next nav.
- **Nothing has changed visually below the banner.** Before
  reset and after reset both show the same `phantom_rail`
  card. That's correct (the registry doesn't change because
  state changed in the DB) but a cold-reader can't tell at a
  glance that the reset "worked" beyond what the banner
  claims. A `● not planted` status pill flipping back to its
  default state would be the visual confirmation. Pulls into
  the same P2 status-pill gap from §2.1.
- The reset button stays visible AFTER reset, which is
  correct (you might want to reset twice if you're paranoid)
  but the visual treatment doesn't change to indicate "the
  thing I just did is already done." Not a real ask — the
  button is harmless to re-click — but worth noting that the
  reset button is functionally idempotent and the chrome
  reflects that.

The success banner doing its job is the BIG win here. The
operator's "did this actually work?" question is answered cleanly,
and the inline reminder of the pedagogical premise reinforces the
Trainer's mental model right at the moment they're about to plant.
Solid landing pattern.

### 2.3 Shot 03 — `/training/plant/phantom_rail` form, pristine

What the operator sees: top nav unchanged + breadcrumb
`← back to Training · l2_triage › L2 Triage gaps` + page title
`Phantom rail` + body paragraph (Transactions whose rail_name
doesn't resolve... legacy ETL feed... default reads like a
plausible legacy rail). Then a `Plant scenario` form section with
two fields:
- `NUMBER OF ROWS` int input defaulting to `3` + caption "How
  many transaction rows to plant. Triage's volume badge reads
  this count directly."
- `RAIL NAME` text input pre-filled `legacy_card_swipe` + caption
  "The rail_name value to plant. Must NOT match any rail
  declared in your L2 (the whole point of the demo). Default
  reads like a plausible legacy rail."

Below the form: dark-teal `⊕ Plant this scenario` primary button
+ `→ Tour the dashboard` text link. Then a `What to do about it`
section + a `Re-baseline` section with the SAME orange reset
button.

Things that work well:

- **The two-field form is honest about the primitive.** Lock 7's
  primitive-driven shell renders exactly what the
  `PlantKindEntry.primitives` tuple declares — no padding, no
  hidden complexity. A cold-reader can correctly infer "OK so
  to add a kind, I declare its primitives and the form draws
  itself." The abstraction is visible at the surface.
- **Field captions explain the picker reasoning inline.** The
  rail_name caption naming "must NOT match any rail declared
  in your L2 (the whole point of the demo)" is *teaching*
  while you're filling the form. That's the Trainer persona
  exactly — pedagogical even at the level of help text.
  Strongly aligned with Lock 8's typed-section pattern (the
  caption text is sourced from the section, not hardcoded in
  the form template).
- **Body paragraph above the form sets context.** "Transactions
  whose rail_name doesn't resolve to any rail declared in your
  L2 yaml. Usually means a legacy ETL feed..." reads as a
  brief on what you're about to demo. Good prose, sourced from
  `L2TriageGapSection.body` via `resolve_section`.
- **`What to do about it` section after the form** mirrors the
  `**Action.**` paragraph in the landing-card mockup — the
  remediation prose travels with the kind. This is the Lock 8
  payoff (one source of truth in markdown, every consuming
  surface gets it) functioning as designed.
- **Re-baseline section at the bottom** is a deliberate Lock 4
  / Lock 6 callout: if you're on the wrong scenario and want
  to start over, here's the button without having to nav back
  to landing. Operator-aware micro-UX. Good.

Friction:

- **Breadcrumb reads `← back to Training · l2_triage › L2
  Triage gaps`.** Two issues stacked:
  - The `l2_triage` mono token is the registry category enum
    value (`PlantCategory.L2_TRIAGE`). That's the *internal
    identifier*; the breadcrumb should show the *family
    label* (`L2 Triage gaps`). It already does — the family
    label is shown to the right of the chevron — so the
    `l2_triage` token is redundant. Drop it. **P2**
    information-density nit.
  - The breadcrumb's typography is two different shades + uses
    both `·` and `›` separators with no clear hierarchy. The
    `← back to Training` is the actual navigation; the
    `l2_triage · L2 Triage gaps` is contextual breadcrumb. A
    cleaner version: `← back to Training` (link) + ` · ` +
    `L2 Triage gaps · phantom_rail` (non-link context).
    **P3**.
- **The primary CTA is `⊕ Plant this scenario` — the glyph
  reads as "add."** Lock 2 spec uses `⚡` (lightning) +
  `Plant + refresh →` to telegraph "this changes data AND
  triggers a refresh." The slice's glyph + copy understates
  the destructive-ish side of the operation. **P3** — but
  the underlying matview refresh duration question (does the
  plant trigger a refresh, or is the operator expected to go
  click Refresh Data?) is unanswered by the UI itself.
- **The `→ Tour the dashboard` secondary link** is a bare
  text link, not a button. The Lock 2 spec says secondary
  CTA. As-rendered, it competes weakly with the primary
  button — a cold-reader's eye might miss it. **P3**.
- **No "Show defaults' reasoning" expandable details.** Lock
  2's spec calls for an expandable that explains where each
  default value comes from (the picker docstring). Slice
  skips it. **P3** — the slice's two-field form has
  defaults that are self-evident (count=3, rail_name=plausible
  legacy name), so this isn't load-bearing yet. When BU.2b
  ships the drift form (5 fields, 5 picker heuristics), this
  becomes load-bearing.
- **No "Other kinds" left rail.** Lock 2's spec calls for a
  per-kind list down the left edge so the operator can switch
  kinds without going back to the landing. Slice has no
  left rail because there's only one kind to list. Defer; the
  rail makes no sense until BU.2b lands the other 16.
- **The form section header `Plant scenario` and the
  `What to do about it` section header are both h3-ish, no
  hierarchy delta.** The form is the action; the remediation
  is reference material. They should not have equal weight.
  **P3** — a small typography tweak (Plant scenario as h2,
  remediation as h3 with a slightly more muted color).

Net read: the form WORKS, it teaches, and the registry-driven
abstraction is visible (you can see the form is `count` +
`rail_name` because those are the primitives, not because
someone hand-wrote two fields). Polish gaps are real but slice-
appropriate.

### 2.4 Shot 04 — same form after submitting `rail_name="bu1_cold_read_rail"`

What the operator sees: same form, plus a green banner at the
top of the form area: `✓ Planted. Planted phantom_rail with
count=3, rail_name='bu1_cold_read_rail'. Tour the dashboard to
see it surface.` The form fields below the banner have RESET to
their pristine defaults (`3` / `legacy_card_swipe`) — NOT the
values the operator just submitted. The `What to do about it` +
`Re-baseline` sections are unchanged.

Things that work well:

- **The success banner names the planted parameters.** Reading
  `count=3, rail_name='bu1_cold_read_rail'` confirms back to
  the operator EXACTLY what got committed. This is the
  receipts pattern from BTa.6 in a different surface; reuse is
  on-target.
- **The "Tour the dashboard to see it surface" call-to-action
  in the banner copy** points the operator at the next step.
  Pedagogical chaining. Good.
- **The page didn't redirect.** Operator stays on the form;
  the success banner is the only state change. Right call —
  they may want to re-plant with tweaked values, or take the
  tour, or just verify the receipt. No forced nav.

Friction (THIS IS THE BIG ONE):

- **The form fields RESET to defaults after submit, NOT to the
  submitted values.** P1. Operator submitted
  `rail_name="bu1_cold_read_rail"` and the form now shows
  `legacy_card_swipe`. If they hit the primary button again
  (legitimate use case: "plant 5 of the same kind to see
  volume"), they would re-plant `legacy_card_swipe`, NOT
  another `bu1_cold_read_rail`. The banner says one thing;
  the form says another. **Trust killer.** Lock 2's spec
  §2.2 explicitly calls this out — form values STICK after
  successful plant, primary button renames to
  `⚡ Re-plant →` to telegraph idempotent-replay. The slice
  shipped the stick-with-defaults variant.

  Sub-question: maybe the form values DID stick and the
  cold-read screenshot is misleading because the page
  re-rendered the defaults BEFORE the POST handler completed?
  No — the success banner says the POST succeeded with the
  operator's value, but the form below shows the picker's
  default. The render path is reading from defaults, not from
  the just-submitted form. **Bug.**

  This needs to be fixed BEFORE BU.2b ships, because every
  other plant page will inherit the same defect. BU.2b is
  going to add 16 forms with 3-6 fields each, and operator
  fatigue from "did I really just lose all my tweaks?" will
  compound at 16x.
- **The primary button label doesn't change.** Lock 2's spec
  says `⚡ Plant + refresh →` flips to `⚡ Re-plant →` once
  a plant exists for the kind in this session. Slice keeps
  `⊕ Plant this scenario`. Same root cause as the field-reset
  above: the form doesn't know the plant fired. **P2** —
  same fix unblocks both.
- **No "Remove plant" / per-kind undo button.** Lock 2's spec
  §2.2 calls for `[ Remove plant ]` (clears this kind only).
  The Re-baseline section nukes EVERYTHING. Per-kind undo is
  the §7 open question in the mockup doc — the slice
  legitimately defers it. **P3** — track for BU.4 decision.
- **The page title doesn't gain a `● planted` pill.** Lock 2
  §2.2 calls for the kind title to show the plant state.
  Slice doesn't. **P3** — folds into the broader status-pill
  gap.
- **No "View on dashboard ▸" link in the success banner.**
  Lock 2 §2.2 calls for a quick deep-link CTA in the banner.
  Slice's banner says "Tour the dashboard" in PROSE but
  doesn't surface the link. The `→ Tour the dashboard` link
  is below the banner, in the form-area CTA strip, which the
  operator's eye has to bounce back to. **P3** — minor copy
  improvement (make "Tour the dashboard" in the banner an
  underlined link).
- The banner copy uses `count=3` lowercased — matches the form
  field's `NUMBER OF ROWS` label only by inference. Pedantic,
  but a cold-reader has to translate `count` ↔ `NUMBER OF
  ROWS`. Either name the parameters as they appear in the
  banner OR phrase the banner as "3 rows of `bu1_cold_read_rail`
  planted" (use the human form). **P3**.

The form-reset bug is the only finding in the slice that I'd
flag as load-bearing for the BU.2b decision. Everything else is
polish that gets caught in cold-read v5 (BU.6) anyway.

### 2.5 Shot 05 — `/training/tour/phantom_rail` with iframe

What the operator sees: top nav + breadcrumb `← back to plant
form  Tour · Phantom rail` + caption paragraph "The dashboard
surface below is where this violation kind lands. If you plant a
scenario then return here, the row should be visible. **Empty =
plant didn't fire OR the matview refresh hasn't run yet** (click
Refresh Data on the destination if it has its own)."

Below that: `iframe: /etl/triage` mono label + an embedded iframe
showing the full `/etl/triage` page (with its own Recon-Gen top
nav, Studio/L2 Editor/ETL Support tabs, the Refresh Data /
Triage / Probe / Loop overview sub-nav, the Bundled-demo data
disclosure banner, and "12 gaps across 2 kinds" with the
Unmatched rail_name + Missing LimitSchedule sections).

Things that work well:

- **The caption paragraph is honest** about the failure modes
  ("Empty = plant didn't fire OR the matview refresh hasn't
  run yet"). Cold-reader doesn't have to wonder "is my data
  late or my code broken?" — the caption pre-answers. Good
  pedagogical posture.
- **The `iframe: /etl/triage` mono label** above the iframe
  tells the operator what URL they're looking at without
  forcing them to inspect-element. Tiny but appreciated.
- **The iframe-inheriting-its-own-chrome pattern works** —
  the Triage page inside the iframe is fully functional (you
  can click around if you want). Lock 3's isolation-via-
  iframe call is correct; trying to inline-render the Triage
  HTML into the tour page would have been a chrome-collision
  mess.

Friction:

- **No Before/After toggle.** Lock 3's spec opens with the
  `[ ◯ Before  ●  After ]` toggle as the load-bearing UX. The
  slice ships zero toggle. The cold-reader sees the "After"
  state (or whatever state happens to be in the DB) with no
  way to compare against baseline. **P2 for slice; P0/P1
  blocker for BU.4** — the toggle IS the tour's whole point
  per Lock 3.
- **The iframe shows the FULL Triage page including its own
  top nav.** The cold-reader scans two top-navs stacked on
  the page (the host page's nav + the iframe's nav). That's
  noisy. Lock 3 didn't lock the iframe-scoped-vs-iframe-bare
  question; the cold-read raises it. Options:
  - Strip the iframe's top nav via a query-param flag the
    Triage page honors (`?embed=1` hides nav).
  - Deep-link the iframe directly to the section the kind
    surfaces on (e.g. `/etl/triage#unmatched_rail`) +
    auto-scroll the iframe to that section on load.
  - Accept the double-nav as the price of isolation.
  Recommend the first or second; tracker for BU.4 polish.
  **P2** — visual noise is real friction.
- **The iframe loads `/etl/triage` directly** (not
  `/etl/triage#unmatched_rail`). When BU.2b lands kinds where
  the destination is a long-scrolling dashboard sheet (L1
  Dashboard's Drift sheet has multiple visuals), arriving
  somewhere mid-scroll will be confusing. Lock 3's spec
  includes the anchor-fragment convention; slice doesn't
  exercise it. **P2** — track for BU.4.
- **No "What to point out to your trainee" callout below the
  iframe.** Lock 3 §3 spec calls for this. Slice ships
  iframe-and-nothing-after. **P3** for the slice; BU.4
  fills it via the `tour_notes` field on the typed section.
- **Breadcrumb shows `← back to plant form` instead of
  `← back to Training`.** That's GOOD — the operator came
  from the plant form, the back link respects the
  `?from=` breadcrumb pattern. But: what if they navigated
  directly to `/training/tour/phantom_rail` via URL (not via
  the plant form)? Then `← back to plant form` is a guess.
  Lock 4 of BTa.0's `?from=` convention assumes the back-link
  defaults to the parent landing when no `?from=` is set.
  Check that the slice handles the no-`?from=` case
  correctly. **P3** — edge case to verify.
- **No URL-fragment / query-param to pre-load the operator's
  plant settings into the iframe target.** When BU.4 wires
  the Before/After toggle, the After state needs to know what
  plant values to apply — slice doesn't pass any form values
  through to the tour URL. The mockup §3 ASCII shows
  `?<form params>&from=...` as the tour URL shape; slice
  ships bare `/training/tour/phantom_rail`. **P3** for slice
  (no toggle yet, so no need); load-bearing for BU.4.

Net read: the iframe-mounting pattern works at the structural
level. The Before/After toggle gap is the load-bearing miss for
BU.4; the iframe-chrome-noise gap is the load-bearing miss for
cold-read polish.

### 2.6 Shot 06 — `/etl/triage` directly, after the plant

What the operator sees: the same Triage page shown in the
iframe (without the iframe's chrome). Bundled-demo banner at top
("Some gaps below are intentional demo plants ... With a real
ETL hook configured, only your real gaps surface."). Then "12
gaps across 2 kinds." Two cards:
- `Unmatched rail_name    3 rows total · 1 distinct`
- `Missing LimitSchedule  16,658 rows total · 11 distinct`

Both cards collapsed (just the header line + the count).

This is the GROUND-TRUTH check for the pedagogical premise. The
plant operator did was 3 rows of one rail (`bu1_cold_read_rail`).
The `Unmatched rail_name` card shows `3 rows total · 1 distinct`
— that's EXACTLY the plant. The plant lit up the expected card
with the expected shape. The `dashboard_check` in the registry
fired correctly; Lock 9 Test 3 (plant→matview round-trip) is
empirically passing in the operator's hands, not just in unit
tests.

The `Missing LimitSchedule 16,658 rows total · 11 distinct`
card is the residual. Per the priming context: this is the
STRUCTURAL gap (sasquatch_pr.yaml + the bundled demo data don't
declare a LimitSchedule for many of the rail/role pairings, so
the Triage detector legitimately fires on `missing_limit_schedule`
across thousands of baseline rows). Not a BU.1 bug; tracked as
BU.1.9 for separate closure. The reset's clean-baseline removes
the BTa.8 demo overlay (which is the `legacy_card_swipe` /
`__demo_gap_*` planted rows the operator was hitting before
BU.1.6) but doesn't synthesize a LimitSchedule for every
rail/role pair in the spec_example data. Two separate problems;
the reset is doing its job, and the structural gap is L2-shape
work outside Trainer scope.

**Pedagogical premise holds for the BU.1 slice.** Operator
planted ONE kind, and the only kind-tagged-violation that
surfaced on `/etl/triage` is the one they planted (3 rows
matching their count). The `Missing LimitSchedule` is a baseline
condition independent of the plant, NOT a plant collision.

Things to flag at the Triage surface (not BU.1 bugs but worth
noting):

- **The `Missing LimitSchedule` card showing 16,658 rows on
  CLEAN baseline is a P0 cold-read flag on the demo-data
  itself.** Either the spec_example L2 needs LimitSchedule
  declarations for every (parent_role, rail_name) pair, OR the
  Triage detector needs to be parameterized (don't fire on
  rows without an L2-declared expectation, only on rows that
  CONFLICT with one). The current state is: every clean
  Triage view will show 16k+ "violations" that aren't
  violations. Tracked as BU.1.9 per priming, scoped OUT of
  BU.1.5 cold-read laddering.
- The Triage page's count line "12 gaps across 2 kinds" is
  arithmetic-confusing — 3 + 16,658 = 16,661, but the line
  says "12 gaps." Counting kinds vs rows mismatch. **P3** for
  Triage surface (out of BU scope; tracker for BTa polish if
  not already filed).

---

## 3. The pedagogical premise check

The Trainer's load-bearing premise: **plant ONE thing, see ONLY
it surface on the dashboard.** Cold-read against shot 06:

Operator did:
1. Click `↻ Reset to clean baseline` — wipes demo DB + reseeds
   WITHOUT the BTa.8 demo overlay (the fix shipped in BU.1.6
   PLUS the follow-up that also skips L1 plants).
2. Plant `phantom_rail` with `count=3, rail_name='bu1_cold_read_rail'`.
3. Navigate to `/etl/triage`.

Operator sees:
- `Unmatched rail_name 3 rows total · 1 distinct` — THIS IS
  THE PLANT. Count matches (3 rows). Distinct count matches
  (1 rail name). Premise holds.
- `Missing LimitSchedule 16,658 rows total · 11 distinct` —
  baseline condition, NOT plant-attributable. Outside
  Trainer scope (BU.1.9 structural gap).
- No other `Unmatched rail_name` cards. No `legacy_card_swipe`,
  no other BTa.8 overlay residue. **The clean-baseline reset
  is working as designed.**
- No `Unmatched template` card with planted rows. No
  `Missing metadata key` card with planted rows.

**Verdict: PREMISE HOLDS for the slice.** The reset clears the
overlay; the plant surfaces with the exact count + name the
operator submitted; no other violations are attributable to
either the BTa.8 overlay or any pre-baked plant. Trainer can
honestly demo "this is what `phantom_rail` looks like" and the
trainee sees the right thing.

The `Missing LimitSchedule` residual is a STRUCTURAL gap in the
demo data (L2 yaml is incomplete for the LimitSchedule check),
NOT a Trainer-surface bug. It's noisy at present and worth
fixing for cold-read v5 because cold-readers WILL ask "wait,
what's the LimitSchedule thing doing there if I just planted
phantom_rail?" — but the right fix is BU.1.9 (declare missing
LimitSchedules in sasquatch_pr.yaml OR change the Triage
detector's denominator), not anything Trainer-shaped.

Note for the operator: the priming context anticipated this
exact distinction. The reset doing its job + the LimitSchedule
residual being structural-not-plant-related = the BU.1.6 ship
is validated AND the structural gap is correctly classified out.

---

## 4. Registry pattern reactions — does the abstraction show?

The load-bearing claim of Lock 7 + Lock 8: "adding a new kind =
one row in the registry + one section in the markdown handbook +
zero new UI / test files." Cold-read against what's visible to
the operator:

**Evidence the registry IS driving the surface:**

- **The plant form (shot 03) renders exactly the `primitives`
  the registry declares.** `phantom_rail`'s entry has
  `PrimitiveIntField(name='count', ...)` + `PrimitiveStringField(name='rail_name', ...)`,
  and the form shows precisely those two fields — no more, no
  less. If the registry got a third primitive added, the form
  would grow a third field; if one was removed, the form
  would shrink. The shell is data-driven; the cold-reader can
  TELL because the form is exactly as wide as the primitive
  tuple is long.
- **The card title + body prose on the landing (shot 01) and
  the plant page (shot 03) come from the typed section.** The
  body paragraph "Transactions whose rail_name doesn't resolve
  to any rail declared in your L2 yaml..." reads as typed
  prose, not as Python-string concatenation. That's the
  Lock 8 `resolve_section(entry)` pattern functioning — the
  section catalogue is the SoT, the registry just indexes
  into it.
- **The breadcrumb shows the registry's `family` field**
  (`L2 Triage gaps`) without hardcoding. Adding a new family
  in the registry would auto-appear here.
- **The tour iframe URL (shot 05) is the registry's
  `tour_destination.primary_url`** (`/etl/triage`). When BU.2b
  wires the L1 entries, those will iframe
  `/dashboards/l1_dashboard/sheets/...` — same shell, different
  URL field, no per-kind code.
- **The accordion is grouped by `entries_by_family`** — when
  BU.2b populates the registry to 17 entries across 8
  families, this same accordion code will render 8 sections
  with no per-family branches.

**Evidence the abstraction is NOT leaking (good):**

- The landing card is generic-shaped — no `phantom_rail`-
  specific HTML class names or rendering branches visible in
  the rendered output. If you swapped the registry's entry for
  `drift`, the same code path would produce a `drift` card.
- The plant form's field labels are uppercased
  (`NUMBER OF ROWS`, `RAIL NAME`) via the primitive's `label`
  field plus a CSS uppercasing — neither is per-kind. Adding
  a `PrimitiveDecimalField` for some future kind would render
  with the same label treatment.
- The success banner copy template (`Planted phantom_rail
  with count=3, rail_name='bu1_cold_read_rail'`) reads as a
  templated string over `entry.kind` + form kwargs, not a
  per-kind narration. Adding a new kind = no banner template
  change.

**Evidence the abstraction MIGHT be leaking (worth watching):**

- The breadcrumb shows the raw `l2_triage` category enum value
  alongside the human family label. That's a tiny leak of the
  registry's internal naming into the operator-facing surface.
  Either drop the enum value from the breadcrumb (cleanest)
  OR teach `resolve_category_label(category) -> str` and
  display the human form. **P2** — caught in §2.3.
- The card on the landing has no SHOULD-statement / no
  remediation / no CTAs. Either the landing-card render IS
  thinner than the plant-page render (deliberate — different
  surface) OR the landing-card render is incomplete. The Lock
  1 mockup says the landing card DOES carry the SHOULD + the
  Action paragraph. Slice ships a thinner card. **P2** — when
  BU.4 lands the full landing, the card needs to read as
  rich-content not as a list row. The risk is that the
  thinner card was "easy to ship" but doesn't reflect the
  rendering shape BU.2b's 17-card grid will actually need —
  in which case BU.2b would have to re-template the landing
  card on top of doing all the other BU.2b work. Worth
  asking: did the slice's `_render_card(entry)` skip section
  body deliberately, or did it just not get wired? If the
  former, plan to flip it on in BU.2b. If the latter, fix
  before BU.2b parameterizes anti-drift tests over 17 cards.

**Evidence the registry-driven approach will scale:**

- The plant form's coercion via `coerce_form_to_kwargs` is
  primitive-typed (int parses as int, string passes through).
  When BU.2b adds `PrimitiveDecimalField` + `PrimitiveDropdownField`,
  the coercion just needs to grow two new cases; the form
  renderer just needs two new shapes. The shell stays the
  shell. That's the right axis of growth.
- The `dashboard_check` round-trip (Lock 9 Test 3) IS visible
  in shot 06 — the planted count surfaces on the destination
  exactly as the registry declared it would. This is the
  parameterized-test contract being demonstrated EMPIRICALLY
  by a human operator, not just in CI. When BU.2b expands to
  17 entries, the same test parameter shape covers all of
  them. Good infrastructure investment that paid off in
  cold-read.

**Cold-read verdict on the abstraction:** the slice validates the
Lock 7 + Lock 8 + Lock 9 architecture cleanly. The card-content
thinness (only finding worth flagging) is fixable inside BU.2b,
not architectural. No abstraction leaks that suggest the pattern
won't scale to BU.2+ kinds.

---

## 5. What needs fixing BEFORE BU.2b scales

Sorted by "would compound across 17 entries if left."

### 5.1 Generalizable bugs (compound at 17x)

- **Form fields reset to defaults after POST instead of
  preserving submitted values.** §2.4. ONE fix in the shared
  `render_training_plant_page` shell; affects every kind. If
  shipped to BU.2b as-is, operator fatigue compounds at 17x.
  **P1.**
- **Landing card omits SHOULD-statement + remediation +
  CTAs.** §2.1 + §4. ONE template change to the shared
  `_render_card(entry)` helper; affects every card. Slice's
  thin card OK as a placeholder but BU.2b lands 17 cards
  that need to be readable as content not as a list row.
  **P2.**
- **No status pills on cards** + no `● planted` indicator on
  page titles. §2.1 + §2.2 + §2.4. Wiring the
  `<prefix>_training_state` KV row is shared infrastructure;
  shipping it once unblocks every consuming surface.
  Reasonable to defer to BU.4, but flag the dependency
  explicitly. **P2.**
- **Breadcrumb shows raw category enum (`l2_triage`)
  alongside human label.** §2.3. ONE fix in the breadcrumb
  renderer; affects every plant + tour page. **P2.**
- **Tour iframe doesn't deep-link to the section / sheet
  fragment the kind surfaces on.** §2.5. Requires
  `TourDestination.primary_url` to support
  `{anchor_fragment}` or for the renderer to append
  `#section`. Affects every kind's tour. **P2.**

### 5.2 Slice-specific (won't compound)

- Reset banner missing auto-dismiss / close button — §2.2.
- Reset banner missing duration — folds into BU.1.7 progress
  indicator. §2.2.
- Plant form's `⊕` glyph vs `⚡` glyph + button copy — §2.3.
- "Show defaults' reasoning" expandable details — slice's
  2-field defaults are self-evident, becomes load-bearing
  once a 5-field form lands. §2.3.
- "Other kinds" left rail — makes no sense at 1 entry; lands
  with BU.4. §2.3.
- Per-kind `[ Remove plant ]` undo button — §2.4. §7 open
  question; defer to BU.4 decision.
- Before/After toggle on tour — §2.5. BU.4 work item.
- "What to point out" callout below tour iframe — §2.5. BU.4.
- Iframe-chrome noise (double top nav) — §2.5. BU.4 decision.

### 5.3 Generalizable bug WORTH naming: reset button copy works for the slice

The orange `↻ Reset to clean baseline` button's caption reads:
"Wipes the demo DB + reseeds without the BTa.8 demo-gap
overlay. Plant after reset so the dashboard tour shows ONLY
your scenario."

Two things worth flagging:

- The phrase "BTa.8 demo-gap overlay" is internal-implementation
  language. A real Trainer (CSE walking an End User) doesn't
  know what BTa.8 is. **P2** — the user-facing copy should
  name the overlay by what it IS, not by its ship phase. e.g.
  "without the bundled demo gaps" or "without the bundled
  legacy-rail / orphan-template noise." When BU.4 polish lands,
  scrub the phase references out of operator-facing strings.
- The reset wipes BOTH the L2 gap overlay AND the L1 plant
  scenarios (per priming context — the follow-up that landed
  after BU.1.6's initial ship). The current copy doesn't
  reflect the L1 plant skip. Once BU.2b lands the L1 entries,
  the operator will plant an L1 kind (e.g. `drift`), hit reset,
  and want to know that the L1 plant gets wiped too. Update
  the caption: "Wipes the demo DB + reseeds without the
  bundled demo gaps OR any L1/L2 scenario plants." **P2** —
  generalizable, ships with BU.2b context.

### 5.4 BU.1.9 structural gap reminder

The `Missing LimitSchedule 16,658 rows · 11 distinct` baseline
condition is not a Trainer bug but WILL show up in every BU.6
cold-read screenshot of `/etl/triage` and confuse every reader.
Two paths:
- BU.1.9 declares missing LimitSchedules in sasquatch_pr.yaml
  so the baseline is clean.
- The Triage detector for `missing_limit_schedule` gets a
  denominator change (only fire when there's a positive L2
  expectation).
Operator's call which one to ship; flag in the BU.1.9 cell
description either way. Out of scope for BU.1.5 ladder.

---

## 6. TL;DR ladder

Ranked by impact-to-fix on the BU.2+ scale-up path. **Bold = MUST
fix before BU.2b lands 16 more entries through the same code path.**

### P1 (trust killer / load-bearing)

1. **Plant form fields reset to defaults after successful
   POST instead of preserving submitted values.** §2.4. ONE
   fix in shared `render_training_plant_page` shell;
   compounds at 17x in BU.2b. Banner says one thing
   (planted `bu1_cold_read_rail`); form says another
   (showing `legacy_card_swipe`). Re-plant clicks will
   produce wrong-named plants.

### P2 (friction / will compound)

2. Landing cards are content-thin — missing SHOULD-statement,
   remediation paragraph, and explicit `Plant`/`Tour` CTAs.
   §2.1 + §4. ONE shared `_render_card` template change.
   Slice's 1-row "card" is unreadable as a card.
3. No status pills on landing cards + no `● planted`
   indicator on plant-page titles. §2.1 / §2.2 / §2.4. Needs
   the `<prefix>_training_state` KV row infrastructure but
   is the load-bearing visual confirmation that the reset /
   plant worked. Defer to BU.4 acceptable; flag the
   dependency.
4. Breadcrumb leaks the raw `l2_triage` category enum value
   alongside the human family label. §2.3. ONE fix in the
   breadcrumb renderer.
5. Tour iframe doesn't anchor-fragment-deep-link to the
   section / sheet the planted kind surfaces on. §2.5.
   Requires `TourDestination` to support a fragment; loaded
   page just shows whole destination + cold-reader has to
   scroll to find their plant. Compounds when L1 dashboard
   sheets (which are long) get iframed.
6. Tour iframe shows the full destination including its own
   Recon-Gen top nav — double-nav visual noise. §2.5. Either
   `?embed=1` flag on the destination OR accept as cost of
   isolation. Decide BU.4.
7. Reset button caption uses internal phase language
   ("BTa.8 demo-gap overlay") instead of user-facing
   naming. §5.3. ONE copy fix; generalizable.
8. Reset banner / plant banner missing auto-dismiss + close
   button + (optionally) duration. §2.2. Folds into
   BU.1.7's progress-indicator work; coordinate.

### P3 (polish, queue for BU.4 / cold-read v5)

9. Family accordion default-open at 1 entry; spec says
   default-collapsed; family header counts (`N kinds · M
   planted`) missing; intro paragraph terminology drift
   ("end-user" vs "End User"). §2.1.
10. Plant form's `⊕` glyph + "Plant this scenario" copy
    softer than spec's `⚡` + "Plant + refresh →" intent.
    §2.3.

(Stopping at 10 — additional P3 items in §5.2 are real but the
top 10 capture the load-bearing ladder.)

---

## 7. Recommendation to operator

**Ship verdict: BU.1 + BU.1.6 PASS the vertical-slice exit
criterion.** The registry-driven abstraction holds; the
pedagogical premise validates against ground truth on
`/etl/triage`; the clean-baseline reset does what BU.1.6
promised. No findings threaten the BU.2+ scale-up.

**Pause before BU.2b for ONE fix:** the form-fields-reset-on-POST
P1 bug (§2.4) needs to land before BU.2b parameterizes the
shared plant-page shell over 16 more entries. The fix is one
template change in `render_training_plant_page`; <30 min of
work. Without it, every plant page BU.2b ships will silently
discard the operator's tweaks.

**Group the P2 items into BU.2b + BU.4:**
- BU.2b takes: landing-card content thinness (P2 #2),
  breadcrumb enum leak (P2 #4), reset caption rewrite
  (P2 #7).
- BU.4 takes: status pills (P2 #3), tour iframe anchor +
  chrome (P2 #5 + #6), Before/After toggle, sub-nav strip,
  all the polish.
- BU.1.7 takes: reset progress indicator + banner
  auto-dismiss / duration (P2 #8 partial).

**Defer the P3 items to cold-read v5 (BU.6).** They're
real but won't compound across the registry.

**Do not block BU.2b on:** the LimitSchedule structural gap
(BU.1.9, scoped out). It WILL show up in BU.6 cold-read
screenshots and need a fix before phase exit, but it's L2-shape
work, not Trainer-shape work.

**Operator decision points the cold-read can't make:**
- Whether to defer the status-pill plumbing to BU.4 or pull
  it forward to BU.2b. Recommend BU.4 (status pills are
  decoration; not load-bearing for BU.2b's 16-entry
  parameterization).
- Whether the per-kind `[ Remove plant ]` button is worth
  building or can be permanently rolled into Reset. Open
  question in the mockup doc §7 Q3; cold-read doesn't have
  strong evidence either way.
- Whether the L1/L2 visual stripe grouping (Lock 1 blue/
  amber treatment) belongs in BU.2b or BU.4. The slice doesn't
  test it because there's only 1 family; recommend defer to
  BU.4 with BU.2b shipping a flat family list.

End of cold-read.
