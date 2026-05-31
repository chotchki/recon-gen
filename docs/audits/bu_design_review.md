# BU design review — mockup vs shipped

> **Date:** 2026-05-31
> **Method:** cold-read comparison of `bu_design_mockups.md` (the
> spec) against 18 shipped screenshots in `/tmp/bu_design_review/`.
> Does NOT touch source. Surfaces deltas the operator can triage.
> Companion to `bu_2b_cold_read.md` (HTML-only cold-read, P1s
> already fixed) and `bu_cold_read.md` (earlier round).

---

## Headline verdict: **SOFT-MISS**

Three out of three canvas surfaces (landing, plant, tour)
shipped a meaningful subset of what the mockup specified. The
core flows work — registry iteration is real, plant submits, tour
embeds the right destinations — and the cosmetic deltas behind it
look in-flight rather than abandoned (recent BU.4-stage-X commits
keep filling in family taxonomy, kind_qualifier rendering,
collapsed-by-default accordions, etc.).

But the gap is wider than "polish." Three pieces of the mockup's
operator loop are missing entirely:

1. **Per-card CTAs on the landing** — `[ Plant this → ]` /
   `[ Take the tour → ]` buttons + status pill + `[?]` glossary
   icon on EACH card. The shipped landing is a passive catalog
   where the whole row is one link; no CTA disambiguation.
2. **The Before/After toggle on tour pages** — the entire `[ ●
   Before     ◯  After ]` switching mechanic is absent. Tour
   pages today are a static iframe of "wherever this kind lands."
   The "tour" framing (walking a trainee from clean-state to
   planted-state) cannot happen without the toggle.
3. **The plant-page left rail** — the 21-kind navigation rail
   that lets the operator hop between kinds without bouncing back
   to landing. Today the only way back is the breadcrumb.

These three together cap how compelling the surface is in a
trainer's hands. The catalog is browseable, individual plants
work, but the "narrated demo loop" the mockup designed for cannot
be performed end-to-end.

Counts: **3 P1, 10 P2, 8 P3 = 21 deltas.**

### Per-section delta summary

| Section          | Conformance | P1 | P2 | P3 | Notable miss                                  |
|------------------|-------------|----|----|----|-----------------------------------------------|
| §1 Landing       | ~60%        | 1  | 4  | 2  | No per-card CTAs / status pills / [?] icon   |
| §2 Plant page    | ~70%        | 1  | 4  | 3  | No left rail; no "Show defaults' reasoning"   |
| §3 Tour page     | ~40%        | 1  | 2  | 2  | No Before/After toggle (the central UX)      |
| §4 Reset         | ~85%        | 0  | 0  | 1  | Per-page placement OK; flash UX uncheckable  |
| §5 Sub-nav strip | 0%          | 0  | 0  | 0  | Marked "optional" in §5.3 — not a delta     |
| §6 /data removal | n/a         | 0  | 0  | 0  | Not in screenshot set                       |

---

## P1 — block release

### P1.1 — Landing cards have NO per-card CTAs, status pill, or `[?]` glossary icon

**What the mockup said** (§1.3, §1.4 — the card spec is detailed
across all of §1.3a/b/c and §1.4):

```
┌──────────────────────────────────────────────────────────┐
│ [drift] [?]                              ● not planted   │
│ Sub-ledger drift                                         │
│                                                          │
│ For every account on every day, the sum of...            │
│                                                          │
│ Action. When a row surfaces, the ETL has skipped a leg.  │
│                                                          │
│ [ Plant this → ]   [ Take the tour → ]                   │
└──────────────────────────────────────────────────────────┘
```

Each card carries: bracketed kind badge, `[?]` glossary opener,
status pill (`● not planted` / `● planted hh:mm` / `⚠ planted
but reset since`), card title, body, **"Action." prose**, AND
two distinct CTAs.

**What the screenshot shows** (`01_landing.png`,
`02_landing_reset_banner.png`): cards are just two lines —
mono-identifier (`phantom_rail`) + bold human label, then a body
paragraph. **No CTAs, no status pill, no `[?]` icon, no "Action."
paragraph.** The whole row appears to be one clickable link (no
visual affordance distinguishes "Plant this" from "Take the
tour" — they must be choosing one by default).

**Severity:** P1. This collapses two operator choices ("I want to
plant" vs "I want to tour") into one implicit choice. The
mockup's entire info architecture for the landing — see the kind
→ decide-direction-on-card pattern — is reduced to a flat
catalog. The operator can't preview status (already planted?),
can't get a glossary popover, can't skip past the plant form
straight to the tour.

**Suggested fix:** render the card template per §1.3's spec — at
minimum `[ Plant this → ]` + `[ Take the tour → ]` CTAs at the
bottom of each card. Status pill + `[?]` icon are second-pass.
Status pill needs the `<prefix>_training_state` KV row from §1.5
("State source for status pills" caption) — that's BU-shape new
work.

---

### P1.2 — Plant page has NO left rail of "other kinds"

**What the mockup said** (§2.1 canonical plant page mockup, left
column):

```
┌──────────────┬───────────────────────────────────────────────────┐
│ Other kinds  │ Form (defaults from default_scenario_for)         │
├──────────────┼───────────────────────────────────────────────────┤
│ Balance      │                                                   │
│ ▎ drift  ●   │ Account                                           │
│   ledger_dft │ ...                                               │
│   overdraft  │                                                   │
│ Policy       │                                                   │
│   limit_brch │                                                   │
│   eod_brch   │                                                   │
│ Aging        │                                                   │
...
```

The mockup explicitly calls out (§2.1, "Operator-facing strings"):
*"Left rail: 12-kind list grouped by family (mirrors §1's
taxonomy). Current kind highlighted with a `▎` left-edge stripe
and a `●` after the label. Click switches to another kind's
plant page; the URL pushes (browser back returns to current)."*

**What the screenshot shows** (`13_plant_drift.png`,
`14_plant_limit_breach_outbound.png`, `15_plant_stuck_pending.png`,
all 11 plant screenshots): single-column layout. Form sits in the
middle. **No left rail at all.** Only navigation away is the top
`← back to Training` breadcrumb.

**Severity:** P1. The mockup explicitly designs for the
"compare-across-kinds" workflow (operator opens drift, says "what
does overdraft look like?", clicks overdraft in the rail). Today
that operator must hit Back, scroll the accordion grid, find
overdraft, click. Three clicks not one. For a trainer iterating
on multiple plants in a session, this is friction at the most
frequently-used affordance.

**Suggested fix:** add the left rail per §2.1's mockup. Pure
registry iteration — the same iteration shape as the landing
accordion, just rendered as a sidebar instead of cards. Lock 7's
"single registry walk" principle applies.

---

### P1.3 — Tour pages have NO Before/After toggle

**What the mockup said** (§3.0, §3.1, §3.2, §3.3 — the toggle is
the SPINE of the tour-page design):

```
┌─────────────────────────────────────────────────┐  [ Done ]
│  ●  Before     ◯  After                         │
└─────────────────────────────────────────────────┘

 What you're looking at: Baseline demo data. The Drift sheet
 shows 0 rows; no account on any day has a drift violation.
```

When toggled to After:

```
│  ◯  Before     ●  After      [ Re-plant ]       │  [ Done ]
│
│  What you're looking at: One drift violation planted on
│  cust-001 5 days ago. The Drift sheet now shows 1 row...
```

Toggle wires (§3.6): click `After` → `POST /training/tour/<kind>/plant`
→ refresh matviews → reload iframe. Click `Before` →
`POST /training/tour/<kind>/reset-kind` → reload iframe. Progress
bar in iframe during the ~10s wait. Browser-tab pulse on
completion.

**What the screenshot shows** (`30_tour_phantom_rail.png`,
`31_tour_drift.png`, `32_tour_chain_orphan.png`): the tour page
is a static iframe of the destination. **No toggle, no Before
state, no After state.** The page caption reads "The dashboard
surface below is where this violation kind lands. If you plant a
scenario then return here, the row should be visible" — i.e. the
operator is expected to plant ELSEWHERE and come back. The "tour"
is just "look at the dashboard."

**Severity:** P1. This is the central mechanic of the tour-page
design. Without the toggle, the tour page is functionally
identical to deep-linking from the landing CTA to the underlying
dashboard sheet — which is what the BS-era pane did. The "narrate
the before-and-after" loop the mockup designed (§5.2's post-BU
flow ending with "0 → 1 KPI delta + What to point out bullets")
cannot happen.

**Suggested fix:** implement the toggle per §3.6 wiring spec —
`POST /training/tour/<kind>/plant` + `POST /training/tour/<kind>/reset-kind`
endpoints, two-state pill, iframe reload with cache-busting param.
The reset-kind endpoint falls back to full reset under the hood
per §7 Q3's round-1 decision (simpler implementation; full reset
is fast enough). Browser-tab pulse + progress bar are second-pass
polish.

---

## P2 — next polish cycle

### P2.1 — Landing top-level L1/L2 grouping is absent

**Mockup** (§1.2 mockup + §1.3 "L1 vs L2 visual distinction"
caption): two top-level group containers labeled "L1 INVARIANT
VIOLATIONS (15 kinds)" and "L2 FEED-CONTRACT + L2FT HYGIENE (9
kinds)" — each with its own stripe color (cool-blue for L1,
warm-amber for L2). Family accordions nest INSIDE the group
containers; two clicks to open a family card.

**Screenshot** (`01_landing.png`): 8 family accordions render in
a flat list — no L1 / L2 group containers, no stripe color, no
section headers. Per-card `L1` / `L2` / `L2FT` badge is also
missing (mockup §1.3b/c).

**Severity:** P2. The taxonomy ordering inside is right, but the
visual grouping that helps a trainer say "I want to demo an L2
issue" is lost. The mental model is flattened. (Borderline P1 if
the operator considers the L1/L2 distinction important to surface
at-a-glance; demoting to P2 because the family names themselves
prefix with `L1 ` / `L2 ` / `L2FT ` which carries some of the
signal.)

**Fix:** wrap the 5 L1 families in one `<section>` with header
"L1 invariant violations (15 kinds)" + cool-blue accent; wrap the
3 L2/L2FT families in another with warm-amber. Per-card category
badge (`L1` / `L2` / `L2FT`) is a separate small fix in the card
template.

---

### P2.2 — Landing family ordering puts L2 Triage first, not L1

**Mockup** (§1.2): L1 group listed FIRST (Conservation → Cap →
Aging → Chain → Audit), L2 group SECOND (Triage → Coverage →
L2FT Hygiene). Default-collapsed; first family open under
BU.4 stage 4.

**Screenshot** (`01_landing.png`): family order is L2 Triage
gaps (open) → L1 Conservation → L1 Cap → L1 Aging → L1 Chain
coherence → L1 Audit → L2 Coverage gaps → L2FT Hygiene. L2
Triage being first is plausibly the "sort by planted DESC, then
family name" rule (§1.5 captions) firing when 0 plants exist —
but the mockup explicitly puts L1 first by family-name within
group, and §1.5's sort applies WITHIN a family, not across all.

**Severity:** P2. Reinforces P2.1 — the operator's first
impression is "this is an L2 tool" because the first thing they
see open is L2 Triage gaps. The mockup's design intent ("L1 is
the primary surface, L2 is the secondary surface") inverts.

**Fix:** order families by group first (L1 group, then L2 group),
then by within-group canonical order. The default-open accordion
should be the FIRST L1 family (Conservation) not the
alphabetically-first family.

---

### P2.3 — Landing has no plant-counter / footer tip / intro stats

**Mockup** (§1.2 + §1.4): intro line `21 violation kinds (15 L1
+ 5 L2 ETL-feed + 4 L2FT Hygiene) · 0 planted this session`.
Footer tip: `Tip: plant several kinds at once, then take each
tour to compare. Reset to baseline clears every plant.` Per-family
header: `▶ Balance integrity (3 kinds · 0 planted)`.

**Screenshot** (`01_landing.png`): family headers show "(N kinds)"
only — no planted count. No intro stats. No footer tip.

**Severity:** P2. The "0 planted this session" copy is functional
state-feedback the operator wouldn't notice missing — until they
plant 2 things, walk away, come back, and have no on-page memory
of what's still planted. The footer tip is pure onboarding fluff;
absence is forgivable.

**Fix:** add the per-family planted-count to family-header
template; add the intro stat + footer tip strings to the landing
template. Both data-driven from the same `<prefix>_training_state`
KV row P1.1 above needs.

---

### P2.4 — Plant page primary CTA copy + glyph differs from mockup

**Mockup** (§2.1): `[ ⚡ Plant + refresh → ]` (lightning glyph,
copy emphasizes "+ refresh"). Caption below: `~10s · refreshes
matviews so the dashboard immediately reflects the plant`.

**Screenshots** (`10_plant_phantom_rail.png` through
`20_plant_chain_orphan.png`, all 11): primary CTA reads
`⊕ Plant this scenario`. No caption below.

**Severity:** P2. Two losses: (a) the lightning glyph reinforces
"this changes data" — important affordance for a button that
truncates+reseeds; (b) the "+ refresh" copy + "~10s" caption set
duration expectations so the operator doesn't double-click during
the wait. Operator's mental model of "what just happened when I
clicked that" is fuzzier.

**Fix:** swap glyph to ⚡; rename to "Plant + refresh →"; add the
duration caption underneath.

---

### P2.5 — Plant page has NO "Show defaults' reasoning" disclosure

**Mockup** (§2.1): `▸ Show defaults' reasoning (4 picks)`
expandable section below the form, lists each picker decision
with the alternatives + tiebreaker rule. Data sourced from
`__doc__` strings on the `_pick_*` functions in
`common/l2/auto_scenario.py`.

**Screenshots**: not present on any plant page screenshot.

**Severity:** P2. The whole "defaults from default_scenario_for"
framing in the form header is opaque — the operator sees pre-
filled values and has no way to know WHY those values were picked
or what the alternatives were. For trainers who'll be asked
"why ACHCredit and not WireDebit?" by a trainee, this is the
answer-key panel that's missing.

**Fix:** add the expandable section per §2.1's spec. Cheap if
picker functions already have docstrings; otherwise BU-adjacent
work to add them.

---

### P2.6 — Plant page secondary CTA reads "Tour the dashboard" not "Take the tour with these settings"

**Mockup** (§2.1): `[ Take the tour with these settings → ]` —
serialize form values into the URL fragment, navigate to
`/training/tour/<kind>?<form params>&from=...`. Tour page then
auto-plants on Before→After toggle using the serialized settings.

**Screenshots**: secondary CTA reads `→ Tour the dashboard` — no
form-value handoff implied.

**Severity:** P2. Tied to P1.3 (no toggle = no auto-plant
mechanism). If the toggle ships, this label should change to
match the mockup so the operator knows their form edits will
carry over to the tour.

**Fix:** rename CTA; add form serialization at navigation time;
tour endpoint reads URL params as initial form state. Both ends
move together.

---

### P2.7 — Tour page has NO "What to point out to your trainee" callout

**Mockup** (§3.1, §3.3, §3.7, §3.10 — every tour page mockup):
below the iframe, a bullet list:

```
What to point out to your trainee:
 · The KPI flipped from "0" to "1" — the matview now sees the gap.
 · The day axis shows a bar at -5d matching the plant's days_ago.
 · The table row carries the account_id + drift_amount; ...
```

Sourced from a new `tour_notes` field on `InvariantSection`
(§3.3 captions); BU.5 was to land the field + populate 2-3 kinds;
the long tail backlogged. Kinds without notes get a placeholder.

**Screenshots** (`30_tour_phantom_rail.png`, `31_tour_drift.png`,
`32_tour_chain_orphan.png`): no callout below the iframe.

**Severity:** P2. The mockup's whole framing — "this is a tour
page, here's what the trainer narrates" — depends on this
callout. Without it, the tour page is "here's the dashboard, you
figure it out." Lower-impact for an experienced trainer who knows
what to say; high-impact for a junior trainer using this as
walkthrough material.

**Fix:** add the `tour_notes` field to the typed sections; render
below iframe; placeholder copy for unauthored entries. Some can
ship from the bundled markdown (the bullets in the mockup are
real prose the trainer would say).

---

### P2.8 — Tour page title is missing kind badge + category badge

**Mockup** (§3.0): `[<kind>] [<category>] Tour: <Human title>`
where `<category>` ∈ {L1, L2, L2FT}. So a tour page reads
`[drift] [L1] Tour: Sub-ledger drift`.

**Screenshot** (`30_tour_phantom_rail.png`): page title is
`Tour · Unmatched rail_name`. No bracketed `[phantom_rail]`
badge, no `[L2]` category badge.

**Severity:** P2. Per the mockup's framing, the kind badge serves
as a deep-link breadcrumb (operator pastes a URL into Slack,
recipient sees the kind name immediately). The category badge
prevents confusion when bouncing between L1 / L2 / L2FT tours
that share names.

**Fix:** title template change. One line.

---

### P2.9 — Plant page lacks SHOULD blockquote styling

**Mockup** (§2.1): the SHOULD statement renders as a `>`
blockquote:

```
> For every account on every day, the sum of signed_amount over the
> account's transactions should equal the daily_balances.balance
> for that account+day.
```

**Screenshot** (`13_plant_drift.png`): the SHOULD text renders as
plain prose under the H1. No `>` quote marker, no left-border
stripe, no italic — visually indistinguishable from a regular
description paragraph.

**Severity:** P2. The blockquote convention matters because the
SHOULD statement is the AUTHORITATIVE invariant definition (it's
what `L1_Invariants.md` carries verbatim). Trainer's mental model:
"this is what we promised; the form below is what we're going to
break to demo what happens." Without the visual distinction, the
operator can't quickly see "where's the invariant declaration
ending and the demo prose starting?"

**Fix:** wrap `section.short_statement` in `<blockquote>` with
Tailwind border-l-4 + italic styling. Small CSS change.

---

### P2.10 — Reset button text differs from mockup

**Mockup** (§4.2): `[ ↻ Reset to baseline ]` — short copy.

**Screenshots** (every page header): `↻ Reset to clean baseline`
— minor wording shift ("clean" inserted). Re-baseline section on
each plant page also uses `↻ Reset to clean baseline`.

**Severity:** P2. Cosmetic. "Clean" disambiguates "back to a
known-clean state" vs the mockup's slightly more abstract
"baseline." Not a defect — implementer's call. Flag only because
mockup was specific.

**Fix:** leave as-is OR shorten to match mockup. Operator's
preference.

---

## P3 — backlog

### P3.1 — Plant page header doesn't show `[<kind>]` badge in title

**Mockup** (§2.1): page title `[drift]  Plant: Sub-ledger drift`.

**Screenshots**: title is just human label `Sub-ledger drift`.

**Severity:** P3. Same shape as P2.8 for tour pages — the mono
identifier is useful in URL-share contexts. Cosmetic.

**Fix:** prepend `<code>[{kind}]</code>` to title template.

---

### P3.2 — Plant page lacks "Plant: " prefix in title

**Mockup**: `Plant: Sub-ledger drift` — distinguishes from the
tour title pattern `Tour: Sub-ledger drift`.

**Screenshot**: just `Sub-ledger drift`.

**Severity:** P3. Cosmetic; the breadcrumb + URL already give
context. Minor mental-model help.

**Fix:** add `Plant: ` prefix in title template.

---

### P3.3 — Breadcrumb shows family pretty-name, not "Training" parent

**Mockup** (§2.1): `← Back to Training` (sticky).

**Screenshots** (every plant page): breadcrumb reads `← back to
Training | L1 Conservation` — adds family label as second
breadcrumb item.

**Severity:** P3. Not a regression — bu_2b_cold_read.md P2.12
notes this. Family label is useful context; "Training" + family
duplicates the family-pretty-label vs category-pretty-label
information. Cosmetic.

**Fix:** drop family label OR replace breadcrumb with just
`← Back to Training`. Operator's call.

---

### P3.4 — Plant page has no `Studio · Training · Plant` page-mode strip

**Mockup** (§2.1): page-mode strip `Studio · Training · Plant`
above the breadcrumb.

**Screenshots**: this strip is absent. The top nav shows
`STUDIO | L2 Editor | ETL Support | Training` instead.

**Severity:** P3. The Studio · Section · Mode pattern is the
existing `/etl/run` chrome convention; tour/plant pages skipping
it slightly breaks visual consistency with the other Studio
surfaces. Probably an in-flight decision (the screenshot's
nav-bar approach is cleaner).

**Fix:** add the strip OR confirm the new pattern is intentional
+ update mockup.

---

### P3.5 — Tour page lacks `[ Done ]` button

**Mockup** (§3.0, §3.1): top-right of toggle strip, `[ Done ]` —
back to landing. Distinct from sticky `← Back to Training`
breadcrumb because "Done" implies "completed this tour."

**Screenshots**: no Done button. Only `← back to plant form`
breadcrumb at top.

**Severity:** P3. Tied to P1.3 (no toggle = no toggle strip = no
home for Done button). Will resurface when toggle ships.

**Fix:** ships with toggle work.

---

### P3.6 — Tour iframe has no `~70vh, full width` chrome wrapper

**Mockup** (§3.1): iframe presented inside a rounded card chrome
suggesting a "demo screen" — visual delimiter so operator sees
"here ends the trainer chrome, here begins the iframe."

**Screenshot** (`31_tour_drift.png`): iframe is full-width
bleeding to page edges; no card chrome.

**Severity:** P3. Cosmetic but useful for the "this is an
embedded view of another surface" mental model. Lower priority
than the toggle.

**Fix:** wrap iframe in `<div class="rounded-lg border ...">`.

---

### P3.7 — Plant page success flash, planted-state pill, "Re-plant" CTA rename — UNCONFIRMABLE from screenshots

**Mockup** (§2.2): after a successful plant, the page renders
with: a flash `✓ Planted at hh:mm:ss — N rows + matview refresh`
+ a `● planted` pill next to the title + the primary CTA renames
to `[ ⚡ Re-plant → ]` + a new `[ Remove plant ]` secondary
action appears.

**Screenshots**: only show before-plant states (no `*_after_post`
screenshot in set). Cannot confirm or deny.

**Severity:** P3 (unconfirmable). Flag for operator walk.

**Fix:** operator should plant a kind + screenshot the result;
compare against §2.2. The `bu_2b_cold_read.md` P3.1 entry notes
the post-plant banner exists but has no close/auto-dismiss, so
some version of this is shipped — but the rest is unverified.

---

### P3.8 — Sub-nav strip — UNCONFIRMABLE from screenshots; mockup marks it optional

**Mockup** (§5.3): sub-nav strip `⌂ Catalog | ⚡ Plant: drift
[L1] | 📺 Tour: drift [L1] | ← Landing`.

**Screenshots**: not present on any page.

**Severity:** P3. Mockup itself flagged "the strip is omitted
entirely on the landing (the landing IS the catalog)" and didn't
firmly require it. Probably-intentional omission.

**Fix:** ignore unless operator wants it.

---

## What's specified in mockups but UNCONFIRMABLE from screenshots

Operator walks needed for:

1. **Post-plant flash + state transition** (§2.2) — see P3.7.
   Need screenshot of `/training/plant/<kind>` AFTER a successful
   POST. Confirm: flash present, status pill flips to `● planted`,
   CTA renames to "Re-plant," "Remove plant" affordance exists.

2. **Reset in-flight UX** (§4.3-4.4) — button label changes to
   `▒ Resetting…`, success flash `✓ Reset to baseline — N
   plants removed (Xs)`, browser-tab pulse `(✓) Reset complete`
   for 5s. `02_landing_reset_banner.png` shows the AFTER-state
   banner ("Clean baseline. Demo DB wiped...") — matches §4.4's
   shape. The IN-FLIGHT state isn't captured.

3. **Picker-omission "this L2 doesn't support this kind"**
   (§2.5) — the warning panel rendered when
   `default_scenario_for` omits the kind for the current L2. The
   plant screenshots all show successful picker rendering — no
   sample of the failure-mode panel.

4. **Toggle-disabled state during in-flight plant** (§3.6) —
   blocked by P1.3 (no toggle exists yet).

5. **Browser-tab pulse on plant/reset completion** (§3.6, §4.4)
   — `(✓) After ready` / `(✓) Reset complete` for 5s. Pure
   browser behavior, screenshot can't surface.

6. **`uncovered_*` destructive plant page** (§2.6.3) — orange
   "DESTRUCTIVE" chip + row-count preview + CTA copy emphasizing
   DELETE. `19_plant_uncovered_rail.png` shows the page exists
   but NO destructive warning panel + the CTA reads
   `⊕ Plant this scenario` not `⚡ Empty the rail + refresh →`.
   This is actually a P1-shape miss but I'm flagging at P3 here
   because the underlying op (DELETE) still happens and operator
   has the reset escape — but the warning was a real protective
   affordance the mockup specified.

---

## What's IMPLEMENTED beyond / differently from the mockups

Deltas in the OTHER direction — on screen, NOT in mockup. Mostly
improvements; flag so the operator knows what shifted in flight.

1. **"What to do about it" rendered as a SEPARATE CARD below the
   form.** Mockup had it as `**Action.**` paragraph inside the
   form. Card framing is an upgrade — cleaner separation between
   "what the form does" and "what to do when this fires for
   real." Every plant screenshot.

2. **"Re-baseline" explanatory card + reset button at page
   bottom.** Mockup just had the header reset button; the in-page
   restate ("Going to plant a different scenario? Reset first")
   is guidance for naive operators who'd otherwise stack plants.

3. **Empty-primitive "no operator-tunable parameters" hint**
   (`19_plant_uncovered_rail.png`, `12_plant_missing_metadata_key.png`).
   BU.4 stage 3. Mockup implicitly assumed all plants have params.

4. **`kind_qualifier` rendering** — `17_plant_xor_group_missed.png`
   title reads `Multi-mode template variant XOR violation —
   Missed-firing variant`. BU.4 stage 2. Fixes bu_2b_cold_read.md
   P1.4.

5. **L1 Audit `supersession_audit` page renders diagnostic-surface
   body copy** (`18_plant_supersession_audit.png`). Mockup didn't
   spec an audit page; canonical shell handled it cleanly.

---

## Recommendation

**Don't call BU "done" yet.** The three P1s sit on the operator's
primary loop (landing → plant → tour) — all mockup-specified
features that didn't ship, not bugs in shipped code.

**Priority order for fix branches:**

1. **P1.3 (tour Before/After toggle).** Highest demo-value lift.
   Two new POST endpoints + iframe-reload pattern. Per-kind reset
   falls back to full reset under the hood (§7 Q3 decision).
2. **P1.1 (per-card landing CTAs + status pill + `[?]`).**
   Landing is the operator's first surface. Status pill needs the
   new `<prefix>_training_state` KV table; CTAs + `[?]` are
   template-only.
3. **P1.2 (plant page left rail).** Cheapest — pure template +
   registry iteration; ~2-3 hours.

**Then a P2 polish branch** rolling up the 10 P2s — most are
template / registry-row edits.

**Operator walks needed** for the UNCONFIRMABLE items above —
particularly post-plant flash (§2.2) + `uncovered_*` destructive
warning (§2.6.3). Implementation exists; visual confirmation
requires a plant action screenshots didn't capture.

The 21-entry registry surface holds — every primitive renders,
every kind has a working plant + tour route, reset works. The
shipped surface is a credible "v0.8 of the mockup." The
remaining 0.2 is the loop-completing UI (toggle, CTAs, left
rail) plus polish.
