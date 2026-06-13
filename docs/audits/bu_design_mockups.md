# BU design mockups

> **Status:** DRAFT 2026-05-30 (round-3 revision same day — adds
> L2FT Hygiene plants + collapses per-kind mockups around the Lock
> 7 shared registry). Agent design pass per
> `[[feedback_agent_driven_design_works]]`. Briefed against
> `docs/audits/_archive/bu_0_replan.md` (locks 0-7 + §0.5 matrix) +
> `SPEC.md`'s Phase BU section + the existing `_studio_training.py`
> pane source + `common/l2/demo_etl_gaps.py` + `common/l2/seed.py`
> Plant dataclasses + `src/recon_gen/docs/L2FT_Exceptions.md`.
> Drives BU.1-BU.5 implementation.
>
> **Round-2 diff summary:** §1 landing grid expands 12→17 cards
> with new L2 family grouping; §2 plant page adds mockups for the 5
> new L2 kinds whose primitive shapes differ from L1; §3 tour page
> splits into L1 (dashboard iframe) and L2 (`/etl/triage` or
> `/etl/run` Coverage iframe) variants; §5 sub-nav reflects L1/L2
> grouping; §7 gains 5 new open questions specifically about the
> L1+L2 union. §4 (reset) and §6 (`/data` pane removal) unchanged.
>
> **Round-3 diff summary:** §1 landing expands 17→21 cards, adds
> L2FT Hygiene family (8 families total); §2 collapses the per-
> kind plant-page mockups into ONE canonical mockup of
> `_render_plant_page(entry)` + a 4-row primitive-renderer table;
> §3 collapses tour-page mockups into ONE canonical mockup + a
> per-category iframe-destination table (L1 / L2 Triage / L2
> Coverage / L2FT Hygiene); §5 sub-nav driven by registry; §7
> swept — questions that the Lock 7 registry locks the answer to
> are collapsed; new §8 lists the BU.X needs-build cells (5 plant
> primitives that don't exist yet — 1 L1 + 4 L2FT Hygiene). Net
> doc size shrinks slightly — the per-kind variants in §2 and §3
> stop being canonical (the registry derives them).
> Each updated section leads with `**Updated 2026-05-30 round-3
> scope:**` for grep.
>
> **Round-4 diff summary:** §0 locks recap grows 7→11 (Locks
> 8-11 from `bu_0_replan.md`: thin-index registry, anti-drift
> tests, L2TriageGapSection typed source, docs generation); §2
> canonical mockup updated to show display strings as
> `{section.title}` / `{section.short_statement}` /
> `{section.what_to_do}` template variables resolved from the
> typed section at render time (NOT hardcoded in the registry);
> §7 swept again — Q16 collapses (TourDestination.secondary_links
> + L1 InvariantSection sharing answers it), Q18 collapses
> (L2FTExceptionSection contract holds the anchor IDs), Q19
> moves to §8.4 cell description (BU.3.4 implementation detail
> for `dead_metadata`), Q20 stays; §8 each cell grows a "Typed
> source" bullet naming the InvariantSection /
> L2FTExceptionSection / L2TriageGapSection key the registry
> entry indexes into. Net flat — Lock 8's display-string move
> shrinks the registry surface; new section refs grow it back.

---

## 0. Headline + lock reminders

**Updated 2026-05-30 round-3 scope:** Trainer covers EVERY
violation kind surfaced on EITHER the L1 dashboard exception
sheets OR the L2 Flow Tracing L2 Hygiene Exceptions sheet OR
/etl/triage OR /etl/run Coverage (21 kinds across 8 families per
the §0.5 matrix in `bu_0_replan.md`). All UI is data-driven from
the Lock 7 registry — one canonical render path per shape, no
per-kind HTML.

Phase BU stands up the Training mode at `/training/` — the third
authoring surface alongside `/` (L2 Editor) and `/etl/` (ETL
Support). Top-nav already advertises the entry but no route handler
exists, so the link 404s today. The Trainer is built around ONE
shared registry (Lock 7) that maps the 21 violation kinds from
the `bu_0_replan.md` §0.5 matrix to operator-controlled metadata
(form primitives, plant function, tour destination). The UI is a
thin shell — landing renders the registry grouped by family;
plant page renders one entry; tour page renders one entry's
iframe destination. Adding a new violation kind = adding ONE
registry row + (when needed) ONE plant function.

A Trainer's loop after BU lands: land on `/training/` → scan the
21-card grid (8 families across L1 and L2 groups) → pick one kind
→ land on its plant page (form is data-driven from the registry
entry's primitive list) → tweak (or accept) defaults →
`[ Plant + refresh → ]` → review on the embedded iframe
(destination from the entry's `tour_destination` field —
dashboard sheet for L1, /etl/triage for L2 Triage, /etl/run
Coverage for L2 Coverage, L2FT L2 Hygiene Exceptions sheet for
L2FT Hygiene) via `[ Take the tour → ]` → toggle `Before` /
`After` → `[ Reset to baseline ]` when done.

### Locks recap

| #  | Lock                            | Shape                                                                                         | Section here       |
|----|---------------------------------|-----------------------------------------------------------------------------------------------|--------------------|
| 0  | Scope — L1 + L2 + L2FT union    | Trainer covers 15 L1 + 5 L2 ETL-feed + 4 L2FT Hygiene = 21 kinds per §0.5 matrix              | §1 + throughout    |
| 1  | Landing pattern                 | Registry-driven accordion grid (8 families × 21 kinds, L1/L2 visually grouped)                | §1                 |
| 2  | Per-kind plant page             | One canonical `_render_plant_page(entry)` shell; form fields render per `PrimitiveField` type | §2                 |
| 3  | Tour-mode UX                    | One canonical `_render_tour_page(entry)` shell; iframe URL from `entry.tour_destination`     | §3                 |
| 4  | Reset semantics                 | Destructive truncate+reseed (same code path as `/etl/run` Refresh); no confirm modal           | §4 + on every page |
| 5  | Subsume `/data` right-column    | Remove the `_studio_training` pane from `/data`; full migration to `/training/`                | §6                 |
| 6  | L2 plant cleanup parity         | L2 plants ride the same truncate-and-reseed reset as L1, despite different integration point   | §4 caption         |
| 7  | Shared plant registry           | ONE registry module drives ALL per-kind UI; adding a kind = 1 row + 0 new UI files            | §1, §2, §3, §5, §8 |
| 8  | Registry = thin index           | `PlantKindEntry` references typed sections by `kind`; display strings live on InvariantSection / L2FTExceptionSection / L2TriageGapSection | §2 + §8     |
| 9  | Anti-drift = parameterized test contract | 5 tests parameterize over PLANT_REGISTRY: bijectivity, tour-URL liveness, plant→matview, primitive-kwarg coverage, docs-freshness | §7 + §8 |
| 10 | L2TriageGapSection build        | New `common/handbook/l2_triage_gaps.py` + `docs/L2_Triage_Gaps.md` parallel to L1 / L2FT pattern; migrates `_GAP_KIND_LABELS` away | §8 (BU.2a)    |
| 11 | Docs generation = registry walk | `recon-gen docs export` consumes PLANT_REGISTRY + section catalogues; emits per-kind handbook + §0.5 matrix + disclosure banner | §8 (BU.5) |

Full lock rationale + rejected variants + §0.5 coverage matrix
(with `Violation class source (SoT)` column added round-4):
`docs/audits/_archive/bu_0_replan.md`.

---

## 1. `/training/` landing page (BU.4)

**Updated 2026-05-30 round-3 scope:** grid expands 17→21 cards;
families expand 7→8 with new `L2FT Hygiene` family containing the
4 round-3 needs-build plants (`chain_orphan`,
`dead_bundles_activity`, `dead_metadata`, `dead_limit_schedule`).
Card chrome, accordion chrome, status pill, CTAs all data-drive
off the Lock 7 registry — no per-kind HTML. The landing is the
canonical instance of "iterate the registry grouped by family";
the same iteration shape powers §5's sub-nav strip.

**Consumes:** Lock 0 (scope union) + Lock 1 (accordion grid) +
Lock 4 (reset button) + Lock 5 (the chrome is the migration target
for `_studio_training`'s existing content) + Lock 7 (registry
iteration drives the family + card rendering).
**Before:** N/A — the route currently 404s. The closest existing
surface is the right-column pane on `/data` (see §6 for what that
pane looks like today + what it becomes after BU).

### 1.1 Before — current state (route 404s, content is on `/data`)

```
┌──────────────────────────────────────────────────────────────────────┐
│ Recon-Gen │ L2 Editor │ ETL Support │ Training │ L1 Dashboard │ ... │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│                   404 — route not found: /training/                  │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

Operator's mental model from the existing `/data` pane: "I see the
catalog but I can't actually do anything from here — the deep-links
just open the dashboard sheet, no plant happens." BU.4's landing
replaces both the dead route AND the read-only pane.

### 1.2 After — `/training/` landing, all families collapsed

```
┌──────────────────────────────────────────────────────────────────────┐
│ Recon-Gen │ L2 Editor │ ETL Support │ Training [●] │ L1 Dashboard│  │
├──────────────────────────────────────────────────────────────────────┤
│ Studio · Training                  qsgen-sqlite   [ ↻ Reset to base ]│
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│ Plant controlled violations into the demo DB, then walk an End User  │
│ through how each one surfaces on the dashboards or ETL pages.        │
│ Each card describes one violation kind; click "Plant this" to        │
│ overlay it, or "Take the tour" for the guided before/after.          │
│                                                                      │
│ 21 violation kinds (15 L1 + 5 L2 ETL-feed + 4 L2FT Hygiene) ·        │
│ 0 planted this session                                                │
│                                                                      │
│ ┏━ L1 INVARIANT VIOLATIONS (15 kinds) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓ │
│ ┃ Lands on L1 Dashboard sheets. Money-movement integrity checks.   ┃ │
│ ┃                                                                  ┃ │
│ ┃ ┌──────────────────────────────────────────────────────────────┐ ┃ │
│ ┃ │ ▶ L1 Conservation (3 kinds · 0 planted)                      │ ┃ │
│ ┃ └──────────────────────────────────────────────────────────────┘ ┃ │
│ ┃ ┌──────────────────────────────────────────────────────────────┐ ┃ │
│ ┃ │ ▶ L1 Cap (3 kinds · 0 planted)                               │ ┃ │
│ ┃ └──────────────────────────────────────────────────────────────┘ ┃ │
│ ┃ ┌──────────────────────────────────────────────────────────────┐ ┃ │
│ ┃ │ ▶ L1 Aging (2 kinds · 0 planted)                             │ ┃ │
│ ┃ └──────────────────────────────────────────────────────────────┘ ┃ │
│ ┃ ┌──────────────────────────────────────────────────────────────┐ ┃ │
│ ┃ │ ▶ L1 Chain coherence (6 kinds · 0 planted)                   │ ┃ │
│ ┃ └──────────────────────────────────────────────────────────────┘ ┃ │
│ ┃ ┌──────────────────────────────────────────────────────────────┐ ┃ │
│ ┃ │ ▶ L1 Audit (1 kind)                                          │ ┃ │
│ ┃ └──────────────────────────────────────────────────────────────┘ ┃ │
│ ┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛ │
│                                                                      │
│ ┏━ L2 FEED-CONTRACT + L2FT HYGIENE (9 kinds) ━━━━━━━━━━━━━━━━━━━━━┓ │
│ ┃ Lands on /etl/triage, /etl/run Coverage, OR L2FT L2 Hygiene      ┃ │
│ ┃ Exceptions sheet. Feed-shape + L2-runtime-correspondence checks. ┃ │
│ ┃                                                                  ┃ │
│ ┃ ┌──────────────────────────────────────────────────────────────┐ ┃ │
│ ┃ │ ▶ L2 Triage (3 kinds · 0 planted)                            │ ┃ │
│ ┃ └──────────────────────────────────────────────────────────────┘ ┃ │
│ ┃ ┌──────────────────────────────────────────────────────────────┐ ┃ │
│ ┃ │ ▶ L2 Coverage (2 kinds · 0 planted)                          │ ┃ │
│ ┃ └──────────────────────────────────────────────────────────────┘ ┃ │
│ ┃ ┌──────────────────────────────────────────────────────────────┐ ┃ │
│ ┃ │ ▶ L2FT Hygiene (4 kinds · 0 planted)                         │ ┃ │
│ ┃ └──────────────────────────────────────────────────────────────┘ ┃ │
│ ┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛ │
│                                                                      │
│ Tip: plant several kinds at once, then take each tour to compare.    │
│ Reset to baseline clears every plant.                                │
└──────────────────────────────────────────────────────────────────────┘
```

L1 vs L2 visual distinction (rationale + alternatives in §7 Q1):
- L1 group has a cool-blue stripe (`bg-info-subtle`-equivalent
  token) on the group's outer border.
- L2 group has a warm-amber stripe (`bg-warning-subtle` token).
- Group header is a thin all-caps section header; not a separate
  accordion (the families inside are the accordions). Two clicks
  to open a family card: never one click on the group header.

Operator-facing strings (new):
- Page header right pin: `[ ↻ Reset to baseline ]` (Lock 4) — styled
  the same as `/etl/run`'s Refresh button (warning border, not
  primary accent — reads "destructive-but-frequent").
- Intro paragraph (exact text above) — one sentence each on the
  two actions per kind.
- Per-family accordion header: `▶ <Family name> (<N> kinds · <M>
  planted)`. Chevron flips to `▾` on expand. Planted-count is the
  prioritization signal — families with any plants float to the
  top within the page (sort order: planted-count DESC, then family
  name).
- Footer tip: one sentence, soft text. Surfaces the "you can stack
  plants" mental model without front-loading it.

### 1.3 After — one family expanded (Balance integrity)

```
│ ┌──────────────────────────────────────────────────────────────────┐ │
│ │ ▾ Balance integrity (3 kinds · 0 planted)                        │ │
│ │                                                                  │ │
│ │   ┌──────────────────────────────────────────────────────────┐   │ │
│ │   │ [drift] [?]                              ● not planted   │   │ │
│ │   │ Sub-ledger drift                                         │   │ │
│ │   │                                                          │   │ │
│ │   │ For every account on every day, the sum of signed_amount │   │ │
│ │   │ over the account's transactions should equal the         │   │ │
│ │   │ daily_balances.balance for that account+day.             │   │ │
│ │   │                                                          │   │ │
│ │   │ Action. When a row surfaces, the ETL has skipped a leg.  │   │ │
│ │   │ Reconcile the source feed against transactions; the      │   │ │
│ │   │ daily_balances row is authoritative.                     │   │ │
│ │   │                                                          │   │ │
│ │   │ [ Plant this → ]   [ Take the tour → ]                   │   │ │
│ │   └──────────────────────────────────────────────────────────┘   │ │
│ │   ┌──────────────────────────────────────────────────────────┐   │ │
│ │   │ [ledger_drift] [?]                       ● not planted   │   │ │
│ │   │ Sub-ledger drift (ledger side)                           │   │ │
│ │   │ ...                                                      │   │ │
│ │   │ [ Plant this → ]   [ Take the tour → ]                   │   │ │
│ │   └──────────────────────────────────────────────────────────┘   │ │
│ │   ┌──────────────────────────────────────────────────────────┐   │ │
│ │   │ [overdraft] [?]                          ● not planted   │   │ │
│ │   │ Negative balance on a non-overdraft-eligible account     │   │ │
│ │   │ ...                                                      │   │ │
│ │   │ [ Plant this → ]   [ Take the tour → ]                   │   │ │
│ │   └──────────────────────────────────────────────────────────┘   │ │
│ └──────────────────────────────────────────────────────────────────┘ │
```

### 1.3b After — L2 Triage gaps family expanded

```
│ ┃ ┌──────────────────────────────────────────────────────────────┐ ┃ │
│ ┃ │ ▾ Triage gaps (3 kinds · 0 planted)                          │ ┃ │
│ ┃ │                                                              │ ┃ │
│ ┃ │   ┌──────────────────────────────────────────────────────┐   │ ┃ │
│ ┃ │   │ [phantom_rail] [?]   L2     ● not planted            │   │ ┃ │
│ ┃ │   │ Phantom rail in feed                                 │   │ ┃ │
│ ┃ │   │                                                      │   │ ┃ │
│ ┃ │   │ ETL emits transactions tagged with a rail_name your  │   │ ┃ │
│ ┃ │   │ L2 doesn't declare. Triage surfaces these as an      │   │ ┃ │
│ ┃ │   │ "unmatched_rail" card so the integrator can either   │   │ ┃ │
│ ┃ │   │ declare the rail or fix the ETL tag.                 │   │ ┃ │
│ ┃ │   │                                                      │   │ ┃ │
│ ┃ │   │ Action. Decide: real rail (declare it) or wrong tag  │   │ ┃ │
│ ┃ │   │ (fix the ETL).                                       │   │ ┃ │
│ ┃ │   │                                                      │   │ ┃ │
│ ┃ │   │ [ Plant this → ]   [ Take the tour → ]               │   │ ┃ │
│ ┃ │   └──────────────────────────────────────────────────────┘   │ ┃ │
│ ┃ │   ┌──────────────────────────────────────────────────────┐   │ ┃ │
│ ┃ │   │ [phantom_template] [?]   L2     ● not planted        │   │ ┃ │
│ ┃ │   │ Phantom template in feed                             │   │ ┃ │
│ ┃ │   │ ...                                                  │   │ ┃ │
│ ┃ │   │ [ Plant this → ]   [ Take the tour → ]               │   │ ┃ │
│ ┃ │   └──────────────────────────────────────────────────────┘   │ ┃ │
│ ┃ │   ┌──────────────────────────────────────────────────────┐   │ ┃ │
│ ┃ │   │ [missing_metadata] [?]   L2     ● not planted        │   │ ┃ │
│ ┃ │   │ Required metadata key absent                         │   │ ┃ │
│ ┃ │   │ ...                                                  │   │ ┃ │
│ ┃ │   │ [ Plant this → ]   [ Take the tour → ]               │   │ ┃ │
│ ┃ │   └──────────────────────────────────────────────────────┘   │ ┃ │
│ ┃ └──────────────────────────────────────────────────────────────┘ ┃ │
```

Per-card additions for L2:
- An `L2` badge sits between the kind badge and the status pill.
  Distinct from the cool-blue family header so the operator can
  scan a card in isolation (deep-linked from a tour, e.g.) and
  immediately see the category.
- Tour destination is named in the per-card `[?]` glossary entry:
  "Tour destination: /etl/triage (unmatched_rail section)."
- Action prose differs from L1 — L2 actions are integrator-side
  (declare the rail / fix the tag / drop the key) rather than
  ETL-pipeline-side (reconcile the source feed).

The Coverage gaps family expands similarly (2 cards:
`[uncovered_rail]` + `[uncovered_template]`), with tour destination
`/etl/run` Coverage card per Lock 3's mapping table.

### 1.3c After — L2FT Hygiene family expanded (NEW round-3)

```
│ ┃ ┌──────────────────────────────────────────────────────────────┐ ┃ │
│ ┃ │ ▾ L2FT Hygiene (4 kinds · 0 planted)                         │ ┃ │
│ ┃ │                                                              │ ┃ │
│ ┃ │   ┌──────────────────────────────────────────────────────┐   │ ┃ │
│ ┃ │   │ [chain_orphan] [?]   L2FT     ⚠ needs-build (BU.3.2) │   │ ┃ │
│ ┃ │   │ Chain orphan (parent fires, no child)                │   │ ┃ │
│ ┃ │   │                                                      │   │ ┃ │
│ ┃ │   │ Each row is a declared Required chain edge           │   │ ┃ │
│ ┃ │   │ (parent → child) where the parent rail fired in the  │   │ ┃ │
│ ┃ │   │ window but no matched child firing followed.         │   │ ┃ │
│ ┃ │   │                                                      │   │ ┃ │
│ ┃ │   │ Action. Fix the ETL so the child fires when the      │   │ ┃ │
│ ┃ │   │ parent does, or retire the chain edge from the L2.   │   │ ┃ │
│ ┃ │   │                                                      │   │ ┃ │
│ ┃ │   │ [ Plant (placeholder) ]   [ Tour without plant → ]   │   │ ┃ │
│ ┃ │   └──────────────────────────────────────────────────────┘   │ ┃ │
│ ┃ │   ┌──────────────────────────────────────────────────────┐   │ ┃ │
│ ┃ │   │ [dead_bundles_activity] [?]   L2FT   ⚠ needs-build   │   │ ┃ │
│ ┃ │   │ Dead bundles_activity declaration                    │   │ ┃ │
│ ┃ │   │ ...                                                  │   │ ┃ │
│ ┃ │   │ [ Plant (placeholder) ]   [ Tour without plant → ]   │   │ ┃ │
│ ┃ │   └──────────────────────────────────────────────────────┘   │ ┃ │
│ ┃ │   ┌──────────────────────────────────────────────────────┐   │ ┃ │
│ ┃ │   │ [dead_metadata] [?]   L2FT          ⚠ needs-build    │   │ ┃ │
│ ┃ │   │ Dead metadata key declaration                        │   │ ┃ │
│ ┃ │   │ Note: opposite direction from L2 Triage's            │   │ ┃ │
│ ┃ │   │ missing_metadata_key. Here the L2 declares a key     │   │ ┃ │
│ ┃ │   │ no posting carries; there the ETL omitted a key the  │   │ ┃ │
│ ┃ │   │ L2 declares.                                         │   │ ┃ │
│ ┃ │   │ ...                                                  │   │ ┃ │
│ ┃ │   │ [ Plant (placeholder) ]   [ Tour without plant → ]   │   │ ┃ │
│ ┃ │   └──────────────────────────────────────────────────────┘   │ ┃ │
│ ┃ │   ┌──────────────────────────────────────────────────────┐   │ ┃ │
│ ┃ │   │ [dead_limit_schedule] [?]   L2FT    ⚠ needs-build    │   │ ┃ │
│ ┃ │   │ Dead LimitSchedule cell                              │   │ ┃ │
│ ┃ │   │ ...                                                  │   │ ┃ │
│ ┃ │   │ [ Plant (placeholder) ]   [ Tour without plant → ]   │   │ ┃ │
│ ┃ │   └──────────────────────────────────────────────────────┘   │ ┃ │
│ ┃ └──────────────────────────────────────────────────────────────┘ ┃ │
```

Per-card additions for L2FT Hygiene (BU.4 ships the cards; BU.3.2-
BU.3.5 fill in the plant primitives):
- `L2FT` badge — distinct from `L2` (Triage / Coverage) because the
  tour destination differs (L2FT Hygiene tours the L2 Flow Tracing
  L2 Hygiene Exceptions sheet, NOT /etl/triage).
- Status pill is `⚠ needs-build (BU.3.X)` until BU.3 lands the plant
  primitive. CTAs render as disabled `[ Plant (placeholder) ]` +
  enabled `[ Tour without plant → ]` (the tour iframe still works
  with no plant overlay — same fallback shape as §3.4 cold-start).
- Action prose is integrator-side (retire the declaration OR fix
  the ETL); same shape as the L2 Triage / Coverage cards.

### 1.4 After — landing with two plants active

```
│ 21 violation kinds · 2 planted this session                          │
│                                                                      │
│ ┌──────────────────────────────────────────────────────────────────┐ │
│ │ ▾ Balance integrity (3 kinds · 1 planted)                        │ │
│ │                                                                  │ │
│ │   ┌──────────────────────────────────────────────────────────┐   │ │
│ │   │ [drift] [?]                         ● planted 14:23      │   │ │
│ │   │ Sub-ledger drift                                         │   │ │
│ │   │ ...                                                      │   │ │
│ │   │ [ Re-plant → ]   [ Take the tour → ]   [ Remove plant ]  │   │ │
│ │   └──────────────────────────────────────────────────────────┘   │ │
│ │   ┌──────────────────────────────────────────────────────────┐   │ │
│ │   │ [ledger_drift] [?]                  ● not planted        │   │ │
│ │   │ ...                                                      │   │ │
│ │   │ [ Plant this → ]   [ Take the tour → ]                   │   │ │
│ │   └──────────────────────────────────────────────────────────┘   │ │
│ │   ┌──────────────────────────────────────────────────────────┐   │ │
│ │   │ [overdraft] [?]                     ● planted 14:25      │   │ │
│ │   │ ...                                                      │   │ │
│ │   │ [ Re-plant → ]   [ Take the tour → ]   [ Remove plant ]  │   │ │
│ │   └──────────────────────────────────────────────────────────┘   │ │
│ └──────────────────────────────────────────────────────────────────┘ │
```

Per-card status pill states:
- `● not planted` (muted-grey) — default; CTAs are `Plant this`
  + `Take the tour`.
- `● planted <hh:mm>` (success-green) — this session has applied
  the plant; CTAs flip to `Re-plant` (re-runs with current form
  defaults) + `Take the tour` + `Remove plant` (per-kind variant
  of Lock 4's reset, scoped to one kind — see §7 Q3 for whether
  this is feasible or should be cut).
- `⚠ planted but reset since` (warning-yellow) — the operator
  planted, then ran a reset, then visited the landing without
  re-planting. The pill warns the dashboard view won't reflect the
  plant.

Operator-facing strings (per card):
- Kind badge: bracketed lowercase identifier in mono font
  (`[drift]`, `[overdraft]`, …), matching the existing
  `_studio_training` pane's `<span class="data-training__kind">`.
- `[?]` next to the badge: opens the side-panel drawer (BTa.0 Lock
  1 reuse) with the full glossary entry for the kind — first
  paragraph of `L1_Invariants.md`'s section + the columns list.
- Card title: human-readable form (`InvariantSection.title`).
- Card body: SHOULD statement (one paragraph; reuses
  `short_statement` field).
- Action line: `**Action.** <what_to_do>` — matches existing pane.
- CTAs: `[ Plant this → ]` (links to `/training/plant/<kind>?from=
  /training/`) + `[ Take the tour → ]` (links to
  `/training/tour/<kind>?from=/training/`).
- Status pill colors per `[[project_qs_conditional_formatting]]`-
  style accessibility — color + shape (`●` / `⚠`) + text, never
  color-only.

### 1.5 Captions

- **Family taxonomy** (driven by the Lock 7 registry's `family`
  column — same taxonomy as `bu_0_replan.md` §0.5 matrix). The 21
  kinds group into 8 families across two top-level groups:
  - **L1 invariant violations** (5 families, 15 kinds):
    - `L1 Conservation`: `drift`, `ledger_drift`, `overdraft`
    - `L1 Cap`: `limit_breach_outbound`, `limit_breach_inbound`,
      `expected_eod_balance_breach`
    - `L1 Aging`: `stuck_pending`, `stuck_unbundled`
    - `L1 Chain coherence`: `chain_parent_disagreement`,
      `xor_group_missed`, `xor_group_overlap`,
      `fan_in_missing_parent`, `fan_in_extra_parent`,
      `multi_xor_missed`, `multi_xor_overlap`
    - `L1 Audit`: `supersession_audit`
  - **L2 feed-contract + L2FT Hygiene** (3 families, 9 kinds):
    - `L2 Triage` (surface /etl/triage): `phantom_rail`,
      `phantom_template`, `missing_metadata_key`
    - `L2 Coverage` (surface /etl/run Coverage cards):
      `uncovered_rail`, `uncovered_template`
    - `L2FT Hygiene` (surface L2 Flow Tracing app's L2 Hygiene
      Exceptions sheet): `chain_orphan`,
      `dead_bundles_activity`, `dead_metadata`,
      `dead_limit_schedule`
  The taxonomy IS the registry — adding a kind or moving one
  between families is one edit to one registry row. Categories
  drive the visual grouping per §1.2; family headers drive the
  accordions.
- **State source for status pills.** A new `<prefix>_training_state`
  KV row per (kind, deployment) tracks plant state:
  `{"plant_kind": "drift", "last_planted_at": "2026-05-30T14:23:13",
  "params_hash": "..."}`. Reset (Lock 4) deletes every row. Process
  restart loses the rows (they live in the demo DB, but the table
  is empty post-restart — see §7 Q5 for persistence question). The
  alternative: keep the table empty across restarts AND show the
  status pill as "● unknown" on first paint; not picked (most
  Trainer sessions are short, the state lives within one process
  lifetime).
- **Sort orders.** Families sort by `planted DESC, name ASC`.
  Within a family, kinds sort by `_DISPLAY_ORDER` index (the
  existing operator-attention order from the BS-era pane).
- **Empty / one-family states.** If only one family has any plants,
  it's the only one auto-expanded on first paint. If zero plants,
  all families collapsed (default-collapsed per Lock 1 + BTa.0
  Lock 3's reuse). If ALL kinds have plants, all families auto-
  expanded with a footer banner: `All 12 kinds planted — take the
  tour for each, or reset to start over.`

---

## 2. `/training/plant/<kind>` per-kind plant page (BU.2b)

**Updated 2026-05-30 round-4 scope:** display strings in the
canonical mockup (§2.1) are now annotated as `{section.title}` /
`{section.short_statement}` / `{section.what_to_do}` template
variables resolved from the typed section at render time, per
Lock 8. The rendered text in the ASCII boxes is what those
templates evaluate to for the drift entry, but the registry row
itself carries no strings beyond `kind` / `family` /
`plant_function` / `primitives` / `tour_destination` /
`dashboard_check`. The drift example's `{section}` resolves to
`load_bundled_invariants()["drift"]`.

**Updated 2026-05-30 round-3 scope:** collapsed from per-kind
variants into ONE canonical mockup of `_render_plant_page(entry)`
+ a 4-row primitive-renderer table (§2.4). The §2.1 mockup
(drift) remains the canonical instance — every other kind renders
THE SAME shape with different field set + different
`plant_function` adapter. §2.6's per-L2-kind mockups stay as
*examples of the template applied to non-L1 entries* (shows the
shell works across categories), not as definitional per-kind
designs.

**Consumes:** Lock 0 (scope union) + Lock 2 (one canonical page
shell) + Lock 4 (reset button stays in the header) + Lock 7
(`_render_plant_page(entry)` walks the registry entry's
primitives) + Lock 8 (`resolve_section(entry)` at render-top;
display strings come from the typed section) + BTa.0 Lock 4
(`?from=` back-breadcrumb).
**Before:** N/A — this is a new surface.

### 2.1 After — `/training/plant/drift` (the canonical mockup; every other kind follows this shell)

**Round-4 render-time resolution.** The display strings below are
NOT hardcoded in the registry. The renderer calls
`section = resolve_section(entry)` at the top and substitutes:

- `[<kind>]` → `entry.kind` (the canonical machine name —
  registry).
- `Plant: <Human title>` → `Plant: {section.title}` — from
  `InvariantSection["drift"].title` for L1 / `L2FTExceptionSection`
  for L2FT Hygiene / `L2TriageGapSection` for L2 Triage + L2
  Coverage.
- Quote blockquote → `{section.short_statement}` (renders as
  `> ...`) — empty / omitted when the section has no SHOULD
  (L2FT + L2 Triage don't carry blockquote SHOULDs).
- `**Action.** ...` paragraph in §2.2 post-plant flash →
  `**Action.** {section.what_to_do}`.

Edit `L1_Invariants.md` / `L2FT_Exceptions.md` /
`L2_Triage_Gaps.md` and every consuming surface (this page, the
landing card, the trainer pane, the generated docs from Lock 11)
updates from one source. Anti-drift gate in Lock 9 Test 1 catches
a registry-row `kind` that doesn't resolve.

```
┌──────────────────────────────────────────────────────────────────────┐
│ Recon-Gen │ L2 Editor │ ETL Support │ Training [●] │ ...             │
├──────────────────────────────────────────────────────────────────────┤
│ Studio · Training · Plant         qsgen-sqlite   [ ↻ Reset to base ] │
├──────────────────────────────────────────────────────────────────────┤
│ ← Back to Training                                                   │  ← sticky bar
├──────────────────────────────────────────────────────────────────────┤
│ [drift]  Plant: Sub-ledger drift                                     │
│                                                                      │
│ > For every account on every day, the sum of signed_amount over the  │
│ > account's transactions should equal the daily_balances.balance     │
│ > for that account+day.                                              │
│                                                                      │
│ ┌──────────────┬───────────────────────────────────────────────────┐ │
│ │ Other kinds  │ Form (defaults from default_scenario_for)         │ │
│ ├──────────────┼───────────────────────────────────────────────────┤ │
│ │ Balance      │                                                   │ │
│ │ ▎ drift  ●   │ Account                                           │ │
│ │   ledger_dft │ ┌─────────────────────────────────────┐ [?]       │ │
│ │   overdraft  │ │ cust-001 (Customer 1)            ▼ │           │ │
│ │              │ └─────────────────────────────────────┘           │ │
│ │ Policy       │  Picked by default_scenario_for: first template   │ │
│ │   limit_brch │  instance materialized from the L2's first        │ │
│ │   eod_brch   │  AccountTemplate.                                  │ │
│ │              │                                                   │ │
│ │ Aging        │ Days ago                                          │ │
│ │   pending    │ ┌─────┐                                            │ │
│ │   unbundled  │ │  5  │  (1 = yesterday, 30 = a month back)        │ │
│ │              │ └─────┘                                            │ │
│ │ Chain        │                                                   │ │
│ │   chain_pd   │ Delta money                                       │ │
│ │   xor_grp    │ ┌──────────┐                                       │ │
│ │   fan_in_d   │ │   75.00  │  USD; the artificial gap between      │ │
│ │   multi_xor  │ └──────────┘  txns and the daily balance.          │ │
│ │              │                                                   │ │
│ │ Diagnostic   │ Rail                                              │ │
│ │   supr_audit │ ┌─────────────────────────────────────┐ [?]       │ │
│ │              │ │ ACHCredit                        ▼ │           │ │
│ │              │ └─────────────────────────────────────┘           │ │
│ │              │  Picked: first 2-leg Rail whose destination_role  │ │
│ │              │  matches the template's role.                     │ │
│ │              │                                                   │ │
│ │              │ Counter account (external)                        │ │
│ │              │ ┌─────────────────────────────────────┐           │ │
│ │              │ │ ext-corr-bank-001                ▼ │           │ │
│ │              │ └─────────────────────────────────────┘           │ │
│ │              │                                                   │ │
│ │              │ ▸ Show defaults' reasoning (4 picks)              │ │
│ │              │                                                   │ │
│ │              │ ┌─────────────────────────┐                       │ │
│ │              │ │  ⚡ Plant + refresh  →  │                       │ │
│ │              │ └─────────────────────────┘                       │ │
│ │              │  ~10s · refreshes matviews so the dashboard       │ │
│ │              │  immediately reflects the plant                   │ │
│ │              │                                                   │ │
│ │              │ [ Take the tour with these settings → ]           │ │
│ │              │                                                   │ │
│ └──────────────┴───────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
```

Operator-facing strings:
- Sticky breadcrumb: `← Back to Training` (Lock 4 of BTa.0; carried
  via `?from=/training/`). On the plant page reached from a tour,
  the breadcrumb reads `← Back to tour: <kind>` (carried via
  `?from=/training/tour/<kind>`).
- Page title: `[<kind>] Plant: <Human title>`. Quote block
  underneath = the SHOULD statement (renders the
  `InvariantSection.short_statement`).
- Left rail: 12-kind list grouped by family (mirrors §1's
  taxonomy). Current kind highlighted with a `▎` left-edge stripe
  and a `●` after the label. Click switches to another kind's
  plant page; the URL pushes (browser back returns to current).
- Form labels: lowercase, sentence-case captions, `[?]` icon
  inline for fields the operator might not recognize (Account,
  Rail). `[?]` opens the side-panel drawer with the relevant
  glossary entry.
- Per-field "Picked by default_scenario_for: ..." caption — the
  picker's heuristic, plain-prose explained. Sources from
  introspection of `_pick_template`, `_pick_inbound_2leg_rail`,
  etc. in `common/l2/auto_scenario.py` (each picker function gets
  a `__doc__` string the form can render).
- `▸ Show defaults' reasoning (4 picks)` details block — expands
  to a numbered list of all picker decisions in order, with the
  alternatives that were considered + the tiebreaker rule. Same
  shape as `bta_design_mockups.md` §3.3's "What does the L2
  declare?" pattern.
- Primary CTA: `[ ⚡ Plant + refresh → ]` (lightning glyph
  reinforces "this changes data"). The form submits as POST to
  `/training/plant/<kind>` with the field values; server runs
  `filter_scenario_plants(default_scenario_for(...).scenario,
  kinds=(<kind>,))` with the operator's overrides spliced into the
  picked plant, then emits the seed overlay + refreshes matviews.
- Caption under primary CTA: `~10s · refreshes matviews so the
  dashboard immediately reflects the plant` — sets duration
  expectation (matches `/etl/run` Refresh) + names the matview
  refresh so the operator understands why the dashboard sheet has
  fresh data.
- Secondary CTA: `[ Take the tour with these settings → ]` —
  serialize form values into the URL fragment, navigate to
  `/training/tour/<kind>?<form params>&from=/training/plant/<kind>`.
  Tour page then auto-plants on `Before→After` toggle using the
  serialized settings.

### 2.2 After — post-plant flash + state

```
│ Studio · Training · Plant         qsgen-sqlite   [ ↻ Reset to base ] │
├──────────────────────────────────────────────────────────────────────┤
│ ← Back to Training                                                   │
├──────────────────────────────────────────────────────────────────────┤
│  ┌────────────────────────────────────────────────────────────┬───┐  │
│  │ ✓ Planted at 14:23:13 — 1 drift row + matview refresh      │ ✕ │  │
│  │   View on L1 Dashboard ▸                                   └───┤  │
│  │   (browser-tab bell rang)                                      │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                      │
│ [drift]  Plant: Sub-ledger drift                          ● planted   │
│ ...                                                                  │
│                                                                      │
│ ┌──────────────┬───────────────────────────────────────────────────┐ │
│ │ Other kinds  │ Form (defaults from default_scenario_for)         │ │
│ │ ...          │                                                   │ │
│ │              │ Account: cust-001 (Customer 1)                    │ │
│ │              │ Days ago: 5                                       │ │
│ │              │ Delta money: 75.00                                │ │
│ │              │ Rail: ACHCredit                                   │ │
│ │              │ Counter account: ext-corr-bank-001                │ │
│ │              │                                                   │ │
│ │              │ ┌─────────────────────────┐                       │ │
│ │              │ │  ⚡ Re-plant       →    │   ← form values stick │ │
│ │              │ └─────────────────────────┘     ; user can edit + │ │
│ │              │                                  re-plant         │ │
│ │              │ [ Take the tour with these settings → ]           │ │
│ │              │                                                   │ │
│ │              │ [ Remove plant ]   (clears this kind only —       │ │
│ │              │                    other plants survive)          │ │
│ │              │                                                   │ │
│ └──────────────┴───────────────────────────────────────────────────┘ │
```

Operator-facing strings:
- Success flash: `✓ Planted at <hh:mm:ss> — <N rows> + matview
  refresh`. Auto-dismisses after 10s. Reuses BTa.6 flash pattern.
- Flash CTA: `View on L1 Dashboard ▸` — links to
  `/dashboards/l1_dashboard/sheets/<sheet_id>` (the same App2 sheet
  the trainer pane already deep-links to). Opens in new tab so the
  operator can keep the plant page open for tweaking.
- Page title gets `● planted` pill appended after the form is
  saved (state read from `<prefix>_training_state`).
- Primary CTA renames to `[ ⚡ Re-plant → ]` once plant exists.
- New `[ Remove plant ]` secondary action — per-kind undo (see
  §7 Q3 — open question whether this is implementable as more
  than a "no-op + just run full reset under the hood").

### 2.3 After — a not-yet-implemented kind (BU.3 backlog)

```
│ Studio · Training · Plant         qsgen-sqlite   [ ↻ Reset to base ] │
├──────────────────────────────────────────────────────────────────────┤
│ ← Back to Training                                                   │
├──────────────────────────────────────────────────────────────────────┤
│ [expected_eod_balance_breach]  Plant: EOD balance breach             │
│                                                                      │
│ > For every account with an expected_eod_balance declared, the       │
│ > actual end-of-day balance should match within tolerance.           │
│                                                                      │
│ ┌──────────────┬───────────────────────────────────────────────────┐ │
│ │ Other kinds  │                                                   │ │
│ │ ...          │  Plant primitive not yet implemented.             │ │
│ │              │                                                   │ │
│ │ Policy       │  Tracked under BU.3. The L2 invariant matview     │ │
│ │   limit_brch │  for this kind exists                             │ │
│ │ ▎ eod_brch ● │  (`<prefix>_inv_expected_eod_balance_breach`)     │ │
│ │              │  but no `ScenarioPlant` field carries the plant   │ │
│ │              │  primitive yet.                                   │ │
│ │              │                                                   │ │
│ │              │  → See BU.1 plant inventory                       │ │
│ │              │  → Track BU.3 progress                            │ │
│ │              │                                                   │ │
│ │              │  When this lands, the form here will mirror the   │ │
│ │              │  shape of /training/plant/limit_breach.           │ │
│ │              │                                                   │ │
│ │              │ [ Take the tour anyway → ]                        │ │
│ │              │   (tour shows the dashboard sheet; "After" state  │ │
│ │              │   will be empty until the plant ships.)           │ │
│ │              │                                                   │ │
│ └──────────────┴───────────────────────────────────────────────────┘ │
```

Operator-facing strings:
- "Plant primitive not yet implemented" panel — surfaces BU.1's
  catalog finding inline; cites the audit doc + BU.3 task. No
  hand-wave or 500.
- `[ Take the tour anyway → ]` — the tour page still works (the
  dashboard sheet exists; "After" just shows the same content as
  "Before" with a warning banner). Doesn't dead-end the operator.

### 2.4 Captions — per-primitive render helpers (Lock 7 derived)

**Updated 2026-05-30 round-3 scope:** the per-kind form-shapes
table from round-2 is now the §0.5 violation coverage matrix in
`bu_0_replan.md` (canonical source). The form for any kind
derives from its `PlantKindEntry.primitives` tuple — one
`PrimitiveField` per kwarg of the entry's `plant_function`. Per-
primitive render helpers below are the entire UI codebase for the
form body (one helper per primitive subclass; no per-kind
branching).

| `PrimitiveField` subtype  | HTML rendered                                                                                                | Help-caption source                                              |
|---------------------------|--------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------|
| `PrimitiveStringField`    | `<input type="text" name="<name>" value="<default_picker(l2)>"> <span class="help">{help_text}</span>`       | `default_picker.__doc__` first line                              |
| `PrimitiveIntField`       | `<input type="number" name="<name>" value="..." min="<min_value>" max="<max_value>">` + help span             | same                                                             |
| `PrimitiveDecimalField`   | `<input type="number" step="0.01" name="<name>" value="..."> <span class="help">{help_text}</span>`          | same                                                             |
| `PrimitiveDropdownField`  | `<select name="<name>">{<option ... selected>...</option> for v, label in options_source(l2)}</select>`     | same; options dynamically sourced from L2                        |

**That's the entire form-rendering surface.** A new violation
kind whose plant function takes (e.g.) `(account_id: str,
days_ago: int, multiplier: Decimal)` gets a form composed of:
- `PrimitiveDropdownField(name='account_id', options_source=_list_internal_accounts, ...)`
- `PrimitiveIntField(name='days_ago', default_picker=lambda l2: 5, ...)`
- `PrimitiveDecimalField(name='multiplier', default_picker=lambda l2: Decimal("2.0"), ...)`

No new HTML, no new CSS, no new controller. The entry's
`primitives` tuple drives the page.

**Plant variants (xor_group, fan_in, multi_xor):** the registry
treats each variant as its own kind (`xor_group_missed` +
`xor_group_overlap` are two entries, not one entry with a sub-tab
toggle). Two reasons: (1) operator's mental model is "I want to
demo the missed-firing case" or "I want to demo the overlap case"
— different demos; (2) keeps the registry shape uniform — one
entry = one form = one tour. No per-kind sub-tab UI to maintain.
Round-2's `[ Missed firing | Overlap firing ]` sub-tab is rejected
on registry-uniformity grounds.

### 2.5 Caption — handling of "picker omitted this kind for this L2"

`default_scenario_for` returns `(scenario, omitted)` — some plant
kinds can't be derived from arbitrary L2s ("no Chain declares
fan_in=True with a known Rail or Template parent (AB.4)"). When
the operator visits `/training/plant/<kind>` on an L2 where the
picker omitted the kind:

```
│ ┌──────────────┬───────────────────────────────────────────────────┐ │
│ │ Other kinds  │                                                   │ │
│ │ ...          │  This L2 doesn't support this plant kind.         │ │
│ │              │                                                   │ │
│ │ Chain        │  Reason: no Chain declares fan_in=True with a     │ │
│ │   chain_pd   │  known Rail or Template parent (AB.4).            │ │
│ │   xor_grp    │                                                   │ │
│ │ ▎ fan_in_d ⚠ │  To exercise this kind, edit the L2 to declare    │ │
│ │   multi_xor  │  a Chain with fan_in=true. The L2 editor at /     │ │
│ │              │  has a Chain block.                                │ │
│ │              │                                                   │ │
│ │              │  → Open L2 editor (Chains block)                   │ │
│ │              │  → See full omission report                        │ │
│ │              │                                                   │ │
│ └──────────────┴───────────────────────────────────────────────────┘ │
```

Operator-facing strings: the omission reason from
`default_scenario_for`'s `omitted` tuple is rendered verbatim; a
`→ Open L2 editor (<block>)` deep-link routes back into the
Integrator surface to add the missing declaration. The kind's
landing card carries a `⚠ unsupported by this L2` status pill
instead of `● not planted`.

### 2.6 After — the canonical template applied to non-L1 entries

**Updated 2026-05-30 round-3 scope:** these aren't bespoke
per-kind page designs — they're examples of `_render_plant_page(entry)`
rendering a registry entry whose `primitives` tuple is shorter or
contains different field types. Same shell as §2.1; only the form
body content differs (per the §2.4 primitive-renderer table). Two
non-L1 examples below (L2 Triage simple form + L2 Coverage
destructive form) prove the shell handles non-L1 entries; the
4 L2FT Hygiene plant pages render the same way (no separate
mockup needed once BU.3 lands their `plant_function` + entries).

#### 2.6.1 `/training/plant/phantom_rail` — example: simple count + string form

```
┌──────────────────────────────────────────────────────────────────────┐
│ Recon-Gen │ L2 Editor │ ETL Support │ Training [●] │ ...             │
├──────────────────────────────────────────────────────────────────────┤
│ Studio · Training · Plant         qsgen-sqlite   [ ↻ Reset to base ] │
├──────────────────────────────────────────────────────────────────────┤
│ ← Back to Training                                                   │
├──────────────────────────────────────────────────────────────────────┤
│ [phantom_rail]  L2     Plant: Phantom rail in feed                   │
│                                                                      │
│ > Triage's unmatched_rail detector flags rows whose rail_name        │
│ > doesn't resolve to any L2-declared Rail. Plant fakes a feed where  │
│ > the ETL emitted a rail name your L2 doesn't know about.            │
│                                                                      │
│ ┌──────────────┬───────────────────────────────────────────────────┐ │
│ │ Other kinds  │ Form (defaults from demo_etl_gaps.py constants)   │ │
│ ├──────────────┼───────────────────────────────────────────────────┤ │
│ │ L1           │                                                   │ │
│ │ Balance      │ Number of phantom transactions to insert          │ │
│ │   drift      │ ┌─────┐                                            │ │
│ │   ledger_dft │ │  3  │  Triage's volume badge shows this count.   │ │
│ │   overdraft  │ └─────┘                                            │ │
│ │ Policy       │                                                   │ │
│ │   limit_brch │ Phantom rail_name (the value Triage flags)        │ │
│ │   eod_brch   │ ┌──────────────────────────────┐                  │ │
│ │ Aging        │ │ legacy_card_swipe            │                  │ │
│ │ ...          │ └──────────────────────────────┘                  │ │
│ │              │  Default reads like a plausible legacy rail an    │ │
│ │              │  integrator might forget to declare — not a       │ │
│ │              │  random sentinel.                                 │ │
│ │ L2           │                                                   │ │
│ │ Triage gaps  │ ▸ Show defaults' reasoning (2 picks)              │ │
│ │ ▎ phant_rail ●│                                                  │ │
│ │   phant_tmpl │ ┌─────────────────────────┐                       │ │
│ │   missing_md │ │  ⚡ Plant + refresh  →  │                       │ │
│ │ Coverage gaps│ └─────────────────────────┘                       │ │
│ │   uncov_rail │  ~10s · refreshes matviews + the /etl/triage      │ │
│ │   uncov_tmpl │  detector picks up the new rows                   │ │
│ │              │                                                   │ │
│ │              │ [ Take the tour with these settings → ]           │ │
│ │              │                                                   │ │
│ └──────────────┴───────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
```

`phantom_template` is structurally identical — `count` (default 2)
+ `template_name` (default `orphan_settlement_batch`). Same form
chrome; only the labels swap.

#### 2.6.2 `/training/plant/missing_metadata` (template dropdown)

```
│ [missing_metadata]  L2     Plant: Required metadata key absent       │
│                                                                      │
│ > A row tagged with a real template arrives without that template's  │
│ > declared transfer_key. Triage's missing_metadata_key detector      │
│ > flags it as "12 of 14 rows have it" partial coverage.              │
│                                                                      │
│ ┌──────────────┬───────────────────────────────────────────────────┐ │
│ │ ... [rail]   │ Target template (must declare a required          │ │
│ │              │ transfer_key)                                     │ │
│ │              │ ┌─────────────────────────────────────┐ [?]       │ │
│ │              │ │ payment_initiated (transfer_key:    │           │ │
│ │              │ │  customer_reference)              ▼ │           │ │
│ │              │ └─────────────────────────────────────┘           │ │
│ │              │  Picked: first template (sorted by name) that has │ │
│ │              │  a transfer_key declared.                         │ │
│ │              │                                                   │ │
│ │              │ Key to omit from metadata                         │ │
│ │              │ ┌─────────────────────────────────────┐           │ │
│ │              │ │ ALL required keys (empty JSON {})  │           │ │
│ │              │ └─────────────────────────────────────┘           │ │
│ │              │  v1 ships only "omit all" — operator confirms in  │ │
│ │              │  §7 Q4 whether per-key omission is worth it.      │ │
│ │              │                                                   │ │
│ │              │ ▸ Show defaults' reasoning (2 picks)              │ │
│ │              │                                                   │ │
│ │              │ ┌─────────────────────────┐                       │ │
│ │              │ │  ⚡ Plant + refresh  →  │                       │ │
│ │              │ └─────────────────────────┘                       │ │
│ │              │                                                   │ │
│ └──────────────┴───────────────────────────────────────────────────┘ │
```

Empty-L2 fallback (no template declares a `transfer_key`): the
form body shows a `⚠ This L2 doesn't support this plant kind`
banner, same shape as §2.5's omission panel, with a deep-link to
the L2 editor's transfer-template block.

#### 2.6.3 `/training/plant/uncovered_rail` (rail dropdown — destructive)

```
│ [uncovered_rail]  L2     Plant: Rail declared but no rows            │
│                                                                      │
│ > /etl/run Coverage shows one card per declared Rail with a ✓/✗.     │
│ > Plant DELETEs every transaction for one rail so its Coverage row   │
│ > flips ✗ ("declared but no rows in window").                        │
│                                                                      │
│ ┌──────────────┬───────────────────────────────────────────────────┐ │
│ │ ... [rail]   │ Rail to empty (DELETE all its transactions)       │ │
│ │              │ ┌─────────────────────────────────────┐ [?]       │ │
│ │              │ │ wire_transfer                    ▼ │           │ │
│ │              │ └─────────────────────────────────────┘           │ │
│ │              │  Picked: alphabetically-last declared rail        │ │
│ │              │  (stable, demo-clear; doesn't depend on row       │ │
│ │              │  counts).                                         │ │
│ │              │                                                   │ │
│ │              │  ⚠ DESTRUCTIVE                                    │ │
│ │              │  This DELETEs every <prefix>_transactions row     │ │
│ │              │  with rail_name='wire_transfer' (~342 rows in     │ │
│ │              │  current baseline). Reset to baseline restores    │ │
│ │              │  them via re-emit.                                │ │
│ │              │                                                   │ │
│ │              │ ▸ Show defaults' reasoning (1 pick)               │ │
│ │              │                                                   │ │
│ │              │ ┌──────────────────────────────┐                  │ │
│ │              │ │  ⚡ Empty the rail + refresh │                  │ │
│ │              │ │     → (DESTRUCTIVE)          │                  │ │
│ │              │ └──────────────────────────────┘                  │ │
│ │              │  ~10s · CTA copy emphasizes the DELETE shape      │ │
│ │              │                                                   │ │
│ │              │ [ Take the tour with these settings → ]           │ │
│ │              │                                                   │ │
│ └──────────────┴───────────────────────────────────────────────────┘ │
```

Operator-facing differences for `uncovered_*`:
- Destructive warning panel (orange chip) sits between the form
  body and the CTA — these plants run DELETE, not INSERT. The row
  count preview ("~342 rows") is computed from `coverage_for` on
  page load so the operator sees what they're about to drop.
- Primary CTA copy is `[ ⚡ Empty the rail + refresh → ]` not
  `[ ⚡ Plant + refresh → ]` — names the actual operation.
- Reset framing in the warning: "Reset to baseline restores them
  via re-emit" — reassures the operator the DELETE is recoverable.

`uncovered_template` is structurally identical with a template
dropdown instead of a rail dropdown.

---

## 3. `/training/tour/<kind>` per-kind tour page (BU.5)

**Updated 2026-05-30 round-3 scope:** §3.0 expands to FOUR
iframe-destination categories (L1 / L2 Triage / L2 Coverage /
L2FT Hygiene). §3.7 (L2 Triage) + §3.8 (L2 Coverage) stay as
canonical examples per category; §3.10 (NEW) covers L2FT Hygiene
as the fourth category. Implementation note: ALL FOUR categories
use ONE canonical `_render_tour_page(entry)` function — the only
delta is the iframe URL template. The registry's
`tour_destination` field IS the source of truth for that URL.

**Consumes:** Lock 0 (scope union) + Lock 3 (embedded iframe +
before/after toggle, destination per-kind) + Lock 4 (reset stays
in header) + Lock 7 (one canonical tour page, destination data-
driven from `entry.tour_destination`).
**Before:** N/A — new surface. The closest analog is opening the
existing `_studio_training` deep-link in a new tab, which shows
the same data the dashboard renders with no plant overlay.

### 3.0 Tour pattern — one shell, four iframe categories

All four categories share the same chrome (handled by
`_render_tour_page(entry)`):
- Sticky bar: `← Back to Training` + `[ Plant page → ]`.
- Page title: `[<kind>] [<category>] Tour: <Human title>`
  (`<category>` ∈ {L1, L2, L2FT}).
- Toggle strip: `[ ●  Before     ◯  After ]` + `[ Done ]`.
- Caption above iframe + "What to point out" callout below.

The ONLY delta across categories is the iframe URL template — the
registry's `tour_destination.primary_url_template` field. Per-
category convention (sourced from Lock 3's mapping table in
`bu_0_replan.md`):

| Category      | `tour_destination.primary_url_template`                                       | Visual delta shape                         |
|---------------|-------------------------------------------------------------------------------|--------------------------------------------|
| L1            | `/dashboards/l1_dashboard/sheets/<sheet_id>` (per entry)                      | KPI flips + chart bars appear              |
| L2 Triage     | `/etl/triage` (optionally `?expand=<kind>` deep-link)                         | Section was empty, now has a card          |
| L2 Coverage   | `/etl/run?failures-only=1#coverage-<rails\|templates>`                        | One ✓ row flipped to ✗ in failures-only    |
| L2FT Hygiene  | `/dashboards/l2_flow_tracing/sheets/l2ft-sheet-l2-exceptions#<check-anchor>`  | One check's table goes from 0 to N rows    |

Mocked-up examples below: §3.1-3.5 (L1), §3.7 (L2 Triage),
§3.8 (L2 Coverage), §3.10 (L2FT Hygiene). All four mockups
illustrate the same shell rendering a different
`tour_destination` URL.

### 3.1 After — `/training/tour/drift`, Before state

```
┌──────────────────────────────────────────────────────────────────────┐
│ Recon-Gen │ L2 Editor │ ETL Support │ Training [●] │ ...             │
├──────────────────────────────────────────────────────────────────────┤
│ Studio · Training · Tour          qsgen-sqlite   [ ↻ Reset to base ] │
├──────────────────────────────────────────────────────────────────────┤
│ ← Back to Training        [ Plant page → ]                           │  ← sticky bar
├──────────────────────────────────────────────────────────────────────┤
│ [drift]  Tour: Sub-ledger drift                                      │
│                                                                      │
│ > For every account on every day, the sum of signed_amount over the  │
│ > account's transactions should equal the daily_balances.balance     │
│ > for that account+day.                                              │
│                                                                      │
│ ┌─────────────────────────────────────────────────┐  [ Done ]        │
│ │  ●  Before     ◯  After                         │                  │
│ └─────────────────────────────────────────────────┘                  │
│                                                                      │
│  What you're looking at: Baseline demo data. The Drift sheet         │
│  shows 0 rows; no account on any day has a drift violation.          │
│                                                                      │
├──────────────────────────────────────────────────────────────────────┤
│ ┌──────────────────────────────────────────────────────────────────┐ │
│ │  ╭─────────────────────────────────────────────────────────────╮ │ │
│ │  │ Drift                              Recon Generator · L1     │ │ │
│ │  │ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ │ │ │
│ │  │ KPI: 0 drifted accounts                                     │ │ │
│ │  │                                                             │ │ │
│ │  │ ┌─────────────────────────────────────────────────────────┐ │ │ │
│ │  │ │ (chart: Drift by day — empty, "No drift in window")     │ │ │ │
│ │  │ └─────────────────────────────────────────────────────────┘ │ │ │
│ │  │                                                             │ │ │
│ │  │ Table: (empty)                                              │ │ │
│ │  │                                                             │ │ │
│ │  │ <embedded App2 iframe, ~70vh, full width>                   │ │ │
│ │  ╰─────────────────────────────────────────────────────────────╯ │ │
│ └──────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│ What to point out to your trainee:                                   │
│  · The KPI reads "0 drifted accounts." This is healthy state.        │
│  · The chart says "No drift in window" — there's nothing to drill    │
│    into.                                                             │
│  · Now toggle to After to see what one drift violation looks like.   │
└──────────────────────────────────────────────────────────────────────┘
```

### 3.2 After — toggled to After state (mid-toggle / loading)

```
│ ┌─────────────────────────────────────────────────┐  [ Done ]        │
│ │  ◯  Before     ●  After                         │                  │
│ └─────────────────────────────────────────────────┘                  │
│                                                                      │
│  What you're looking at: Planting drift on cust-001 5 days ago       │
│  with delta $75.00 + refreshing matviews… (~10s)                     │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │  ▒▒▒▒▒▒▒▒░░░░░░░░░░░░░░░░░░░░░░░░  Refreshing matviews…    │    │
│  └──────────────────────────────────────────────────────────────┘    │
```

### 3.3 After — toggled to After state (loaded)

```
│ ┌─────────────────────────────────────────────────┐  [ Done ]        │
│ │  ◯  Before     ●  After      [ Re-plant ]       │                  │
│ └─────────────────────────────────────────────────┘                  │
│                                                                      │
│  What you're looking at: One drift violation planted on cust-001     │
│  5 days ago. The Drift sheet now shows 1 row; the violation          │
│  account_id + balance_date appear in the table below.                │
│                                                                      │
├──────────────────────────────────────────────────────────────────────┤
│ ┌──────────────────────────────────────────────────────────────────┐ │
│ │  ╭─────────────────────────────────────────────────────────────╮ │ │
│ │  │ Drift                              Recon Generator · L1     │ │ │
│ │  │ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ │ │ │
│ │  │ KPI: 1 drifted account                          ⚠ +1        │ │ │
│ │  │                                                             │ │ │
│ │  │ ┌─────────────────────────────────────────────────────────┐ │ │ │
│ │  │ │ (chart: Drift by day                                    │ │ │ │
│ │  │ │                                                         │ │ │ │
│ │  │ │              ▓                                          │ │ │ │
│ │  │ │              ▓                                          │ │ │ │
│ │  │ │  ────────────▓─────────────────────                     │ │ │ │
│ │  │ │     -10d  -5d *  -3d  -1d                                │ │ │ │
│ │  │ └─────────────────────────────────────────────────────────┘ │ │ │
│ │  │                                                             │ │ │
│ │  │ Table:                                                      │ │ │
│ │  │   account_id   balance_date   drift_amount                  │ │ │
│ │  │   cust-001     2026-05-25     -75.00                        │ │ │
│ │  │                                                             │ │ │
│ │  │ <embedded App2 iframe — same URL as Before, fresh data>     │ │ │
│ │  ╰─────────────────────────────────────────────────────────────╯ │ │
│ └──────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│ What to point out to your trainee:                                   │
│  · The KPI flipped from "0" to "1" — the matview now sees the gap.   │
│  · The day axis shows a bar at -5d matching the plant's days_ago.    │
│  · The table row carries the account_id + drift_amount; the          │
│    trainee can left-click to drill into that account's transactions  │
│    + balance history, where they'd reconcile the ETL source.         │
│  · Toggle back to Before to confirm the dashboard returns to clean.  │
└──────────────────────────────────────────────────────────────────────┘
```

Operator-facing strings:
- Sticky breadcrumb: `← Back to Training` + `[ Plant page → ]` —
  bidirectional access to the kind's plant page from inside the
  tour, so the operator can tweak parameters mid-demo without
  losing the tour context.
- Toggle: `[ ●  Before     ◯  After ]` — two-state pill. Click
  swaps state + triggers the corresponding server action (apply
  plant + matview refresh for `After`; reset-this-kind for
  `Before`). Pill animates the dot moving; iframe shows the
  "Refreshing matviews…" progress bar inline during the action.
- `[ Done ]` — top-right of the toggle strip; takes the operator
  back to `/training/` (the landing). Distinct from the
  breadcrumb's `← Back to Training` in that it implies "I'm done
  with this tour" rather than "I want to leave temporarily."
- `[ Re-plant ]` (shown only in After state) — re-runs the plant
  overlay. Useful when the operator opened the plant page in
  another tab, tweaked settings, came back, and wants the new
  settings reflected.
- "What you're looking at" caption — single paragraph that
  describes what the current state IS, not what to point out
  (that's the bullet list below the iframe). Updates with the
  toggle.
- "What to point out to your trainee" bullet list — sourced from
  a new `tour_notes` field on `InvariantSection`. BU.5 lands the
  field on the dataclass + populates 2-3 kinds (drift, overdraft,
  one chain kind); the long tail of authored notes is BU.7+ or
  BV. Kinds without `tour_notes` show a placeholder: `Tour notes
  not yet authored — see L1 invariants doc for what the sheet
  expects to surface.`

### 3.4 After — tour with no plant configured yet (cold-start)

When the operator arrived at `/training/tour/<kind>` directly
(e.g. via the landing's `[ Take the tour → ]` CTA, without first
visiting the plant page):

```
│ ┌─────────────────────────────────────────────────┐  [ Done ]        │
│ │  ●  Before     ◯  After                         │                  │
│ └─────────────────────────────────────────────────┘                  │
│                                                                      │
│  What you're looking at: Baseline demo data. To toggle "After,"      │
│  the tour will plant drift with default settings:                    │
│   · Account: cust-001    · Days ago: 5                               │
│   · Delta money: $75.00  · Rail: ACHCredit                           │
│   · Counter: ext-corr-bank-001                                       │
│  Want to customize? → Plant page                                     │
```

Operator-facing strings:
- Before-state caption when no plant exists: dumps the default
  picker values inline so the operator sees what `After` would do
  without leaving the page. Routes to the plant page for
  customization.
- After toggling: the tour uses `default_scenario_for`'s picks
  unless the operator arrived via `/training/plant/<kind>`'s
  "Take the tour with these settings" CTA (in which case URL
  fragment carries overrides).

### 3.5 After — tour where the iframe sheet doesn't exist for this kind

A few kinds map to "Today's Exceptions" rather than a dedicated
sheet (per `_L1_KIND_TO_SHEET_ID`):
`chain_parent_disagreement` → `l1-sheet-todays-exceptions`,
`xor_group_violation` → same, `fan_in_disagreement` → same,
`multi_xor_violation` → same, `expected_eod_balance_breach` →
same.

For these kinds, the tour iframe shows Today's Exceptions sheet
with a caption banner explaining the kind-specific row to look
for:

```
│  What you're looking at: One <kind> violation planted. The Today's   │
│  Exceptions sheet aggregates several violation kinds; look for the   │
│  row with kind_label = "<kind>" + transfer_id = <planted_id>.        │
│  This kind doesn't have a dedicated sheet — the Exceptions table     │
│  is the canonical surface.                                           │
```

### 3.6 Captions — implementation notes

- **Iframe URL.** `/dashboards/l1_dashboard/sheets/<sheet_id>` —
  exact same URL the existing `_studio_training` deep-link
  generates. Per-kind sheet_id from
  `_studio_training._L1_KIND_TO_SHEET_ID`.
- **Toggle wiring.** Click `After` →
  `POST /training/tour/<kind>/plant` (form values from URL
  fragment OR defaults from `default_scenario_for`) → wait for
  200 + refresh matviews → reload iframe with cache-busting
  query param. Click `Before` →
  `POST /training/tour/<kind>/reset-kind` (per-kind variant of
  Lock 4 — see §7 Q3) → reload iframe.
- **Toggle disabled states.** While a plant or reset is in
  flight, both pill buttons are disabled. The progress bar
  inside the iframe area gives feedback (10s for plant, 10s for
  reset — both run the matview refresh).
- **Browser-tab pulse on completion.** Same pattern as
  `/etl/run` per BTa.0 §7 Q4. Tab title pulses `(✓) After ready`
  or `(✓) Reset complete` for 5s.
- **Caption "+1" delta in KPI.** Showing the diff between Before
  and After KPIs would require a separate query against the
  matview; cheap, but adds a code path. Default: render the
  After KPI value as-is + a `⚠ +N` chip when known (the plant
  primitive knows how many rows it inserts; just hard-code the
  delta per kind). Cut to a simple post-state KPI if the chip
  approach is fiddly to implement.

### 3.7 After — L2 Triage tour, `/training/tour/phantom_rail`

The 3 L2 Triage-gap kinds (`phantom_rail`, `phantom_template`,
`missing_metadata`) tour `/etl/triage` in the iframe. The page is
a list of accordion sections (one per gap kind); the planted gap
appears as a new card inside its section.

#### 3.7.1 Before state — Triage all-clear

```
┌──────────────────────────────────────────────────────────────────────┐
│ Studio · Training · Tour          qsgen-sqlite   [ ↻ Reset to base ] │
├──────────────────────────────────────────────────────────────────────┤
│ ← Back to Training        [ Plant page → ]                           │
├──────────────────────────────────────────────────────────────────────┤
│ [phantom_rail]  L2     Tour: Phantom rail in feed                    │
│                                                                      │
│ > ETL emits transactions tagged with a rail_name your L2 doesn't     │
│ > declare. Triage surfaces these as an "unmatched_rail" card.        │
│                                                                      │
│ ┌─────────────────────────────────────────────────┐  [ Done ]        │
│ │  ●  Before     ◯  After                         │                  │
│ └─────────────────────────────────────────────────┘                  │
│                                                                      │
│  What you're looking at: Baseline demo data. /etl/triage shows the   │
│  "Unmatched rails" section is empty (✓ all rail_names resolve).      │
│                                                                      │
├──────────────────────────────────────────────────────────────────────┤
│ ┌──────────────────────────────────────────────────────────────────┐ │
│ │  ╭─────────────────────────────────────────────────────────────╮ │ │
│ │  │ Triage                              Recon Generator · ETL   │ │ │
│ │  │ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ │ │ │
│ │  │ 0 gaps detected · last refresh 14:23                        │ │ │
│ │  │                                                             │ │ │
│ │  │ ▶ Unmatched rails (0)                                       │ │ │
│ │  │ ▶ Unmatched templates (0)                                   │ │ │
│ │  │ ▶ Missing limit schedules (0)                               │ │ │
│ │  │ ▶ Missing metadata keys (0)                                 │ │ │
│ │  │                                                             │ │ │
│ │  │ <embedded /etl/triage iframe, ~70vh, full width>            │ │ │
│ │  ╰─────────────────────────────────────────────────────────────╯ │ │
│ └──────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│ What to point out to your trainee:                                   │
│  · All four sections show "(0)" — the L2 contract matches the feed.  │
│  · No accordion has anything to expand. This is healthy state.       │
│  · Toggle After to see what 3 phantom-rail rows do.                  │
└──────────────────────────────────────────────────────────────────────┘
```

#### 3.7.2 After state — planted unmatched_rail gap

```
│ ┌─────────────────────────────────────────────────┐  [ Done ]        │
│ │  ◯  Before     ●  After      [ Re-plant ]       │                  │
│ └─────────────────────────────────────────────────┘                  │
│                                                                      │
│  What you're looking at: Planted 3 transactions with                 │
│  rail_name="legacy_card_swipe" — a name the L2 doesn't declare. The  │
│  "Unmatched rails" section now has one card.                         │
│                                                                      │
├──────────────────────────────────────────────────────────────────────┤
│ ┌──────────────────────────────────────────────────────────────────┐ │
│ │  ╭─────────────────────────────────────────────────────────────╮ │ │
│ │  │ Triage                              Recon Generator · ETL   │ │ │
│ │  │ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ │ │ │
│ │  │ 1 gap detected · last refresh 14:25                         │ │ │
│ │  │                                                             │ │ │
│ │  │ ▾ Unmatched rails (1)                                       │ │ │
│ │  │   ┌────────────────────────────────────────────────────┐   │ │ │
│ │  │   │ legacy_card_swipe                  3 rows  ⚠       │   │ │ │
│ │  │   │ 3 rows arrived with rail_name="legacy_card_swipe"  │   │ │ │
│ │  │   │ but the L2 declares no Rail of that name.          │   │ │ │
│ │  │   │ Sample id: __demo_gap_phantom_rail_000             │   │ │ │
│ │  │   │ Declared rails: ACHCredit, ACHDebit, …             │   │ │ │
│ │  │   │                                                    │   │ │ │
│ │  │   │ → Open L2 editor (Rails block)                     │   │ │ │
│ │  │   └────────────────────────────────────────────────────┘   │ │ │
│ │  │ ▶ Unmatched templates (0)                                   │ │ │
│ │  │ ▶ Missing limit schedules (0)                               │ │ │
│ │  │ ▶ Missing metadata keys (0)                                 │ │ │
│ │  │                                                             │ │ │
│ │  │ <embedded /etl/triage iframe — same URL, fresh data>        │ │ │
│ │  ╰─────────────────────────────────────────────────────────────╯ │ │
│ └──────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│ What to point out to your trainee:                                   │
│  · The header count flipped from "0" to "1 gap detected."            │
│  · The Unmatched rails section auto-expanded — there's now           │
│    something to see. (Future feature: deep-link the iframe to        │
│    pre-expand this section; §7 Q12 captures the option.)             │
│  · The card shows volume (3 rows), sample id, and the operator's    │
│    next move ("→ Open L2 editor (Rails block)").                    │
│  · The trainee's job: decide if `legacy_card_swipe` is a real rail   │
│    they should declare, or an ETL tagging bug they should fix.       │
│  · Toggle Before to confirm the section returns to empty.            │
└──────────────────────────────────────────────────────────────────────┘
```

### 3.8 After — L2 Coverage tour, `/training/tour/uncovered_rail`

The 2 L2 Coverage-gap kinds (`uncovered_rail`, `uncovered_template`)
tour `/etl/run?failures-only=1#coverage-*` in the iframe. The
`?failures-only=1` query string engages the BTa.6 toggle so the
operator's eye lands on the planted ✗ row instead of scrolling
through dozens of ✓s.

#### 3.8.1 Before state — Coverage all-green (failures-only engaged)

```
│ [uncovered_rail]  L2     Tour: Rail declared but no rows             │
│                                                                      │
│ > /etl/run Coverage shows one card per declared Rail with ✓ when     │
│ > rows landed, ✗ when none did. Plant DELETEs every transaction for  │
│ > one rail so its Coverage row flips ✗.                              │
│                                                                      │
│ ┌─────────────────────────────────────────────────┐  [ Done ]        │
│ │  ●  Before     ◯  After                         │                  │
│ └─────────────────────────────────────────────────┘                  │
│                                                                      │
│  What you're looking at: Baseline demo data with                     │
│  ?failures-only=1 engaged. The Rails coverage card shows "All 28     │
│  rails covered ✓" — no rows to list.                                 │
│                                                                      │
├──────────────────────────────────────────────────────────────────────┤
│ ┌──────────────────────────────────────────────────────────────────┐ │
│ │  ╭─────────────────────────────────────────────────────────────╮ │ │
│ │  │ Run · Coverage              Recon Generator · ETL · Run     │ │ │
│ │  │ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ │ │ │
│ │  │ [ ☑ Failures only ]                                         │ │ │
│ │  │                                                             │ │ │
│ │  │ ▼ Rails coverage                                            │ │ │
│ │  │   All 28 rails covered ✓                                    │ │ │
│ │  │   (Toggle Failures only off to see the full list)           │ │ │
│ │  │                                                             │ │ │
│ │  │ ▼ Templates coverage                                        │ │ │
│ │  │   All 9 templates covered ✓                                 │ │ │
│ │  │                                                             │ │ │
│ │  │ <embedded /etl/run iframe, scrolled to #coverage-rails>     │ │ │
│ │  ╰─────────────────────────────────────────────────────────────╯ │ │
│ └──────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│ What to point out to your trainee:                                   │
│  · "All 28 rails covered ✓" — every declared rail has rows in the    │
│    window. This is healthy state.                                    │
│  · The Failures-only toggle is on; if you turned it off, you'd see   │
│    the full list of 28 ✓ rows.                                       │
│  · Toggle After — we'll empty one rail so its row flips ✗.           │
└──────────────────────────────────────────────────────────────────────┘
```

#### 3.8.2 After state — one rail flipped to ✗

```
│ ┌─────────────────────────────────────────────────┐  [ Done ]        │
│ │  ◯  Before     ●  After      [ Re-plant ]       │                  │
│ └─────────────────────────────────────────────────┘                  │
│                                                                      │
│  What you're looking at: Emptied wire_transfer (342 rows DELETEd).   │
│  The Rails coverage card now shows "27 of 28 covered" + the one ✗    │
│  row for wire_transfer.                                              │
│                                                                      │
├──────────────────────────────────────────────────────────────────────┤
│ ┌──────────────────────────────────────────────────────────────────┐ │
│ │  ╭─────────────────────────────────────────────────────────────╮ │ │
│ │  │ Run · Coverage              Recon Generator · ETL · Run     │ │ │
│ │  │ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ │ │ │
│ │  │ [ ☑ Failures only ]                                         │ │ │
│ │  │                                                             │ │ │
│ │  │ ▼ Rails coverage                                            │ │ │
│ │  │   27 of 28 covered · 1 failure                              │ │ │
│ │  │                                                             │ │ │
│ │  │   ┌──────────────────────────────────────────────────┐     │ │ │
│ │  │   │ wire_transfer                       0 rows  ✗    │     │ │ │
│ │  │   │ Declared in L2 but no transactions landed in     │     │ │ │
│ │  │   │ window. Either the ETL skipped this rail or the  │     │ │ │
│ │  │   │ declaration is stale.                            │     │ │ │
│ │  │   └──────────────────────────────────────────────────┘     │ │ │
│ │  │                                                             │ │ │
│ │  │ ▼ Templates coverage                                        │ │ │
│ │  │   All 9 templates covered ✓                                 │ │ │
│ │  │                                                             │ │ │
│ │  │ <embedded /etl/run iframe — same URL, fresh data>           │ │ │
│ │  ╰─────────────────────────────────────────────────────────────╯ │ │
│ └──────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│ What to point out to your trainee:                                   │
│  · The header count: "27 of 28 covered · 1 failure" — one rail       │
│    flipped from ✓ to ✗.                                              │
│  · The wire_transfer row shows "0 rows ✗" with the diagnosis.        │
│  · With Failures-only on, the trainee's eye lands directly on the    │
│    planted ✗ instead of scrolling through 27 ✓ rows.                 │
│  · The trainee's job: decide if wire_transfer is genuinely unused    │
│    (drop the declaration) or if the ETL feed is broken (fix it).     │
│  · Toggle Before — Reset to baseline restores the 342 rows via       │
│    re-emit; the row flips back to ✓.                                 │
└──────────────────────────────────────────────────────────────────────┘
```

### 3.9 Captions — L2 tour implementation notes

- **Iframe URL per kind:**
  - `phantom_rail`, `phantom_template`, `missing_metadata` →
    `/etl/triage` (no query params; the planted gap's section
    auto-expands per `_etl_triage_routes` default behavior, or via
    the §7 Q12 deep-link enhancement).
  - `uncovered_rail` → `/etl/run?failures-only=1#coverage-rails`.
  - `uncovered_template` →
    `/etl/run?failures-only=1#coverage-templates`.
- **Toggle wiring for L2:** same `POST /training/tour/<kind>/plant`
  + `POST /training/tour/<kind>/reset-kind` shape as L1. The plant
  endpoint calls the corresponding `demo_etl_gaps.py` function
  with the operator's form overrides; the reset-kind endpoint
  always falls back to full reset under the hood (per §7 Q3 round-1
  decision).
- **Pacing parity:** L2 plants complete faster than L1 (no
  matview refresh on the L2-feed-contract checks — triage + coverage
  query the base table directly). Realistic Before→After tour
  cycle: ~3-5s for L2 vs ~10s for L1. The toggle disabled-state
  ends sooner; consider showing the elapsed time in the
  "Refreshing…" caption.
- **"What to point out" callout** for L2 kinds focuses on the
  integrator's decision tree (declare vs fix vs drop) rather than
  the L1 "drill into the row" pattern. The trainee outcome is
  different — L2 cards demand a triage decision; L1 dashboard rows
  demand investigation.
- **No KPI delta chip on L2.** L2 iframe destinations don't have a
  headline KPI in the same shape as L1 dashboards. The before/after
  delta is the section-card-appeared or row-flipped-to-✗
  observation in the caption strip itself, not a chip on a number.

### 3.10 After — L2FT Hygiene tour, `/training/tour/chain_orphan` (NEW round-3)

The 4 L2FT Hygiene-gap kinds (`chain_orphan`,
`dead_bundles_activity`, `dead_metadata`, `dead_limit_schedule`)
tour the L2 Flow Tracing app's L2 Hygiene Exceptions sheet. URL
fragment anchors to the per-check section so the iframe scrolls
straight there. NOT the same as `/etl/triage` — different surface,
different audience (dashboard-watcher vs ETL-debugger).

#### 3.10.1 Before state — L2FT L2 Hygiene Exceptions all-clear

```
┌──────────────────────────────────────────────────────────────────────┐
│ Studio · Training · Tour          qsgen-sqlite   [ ↻ Reset to base ] │
├──────────────────────────────────────────────────────────────────────┤
│ ← Back to Training        [ Plant page → ]                           │
├──────────────────────────────────────────────────────────────────────┤
│ [chain_orphan]  L2FT     Tour: Chain orphan                          │
│                                                                      │
│ > Each row is a declared Required chain edge (parent → child) where  │
│ > the parent rail fired in the window but no matched child firing    │
│ > followed within the SLA.                                           │
│                                                                      │
│ ┌─────────────────────────────────────────────────┐  [ Done ]        │
│ │  ●  Before     ◯  After                         │                  │
│ └─────────────────────────────────────────────────┘                  │
│                                                                      │
│  What you're looking at: Baseline demo data. The L2 Hygiene          │
│  Exceptions sheet's "Chain Orphans" check shows 0 rows — every       │
│  parent firing has a matched child within SLA.                       │
│                                                                      │
├──────────────────────────────────────────────────────────────────────┤
│ ┌──────────────────────────────────────────────────────────────────┐ │
│ │  ╭─────────────────────────────────────────────────────────────╮ │ │
│ │  │ L2 Flow Tracing · L2 Hygiene Exceptions                     │ │ │
│ │  │ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ │ │ │
│ │  │ 0 hygiene exceptions across 6 checks                        │ │ │
│ │  │                                                             │ │ │
│ │  │ ▼ Chain Orphans (0)                                         │ │ │
│ │  │   parent_name | child_name | parent_count | child_count |   │ │ │
│ │  │   orphan_count                                              │ │ │
│ │  │   (no rows)                                                 │ │ │
│ │  │                                                             │ │ │
│ │  │ ▶ Unmatched Rail Name (0)                                   │ │ │
│ │  │ ▶ Dead Rails (0)                                            │ │ │
│ │  │ ▶ Dead Bundles Activity (0)                                 │ │ │
│ │  │ ▶ Dead Metadata Declarations (0)                            │ │ │
│ │  │ ▶ Dead Limit Schedules (0)                                  │ │ │
│ │  │                                                             │ │ │
│ │  │ <embedded L2FT L2 Hygiene Exceptions iframe, ~70vh,         │ │ │
│ │  │  scrolled to #chain-orphans anchor>                         │ │ │
│ │  ╰─────────────────────────────────────────────────────────────╯ │ │
│ └──────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│ What to point out to your trainee:                                   │
│  · All 6 hygiene checks show "(0)" — the L2's declared chain edges,  │
│    rail metadata, and limit schedules all match what the runtime     │
│    is actually doing.                                                │
│  · The "Chain Orphans" check is auto-expanded (URL anchor scrolled   │
│    the iframe here on load).                                         │
│  · Toggle After to see what one orphaned chain firing looks like.    │
└──────────────────────────────────────────────────────────────────────┘
```

#### 3.10.2 After state — planted chain orphan

```
│ ┌─────────────────────────────────────────────────┐  [ Done ]        │
│ │  ◯  Before     ●  After      [ Re-plant ]       │                  │
│ └─────────────────────────────────────────────────┘                  │
│                                                                      │
│  What you're looking at: Planted 1 parent firing of `ACHCredit`      │
│  with NO matching child fire on the declared `ACHReturn` child       │
│  rail within SLA. The Chain Orphans check now shows 1 row.           │
│                                                                      │
├──────────────────────────────────────────────────────────────────────┤
│ ┌──────────────────────────────────────────────────────────────────┐ │
│ │  ╭─────────────────────────────────────────────────────────────╮ │ │
│ │  │ L2 Flow Tracing · L2 Hygiene Exceptions                     │ │ │
│ │  │ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ │ │ │
│ │  │ 1 hygiene exception across 6 checks                         │ │ │
│ │  │                                                             │ │ │
│ │  │ ▼ Chain Orphans (1)                                         │ │ │
│ │  │   parent_name | child_name  | p_count | c_count | orphans   │ │ │
│ │  │   ─────────────────────────────────────────────────────────  │ │ │
│ │  │   ACHCredit   | ACHReturn   |    1    |    0    |    1      │ │ │
│ │  │                                                             │ │ │
│ │  │ ▶ Unmatched Rail Name (0)                                   │ │ │
│ │  │ ▶ Dead Rails (0)                                            │ │ │
│ │  │ ...                                                         │ │ │
│ │  │                                                             │ │ │
│ │  │ <embedded iframe — same URL, fresh data>                    │ │ │
│ │  ╰─────────────────────────────────────────────────────────────╯ │ │
│ └──────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│ What to point out to your trainee:                                   │
│  · The header count flipped from "0" to "1 hygiene exception."       │
│  · The Chain Orphans row says ACHCredit → ACHReturn fired 1 → 0;     │
│    one parent fired but its declared child never did.                │
│  · The trainee's decision: either fix the ETL so ACHReturn fires    │
│    when ACHCredit does, OR retire the chain edge from the L2 YAML.  │
│  · Toggle Before to confirm the row vanishes after reset.            │
└──────────────────────────────────────────────────────────────────────┘
```

#### 3.10.3 Captions — L2FT Hygiene tour implementation notes

- **Iframe URL** (registry-driven, per `entry.tour_destination`):
  - `chain_orphan` →
    `/dashboards/l2_flow_tracing/sheets/l2ft-sheet-l2-exceptions#chain-orphans`
  - `dead_bundles_activity` →
    `/dashboards/l2_flow_tracing/sheets/l2ft-sheet-l2-exceptions#dead-bundles`
  - `dead_metadata` →
    `/dashboards/l2_flow_tracing/sheets/l2ft-sheet-l2-exceptions#dead-metadata`
  - `dead_limit_schedule` →
    `/dashboards/l2_flow_tracing/sheets/l2ft-sheet-l2-exceptions#dead-limits`
  Anchor IDs need to be added to the L2 Hygiene Exceptions sheet's
  HTML render (BU.5 work — tiny anchor change in
  `apps/l2_flow_tracing/app.py`'s populate function).
- **Secondary tour links** (when set on the entry): some L2 plants
  light up two surfaces — `phantom_rail` also fires the L2FT
  `unmatched_rail_name` check, `uncovered_rail` also drops the
  L2FT `dead_rails` check. The L2 Triage/Coverage tours for those
  kinds carry a secondary link below the iframe: `Also visible
  on: L2FT Hygiene Exceptions (Unmatched Rail Name)`. NOT a
  second iframe — just a callout pill the operator clicks if they
  want to navigate. Keeps the tour focused on one canonical
  destination; doesn't hide the dual-surface fact.
- **Toggle wiring + reset**: same as L2 Triage / Coverage tours —
  `POST /training/tour/<kind>/plant` calls the entry's
  `plant_function` (a new `demo_etl_gaps.py` function per BU.3 cell);
  reset-kind falls back to full reset under the hood (§7 Q3
  decision).
- **Pacing**: L2FT Hygiene plants use raw DML and may include
  matview refreshes (depending on whether the per-check dataset
  reads `<prefix>_current_transactions` directly or via a matview).
  Conservative ETA: ~5-8s for the Before→After flip. Faster than
  L1 (no per-account daily-balance refresh).
- **"What to point out" callout** focuses on the integrator's
  retire-vs-fix decision (drop the L2 declaration vs fix the
  ETL/feed) — same shape as L2 Triage / Coverage.

---

## 4. Reset-to-baseline flow (BU.4)

**Consumes:** Lock 4 (truncate+reseed, no modal).
**Before:** N/A — Trainer can't reset today (the trainer pane on
`/data` doesn't plant anything, so there's nothing to reset).
`/etl/run` Refresh Data does the same op but in a different
context.

### 4.1 Before — current state

```
(no equivalent — the Trainer surface doesn't plant)
```

### 4.2 After — Reset button on every `/training/*` page header

```
├──────────────────────────────────────────────────────────────────────┤
│ Studio · Training                  qsgen-sqlite   [ ↻ Reset to base ]│
├──────────────────────────────────────────────────────────────────────┤
                                                       ▲
                                                       │
                       Same button on every /training/* page:
                       landing, /training/plant/<kind>,
                       /training/tour/<kind>.
```

### 4.3 After — Reset in flight

```
├──────────────────────────────────────────────────────────────────────┤
│ Studio · Training                  qsgen-sqlite   [ ▒ Resetting…   ] │
├──────────────────────────────────────────────────────────────────────┤
```

Button disabled, label changes, spinner glyph; matches the
`/etl/run` Refresh in-flight pattern.

### 4.4 After — Reset complete (flash + browser-tab pulse)

```
├──────────────────────────────────────────────────────────────────────┤
│ Studio · Training                  qsgen-sqlite   [ ↻ Reset to base ]│
├──────────────────────────────────────────────────────────────────────┤
│  ┌────────────────────────────────────────────────────────────┬───┐  │
│  │ ✓ Reset to baseline — 3 plants removed (12.4s)             │ ✕ │  │
│  │   (browser-tab bell rang)                                  └───┤  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                      │
│ ...rest of page renders with all status pills back to "● not planted"│
```

Operator-facing strings:
- Button labels: `↻ Reset to baseline` (idle) / `▒ Resetting…`
  (in flight). Glyph (`↻` = rotate, `▒` = barred) signals state
  change.
- Success flash: `✓ Reset to baseline — <N> plants removed (<X>s)`.
  Auto-dismisses after 10s OR on `✕`. Caption matches BTa.6 flash
  pattern.
- Browser-tab title pulse: `(✓) Reset complete` for 5s, then
  back to static title.
- Failure flash (truncate or reseed errors): `✗ Reset failed —
  <error>` with a `[ Retry ]` button. Same shape as `/etl/run`'s
  halt flash.

### 4.5 Caption — what the reset actually does

**Updated 2026-05-30 round-2 scope:** Lock 6 (L2 plant cleanup
parity) confirmed — both L1 and L2 plants ride the same truncate-
and-reseed code path. No special-casing needed.

The reset code path is literally the same as `/etl/run`'s
Refresh Data:

1. Truncate `<prefix>_transactions` + `<prefix>_daily_balances` +
   `<prefix>_training_state`.
2. Re-run the ETL hook OR (when `cfg.app2.etl_hook is None`) the
   bundled generator with `default_scenario_for` → `densify_scenario`
   → `add_broken_rail_plants` → `boost_inv_fanout_plants` (per
   `cli/_helpers.build_default_scenario`).
3. Refresh matviews via `refresh_matviews_sql(l2_instance)`.

Both plant categories live entirely inside `<prefix>_transactions`
(+ `<prefix>_daily_balances` for L1 balance plants), so truncate
wipes both equally:
- **L1 plants:** `ScenarioPlant` entries that `emit_full_seed`
  interpolates into the baseline emit. Truncate wipes them
  alongside the baseline; reseed emits the baseline alone.
- **L2 plants:** raw INSERT/DELETE statements `demo_etl_gaps.py`
  emits AFTER `emit_full_seed`. The INSERT-based plants
  (`phantom_*`, `missing_metadata`) are wiped by the truncate; the
  DELETE-based plants (`uncovered_*`) are "wiped" by the reseed
  re-emitting the deleted rows (the L2 declarations didn't change,
  just the transaction data, so re-emit restores them).

No per-plant DELETE bookkeeping needed for either category.

**The one L2-specific caveat for `uncovered_*` on real-hook
deployments:** if `cfg.app2.etl_hook` is wired and the hook is a
streaming source, it may immediately re-DELETE the restored rows
on the next refresh cycle if upstream really doesn't have that
rail/template firing. Same shape as the L1 caveat below. Side-
panel caption on the `uncovered_*` plant page documents this:
"Your ETL hook is the source of truth — if upstream genuinely has
no `wire_transfer` rows, reset may not restore them. For a durable
demo, plant + tour in a single session."

Operator-facing implication: a real-hook deployment with
`cfg.app2.etl_hook` set re-runs the hook on reset. The hook had better
be deterministic (the same hook + same demo DB state in =
same OUT). If the hook reads from a moving upstream feed, reset
may produce different data than the baseline the Trainer planted
against. Caption on the reset button's `[?]` side-panel: "When an
ETL hook is wired, Reset re-runs your hook. If your hook's
upstream feed has changed, the baseline state after Reset may
differ. Plant fresh after reset for accurate before/after." (§7
Q6 — operator confirms this caption.)

---

## 5. Cross-page interaction flow

### 5.1 Pre-BU flow (BS-era, broken)

```
            ┌──────────┐
   operator │  /data   │ ← right-column pane shows catalog
   lands ──▶│          │   with deep-links to App2 sheets
            └────┬─────┘
                 │ clicks "Open dashboard sheet →"
                 │ on the drift card
                 ▼
            ┌──────────┐
            │ /dash-   │ ← App2 Drift sheet, no plants;
            │ boards/  │   "0 drifted accounts" KPI; the
            │ l1/sheets│   trainer has nothing to demo
            │ /drift   │
            └────╳─────┘ ← operator stuck; can't plant
                            anything, only opens the catalog
                            in another tab
            ┌──────────┐
            │ /training│ ← (top-nav link) 404
            └────╳─────┘
```

### 5.2 Post-BU flow (the fix)

```
            ┌──────────────────────────────┐
            │  /training/                  │ ← grid of 12 kinds,
   first──▶│  (landing — accordion grid)  │   5 families collapsed
   visit   │  + [ ↻ Reset to baseline ]   │   + reset button always
            └────┬─────────────────────────┘   visible
                 │ expands "Balance integrity"
                 │ accordion; sees 3 cards;
                 │ picks drift's [ Plant this → ]
                 ▼
            ┌──────────────────────────────┐
            │ /training/plant/drift        │ ← form pre-filled by
            │  ?from=/training/            │   default_scenario_for;
            │                              │   left rail of all 12
            │  ← Back to Training (sticky) │   kinds; "Show defaults'
            └────┬─────────────────────────┘   reasoning" details
                 │ accepts defaults; clicks
                 │ [ ⚡ Plant + refresh → ]
                 ▼
            ┌──────────────────────────────┐
            │ /training/plant/drift        │ ← flash: "✓ Planted at
            │  (post-plant)                │   14:23:13 — 1 drift row
            │  Status pill: ● planted       │   + matview refresh"
            │  CTA renamed: [ Re-plant → ] │   View on L1 Dashboard ▸
            └────┬─────────────────────────┘
                 │ clicks [ Take the tour with these settings → ]
                 ▼
            ┌──────────────────────────────┐
            │ /training/tour/drift         │ ← embedded App2 iframe;
            │  ?from=/training/plant/drift │   sticky bar shows both
            │                              │   ← Back to Training AND
            │  Toggle: ● Before  ◯ After   │   [ Plant page → ]
            │  (After auto-applied from    │
            │   plant page handoff)        │
            └────┬─────────────────────────┘
                 │ walks trainee through:
                 │ - "0 → 1" KPI delta
                 │ - "What to point out" bullets
                 │ - toggle Before/After to
                 │   show the difference
                 ▼
            ┌──────────────────────────────┐
            │ /training/tour/drift         │ ← After loaded; KPI = 1;
            │  (After state)               │   chart shows planted day
            │                              │
            └────┬─────────────────────────┘
                 │ clicks [ Done ]
                 ▼
            ┌──────────────────────────────┐
            │ /training/                   │ ← landing re-rendered;
            │  (landing, drift planted)    │   drift card status pill
            │  Balance integrity (1       │   = ● planted; CTAs flip
            │  planted) auto-expanded      │   to Re-plant + Tour +
            │                              │   Remove plant
            └────┬─────────────────────────┘
                 │ optional: picks next kind, repeats
                 │ OR clicks [ ↻ Reset to baseline ]
                 ▼
            (loop complete; one kind demoed end-to-end in ~6 clicks)
```

The post-BU flow is the BS-era "open catalog → open dashboard
sheet" hop expanded into a complete plant-then-demo loop. The
operator never leaves the `/training/*` URL prefix for the
authoring half of the flow; only the dashboard iframe content
comes from `/dashboards/*` (and stays embedded).

### 5.3 Sub-nav strip — `/training/*` chrome

**Updated 2026-05-30 round-2 scope:** the sub-nav strip carries a
category badge (`L1` or `L2`) next to the current kind to reinforce
which catalog the operator's working in, especially when bouncing
between landing → kind page → tour iframe (which shows a different
destination URL pattern per category).

Mirroring `/etl/`'s sub-nav strip (`_render_etl_sub_nav`,
`_studio_routes.py:623-674`), every `/training/*` page renders a
sub-nav strip below the page header:

```
├──────────────────────────────────────────────────────────────────────┤
│ ⌂ Catalog | ⚡ Plant: drift [L1] | 📺 Tour: drift [L1] | ← Landing  │
├──────────────────────────────────────────────────────────────────────┤
```

L2 example:

```
├──────────────────────────────────────────────────────────────────────┤
│ ⌂ Catalog | ⚡ Plant: phantom_rail [L2] | 📺 Tour: phantom_rail [L2]│
│           | ← Landing                                                │
├──────────────────────────────────────────────────────────────────────┤
```

Strip items:
- `⌂ Catalog` — links to `/training/` (the landing).
- `⚡ Plant: <current kind>` — links to the current kind's plant
  page. When the operator is ON the plant page, this is the active
  entry (rendered as a "you-are-here" label per `_render_etl_sub_nav`'s
  active-style pattern: flat text, accent underline, no button
  chrome).
- `📺 Tour: <current kind>` — links to the current kind's tour
  page. Same active-state rendering.
- `← Return to landing` — explicit exit; same as `Catalog` but
  reads as "I'm done" rather than "go home."

When the operator is on the landing (no current kind), the Plant
+ Tour entries are absent; the strip just shows `⌂ Catalog`
(active, no-op) + nothing else. Or — cleaner — the strip is
omitted entirely on the landing (the landing IS the catalog;
sub-nav strip is redundant).

When the operator navigates from kind A to kind B (via the left
rail on a plant page, or by re-entering from the landing), the
`<current kind>` in the strip updates. The strip uses URL state
(no per-session memory).

Operator-facing strings + icons mirror `/etl/`'s strip:
- `⌂` (home glyph) for landing entry.
- `⚡` for plant — same lightning glyph as the plant page CTA;
  reinforces "this changes data."
- `📺` for tour — TV-shaped glyph; "watch the dashboard render."
  Open to substitution if the glyph collides with Studio theme.
- `←` for return — symmetric with BTa's `← Loop overview`.

### 5.4 Plant cancel / undo affordance mid-tour

Mid-tour the operator may want to back out without leaving the
plant in place. Three escape valves:

1. **In-tour `Before` toggle** — flips the data back without
   leaving the page. The matview refresh runs; plant is gone.
2. **`Reset to baseline` button (page header)** — drops every
   plant globally. Works mid-tour; the tour page auto-flips to
   `Before` state on completion + reloads the iframe.
3. **`[ Remove plant ]` on the plant page** — per-kind undo;
   the tour's `Before` toggle is the same op under the hood.
   Open question §7 Q3 — whether per-kind remove is a real
   operation or always falls back to full reset under the hood.

No mid-action "cancel" — once `[ ⚡ Plant + refresh → ]` or
`[ ↻ Reset to baseline ]` fires, the server runs to completion
(matview refresh can't be partially undone). The button is
disabled during the ~10s window so the operator can't double-fire,
and the in-flight progress bar shows what's running.

---

## 6. `/data` right-column pane removal (Lock 5)

**Consumes:** Lock 5 (subsume, don't split).

### 6.1 Before — current `/data` page right column

```
┌──────────────────────────────────────────────────────────────────────┐
│ Recon-Gen │ L2 Editor │ ETL Support │ Training │ ...                 │
├──────────────────────────────────────────────────────────────────────┤
│ Studio · data shaping              qsgen-sqlite   [ Deploy changes ] │
├──────────────────────────────────────────────────────────────────────┤
│ (etl_hook_strip / scope_strip / window_strip / seed_strip / ...     )│
├──────────────────────────────────────────────────────────────────────┤
│ ┌───────────────────┬────────────────────────────────────────────┐   │
│ │ Timeline section  │ Training pane (data-training section)      │   │
│ │ (24rem col)       │ (1fr col)                                  │   │
│ │                   │                                            │   │
│ │ <timeline>        │ Exception catalogue                        │   │
│ │ <events>          │                                            │   │
│ │ <histograms>      │ Each card describes one L1 invariant kind: │   │
│ │ ...               │ what it means, what to do when it fires,   │   │
│ │                   │ and a link to the dashboard sheet that     │   │
│ │                   │ surfaces the underlying matview.           │   │
│ │                   │                                            │   │
│ │                   │ ┌────────────────────────────────────────┐ │   │
│ │                   │ │ [drift]                                │ │   │
│ │                   │ │ Sub-ledger drift                       │ │   │
│ │                   │ │ For every account... ✓ Open dashboard →│ │   │
│ │                   │ └────────────────────────────────────────┘ │   │
│ │                   │ ┌────────────────────────────────────────┐ │   │
│ │                   │ │ [overdraft] ...                        │ │   │
│ │                   │ └────────────────────────────────────────┘ │   │
│ │                   │ ... (12 cards)                             │   │
│ └───────────────────┴────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────┘
```

### 6.2 After — `/data` page reverts to single-column (or new use)

```
┌──────────────────────────────────────────────────────────────────────┐
│ Recon-Gen │ L2 Editor │ ETL Support │ Training │ ...                 │
├──────────────────────────────────────────────────────────────────────┤
│ Studio · data shaping              qsgen-sqlite   [ Deploy changes ] │
├──────────────────────────────────────────────────────────────────────┤
│ (etl_hook_strip / scope_strip / window_strip / seed_strip / ...     )│
├──────────────────────────────────────────────────────────────────────┤
│ ┌──────────────────────────────────────────────────────────────────┐ │
│ │ Timeline section                                                 │ │
│ │ (max-w-7xl mx-auto p-4)                                          │ │
│ │                                                                  │ │
│ │ <timeline>                                                       │ │
│ │ <events>                                                         │ │
│ │ <histograms>                                                     │ │
│ │ ...                                                              │ │
│ └──────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│   The trainer catalog moved to Training (top nav).                   │
│   → /training/                                                       │
└──────────────────────────────────────────────────────────────────────┘
```

Operator-facing strings:
- Removal: `_studio_routes._render_data_page`'s `<main>` grid
  drops the `<section id="data-training">` block (lines 3853-
  3855); `<main>` class flips from `grid grid-cols-1 lg:[grid-
  template-columns:24rem_1fr] gap-4` to `flex flex-col gap-4` (or
  whatever single-column shape Studio's existing layouts prefer).
- Footer breadcrumb: optional `The trainer catalog moved to
  Training (top nav). → /training/` line below the timeline.
  Cuts in 1-2 weeks (any operator who lands here looking for the
  pane gets routed once + remembers). Operator's preference §7
  Q7.

### 6.3 Caption — `_studio_training.py` module disposition

Three options for what happens to `_studio_training.py`:

1. **Delete the module wholesale.** The render_training_pane
   function moves into the new `common/html/_training/landing.py`;
   the kind→sheet ID map + display order constants move to
   `common/html/_training/constants.py`. Reads cleanly. Affects
   the 4 import sites:
   - `_studio_routes.py:142, 3765` — landing rendering import
     (drops).
   - Any test that imports from `_studio_training` directly —
     update path.
2. **Keep `_studio_training.py` as a deprecation-shim re-export.**
   Defer-and-flag, against `[[feedback_no_compat_shims]]`. Reject.
3. **Rename to `_training_landing.py` + update imports.** Cheap;
   keeps the file's docstring intact. Less ambitious than (1) but
   doesn't add bug surface area.

Default: (1) — fold into a `_training/` package. Operator confirms
in §7 Q8.

---

## 7. Open questions for operator review

**Updated 2026-05-30 round-4 scope:** swept again — Locks 8/9/10/11
collapse Q16 (TourDestination.secondary_links + L1 InvariantSection
sharing answer it without the registry growing) and Q18 (L2FT
anchor IDs become a contract field on `L2FTExceptionSection`,
not an open question — the bundled markdown source declares them
+ the parser exposes them as `L2FTExceptionSection.sheet_anchor`).
Q19 (dead_metadata implementation) moves to §8.4's cell
description as a BU.3.4 decision-point. Q20 stays (gate semantics
are runtime safety, not registry shape).

**Updated 2026-05-30 round-3 scope:** swept — questions that the
Lock 7 registry locks the answer to are collapsed (Q1 family
taxonomy, Q11 L1/L2 visual distinction, Q15 add_broken_rail
exposure — registry decides). Surviving questions are the ones
the registry can't auto-answer + 3 NEW round-3 questions about
the L2FT Hygiene plants. Defaults are the agent's lean; operator
confirms / flips before BU.2 fires.

1. ~~**Family taxonomy — accept the 5-family grouping?**~~
   **COLLAPSED round-3:** Lock 7 + §0.5 matrix lock the 8-family
   taxonomy as a registry-derived constant. Revising = one row
   edit per kind to change `family`. Not a design question, an
   implementation detail.

2. **Per-card status pill vs no status at all?**
   §1.4 shows `● not planted` / `● planted <hh:mm>` / `⚠ planted
   but reset since` per card. The state-tracking implementation
   (Lock 1's `<prefix>_training_state` KV table) is non-trivial.
   The alternative: no pill, every card just shows the CTAs, the
   operator infers plant state from the dashboard.
   **Default if no override:** ship the pill. The Trainer running
   a live demo wants the visual confirmation that the plant
   landed without bouncing to the dashboard; the implementation
   cost is one KV row per plant.

3. **Per-kind `[ Remove plant ]` — real op or "just runs full
   reset"?**
   §2.2 and §3.6 reference a per-kind remove. The honest
   implementation requires tracking which `transaction_id`s the
   plant inserted + emitting DELETEs, which is fiddly (and the
   plant primitives don't return their row counts today). The
   shortcut: `[ Remove plant ]` always runs the full reset, then
   re-plants every OTHER kind that was active before the remove.
   Same outcome from the operator's POV, simpler implementation.
   **Default if no override:** ship the shortcut. Acceptable
   because reset is fast (~10s) and the multi-plant case is rare
   in early demos. Promote to a real per-kind undo if operators
   complain about the re-plant lag.

4. **In-tour "highlighted plant row" — feasible in App2 iframe?**
   §3.1 / §3.3 reference "the new row is highlighted." Whether
   the App2 dashboard can take a URL parameter highlighting the
   planted row's `account_id` / `transfer_id` is an open
   implementation question (App2 supports filter narrowing via
   URL params, but cell-level highlighting may require a new
   render mode).
   **Default if no override:** drop the highlight from the
   first land. The caption text ("the new row appears at the
   bottom of the table") + the KPI delta chip together are
   enough signal; cell highlighting is polish.

5. **`<prefix>_training_state` persistence across process
   restarts?**
   §1.5 keys the status pill state to the table; truncating it on
   each studio process restart keeps things simple but loses the
   pill state. The operator restarts studio fairly often during
   dev. The alternative: persist the table; on restart, validate
   each row against the demo DB (does the planted account_id
   still exist?) + clear stale rows.
   **Default if no override:** truncate-on-restart for the first
   land. Persistence + validation is BU.7 polish.

6. **Reset re-runs the operator's ETL hook when `cfg.app2.etl_hook`
   is wired — acceptable or surprising?**
   §4.5 documents the implication. A real hook may pull from a
   moving upstream feed; reset re-running the hook may produce
   different data than the Trainer planted against. The
   alternative: snapshot the baseline after the first
   refresh-data on this studio process, then reset restores from
   the snapshot. Costlier; per BS's snapshot/restore deferral,
   probably out of scope.
   **Default if no override:** ship reset-re-runs-hook + the
   side-panel caption that documents the behavior. Real-hook
   deployments are rare on the Trainer surface (`cfg.app2.etl_hook is
   None` is the canonical demo); deferred-snapshot is a follow-on
   if operators hit the moving-feed footgun.

7. **`/data` footer breadcrumb to `/training/` — keep
   permanently or cut after a few weeks?**
   §6.2 proposes a 1-2 week breadcrumb. The alternative: keep it
   forever (cheap, useful for new operators); or cut it
   immediately (clean removal, trust the top-nav). Operator's
   `[[feedback_no_compat_shims]]` posture suggests cut soon.
   **Default if no override:** cut after 2 weeks (the same lock
   pattern BS.3 used when collapsing the Studio container — the
   breadcrumb is a transition aid, not a permanent feature).

8. **`_studio_training.py` disposition — fold into a new
   `_training/` package, or rename in place?**
   §6.3 lays out three options. Default is fold-into-`_training/`-
   package (option 1) because the BU surface will accumulate
   multiple modules (landing, plant per-kind, tour, reset
   plumbing) and one package is cleaner than 4-5 top-level
   `_studio_training_*.py` files. The cost is one round of
   import-path churn.
   **Default if no override:** fold into `_training/` package
   during BU.4 (the cell that touches the most call sites
   anyway).

9. **Plant defaults preview — auto-expanded or collapsed?**
   §2.1's mockup shows `▸ Show defaults' reasoning (4 picks)` as
   default-collapsed. First-time operators may need to see WHY
   the defaults were picked to trust them; default-expanded
   front-loads the explanation. The flip: default-expanded clutters
   the form for return visitors who already know.
   **Default if no override:** default-collapsed + a one-line
   inline caption next to each pre-populated field explaining
   the pick in 1-2 words ("first Rail matching template's role").
   The details block is for the full reasoning; the inline
   caption handles the routine case.

10. **`📺 Tour:` glyph in sub-nav strip — TV emoji or
    alternative?**
    §5.3 uses `📺`. Some users find emoji-in-chrome jarring; the
    alternative is a non-emoji glyph like `▶` (play) or `⊙`
    (eye). Operator may have studio theme considerations.
    **Default if no override:** ship `📺` to start; substitute on
    cold-read v3 if it reads off-tone. The `/etl/` strip uses
    emoji glyphs (`↻`, `⚠`, `🔍`) precedent.

### Round-2 open questions (L1+L2 union)

11. ~~**How prominent should the L1-vs-L2 visual distinction be?**~~
    **COLLAPSED round-3:** §1.2's stripe + heading treatment
    stands; the L2FT Hygiene family slotted in under the L2
    group with the same warm-amber stripe (it's L2-side
    operationally even though it surfaces on a dashboard sheet,
    not /etl/*). One operator decision still pending: should L2FT
    Hygiene have its OWN stripe color to disambiguate from L2
    Triage/Coverage? Default: no — three stripe colors clutter;
    the family header text ("L2FT Hygiene") suffices.

12. **L2 Triage tour iframe — keep the toggle pattern even though
    `/etl/triage` is a list-of-cards (no chart that animates)?**
    The toggle pattern delivers maximum pedagogical value on L1
    dashboards (KPI flips, chart bars appear). On `/etl/triage`,
    Before = empty section, After = a new card. Lower-contrast
    delta. Alternatives: (a) keep the toggle (current §3.7
    mockup); (b) drop the toggle, just show the After state with
    a "this card just appeared because you planted X" banner;
    (c) keep the toggle BUT also deep-link the iframe to auto-
    expand the gap-kind's section (e.g.
    `/etl/triage?expand=unmatched_rail`) so the planted card is
    visible without operator scrolling.
    **Default if no override:** ship (c) — toggle + deep-link
    expand. The toggle reinforces the "plant has an effect"
    mental model even when the delta is subtle, and the deep-link
    expand removes the "where's the card?" friction. Lands a
    small new `?expand=<kind>` query param on
    `/etl/triage` as part of BU.5.

13. **`uncovered_*` tour — should the deep-link force
    `?failures-only=1` engaged, or respect the operator's last-
    used Coverage state?**
    §3.8 mocks `?failures-only=1` always-on. Alternatives:
    (a) always-on (current); (b) respect the operator's last
    state (carries the Coverage page's session state through);
    (c) toggle it ONLY in the After state, leave the Before
    state showing the full list so the trainee sees the "before"
    healthy population.
    **Default if no override:** ship (a) — always-on. The tour is
    a tightly-scripted pedagogical surface; the operator's other
    sessions' Coverage state is irrelevant here. The trainee's
    eye should land on the planted ✗ immediately in both states
    (Before = "look, all green"; After = "look, one ✗"). Burying
    the planted row under 27 ✓s defeats the whole point.

14. **`missing_metadata` plant — pick from dropdown of declared
    required keys, or always omit ALL required keys?**
    §2.6.2's mockup ships "ALL required keys (empty JSON {})" as
    the only option. Alternative: render a dropdown of the
    template's declared `transfer_key` + `metadata_keys`, let the
    operator pick one to omit (or hold the rest). The current
    `_insert_missing_metadata_row` implementation in
    `demo_etl_gaps.py` emits `metadata='{}'` (all keys absent),
    so per-key omission requires implementation work.
    **Default if no override:** ship "all keys omitted" for v1.
    The Triage card's diagnosis ("12 of 14 rows have it — 2
    don't") reads the same whether one key or all keys are
    missing; the trainee's decision is the same; implementation
    cost is meaningful. Promote to per-key picking if cold-read
    surfaces "I wanted to demo a partial-coverage case."

15. ~~**Expose `add_broken_rail_plants`?**~~ **COLLAPSED round-3:**
    Lock 7 doesn't fit bulk-noise plants (no per-kind primitive,
    no tour destination, no operator-controlled knobs beyond
    "count"). Keep internal; track as BV. Same answer as round-2;
    just confirming the registry doesn't change the calculus.

### Round-3 open questions (L2FT Hygiene + registry)

16. ~~**Should Drift Timelines (its own L1 sheet) get a dedicated
    tour, vs subsuming under Drift's tour?**~~ **COLLAPSED
    round-4:** answered by Lock 8 + Lock 7's
    `TourDestination.secondary_links` field. `drift` + `ledger_drift`
    share `InvariantSection["drift"]` + `InvariantSection["ledger_drift"]`
    respectively, and each entry's `TourDestination.secondary_links`
    carries the "Also visible on Drift Timelines →" callout pill.
    Same mechanism handles the dual-surface L2 kinds (phantom_rail
    + L2FT Unmatched Rail). No new design surface — the round-3
    Lock 7 sketch already supported it; round-4 just confirms it's
    the canonical answer.

17. **Today's Exceptions tour — sub-kind navigation?**
    Today's Exceptions is the destination for 7 of the 15 L1
    kinds (chain coherence + expected_eod_balance_breach). When
    the operator tours `chain_parent_disagreement`, the iframe
    lands on Today's Exceptions sheet but the planted row may be
    buried among other check-type rows. Options: (a) iframe scrolls
    to the sheet's KPI + filters by check_type on initial load
    via URL params (requires App2 to honor a `check_type=` query
    param — verify before locking); (b) "What to point out"
    callout instructs operator to manually filter; (c) build a
    per-check-type sub-anchor on the sheet (cheaper than URL
    param if the sheet's table is paginated).
    **Default if no override:** ship (c) — sub-anchors per
    check_type on the Today's Exceptions sheet. Cheap (anchor
    only) + Lock 7 already supports per-kind anchor in the URL
    template.

18. ~~**L2FT Hygiene check anchor IDs — confirm naming?**~~
    **COLLAPSED round-4:** anchor IDs become a typed contract
    field on `L2FTExceptionSection.sheet_anchor` (round-4
    extension to the existing dataclass; defaults to the kind
    slug). Bundled `L2FT_Exceptions.md` declares the anchor per
    section; the L2FT app's populate function reads through the
    parsed sections + emits matching `<a id="..."/>` markers.
    Anti-drift gate: Lock 9 Test 2 (tour-URL liveness) fails if
    the section's anchor doesn't resolve on the rendered sheet.
    Default slugs unchanged (`#chain-orphans` etc.); operator
    decision moves from "approve these slugs" to "edit the
    bundled markdown if you want different ones."

19. ~~**`dead_metadata` plant — DELETE rows or UPDATE the JSON key
    to NULL?**~~ **MOVED round-4:** decision moves into §8.4's
    BU.3.4 cell description (it's an implementation detail of
    the plant function, not a design question — and the cell
    description already cites the round-3 default). Operator
    flips if they prefer a different shape; default stands
    until then.

20. **L2FT Hygiene plants — gate on `cfg.app2.etl_hook is None` like
    the other `demo_etl_gaps.py` plants?**
    The existing 5 L2 plants gate on `etl_hook is None` so a
    real-deploy ETL doesn't get demo overlay corruption. The 4
    new L2FT Hygiene plants live in the same module (per BU.3
    cells); same gate applies by default. But the Trainer is
    explicitly operator-driven — the operator is asking for the
    overlay. Confirm: gate stays + Trainer surface refuses to
    plant L2 / L2FT Hygiene when a real ETL hook is wired
    (refuses with a clear error), OR drop the gate when the
    operator hit `[ Plant + refresh ]` (they ASKED for it).
    **Default if no override:** drop the gate when the request
    came from `/training/plant/<kind>` (the operator's
    intentional action overrides the safety). Keep the gate when
    the plant fires as a side-effect of `/etl/run` POST (the
    current behavior). Side-panel warning on real-hook deploys:
    "Your ETL hook is wired. Planting here overlays demo data
    that your next hook refresh may overwrite. Plant + tour in a
    single session for accurate before/after."

---

## 8. Plant primitives needing build (BU.3 cells)

**Updated 2026-05-30 round-4 scope:** each cell now names the
typed section the registry entry indexes into (per Lock 8). The
L1 cell (BU.3.1) references `InvariantSection["expected_eod_balance_breach"]`
which already exists in `L1_Invariants.md`. The 4 L2FT cells
(BU.3.2-3.5) reference `L2FTExceptionSection[<slug>]` which
already exists in `L2FT_Exceptions.md` (parser ships in
`common/handbook/l2ft_exceptions.py`). The L2 Triage / Coverage
typed catalogue (`L2TriageGapSection`) is BU.2a's deliverable
per Lock 10 — NOT in §8 because the existing L2 Triage / Coverage
plant primitives already exist (the 5 needs-build cells are L1 +
L2FT-only). Each cell also adds parameterized e2e fixture rows
for Lock 9's tests 1-3.

**Updated 2026-05-30 round-3 scope:** new section. The §0.5
coverage matrix flags 5 violation kinds with no plant primitive
today. Each cell below is one BU.3 task: add the plant function +
flip the registry entry from placeholder to real + ship a unit
test.

### 8.1 BU.3.1 — `expected_eod_balance_breach` (L1)

- **Primitive to add:** `ExpectedEodBalanceBreachPlant` dataclass
  in `common/l2/seed.py::ScenarioPlant`.
- **Picker function:** `_pick_eod_balance_breach_inputs` in
  `common/l2/auto_scenario.py`. Heuristic: first internal account
  with a declared `expected_eod_balance` field (per L2
  AccountTemplate); plant a daily_balances row with `balance` ≠
  `expected_eod_balance` for one business day.
- **Form primitives (registry):** account dropdown + days_ago int
  + actual_balance decimal (default = expected ± $100).
- **Tour destination:** L1 Today's Exceptions sheet
  (`#check-type-expected-eod-balance-breach` anchor — Q17 above
  drives the anchor convention).
- **Matview surfaced:** `<prefix>_inv_expected_eod_balance_breach`.
- **Typed source (Lock 8):** `InvariantSection["expected_eod_balance_breach"]`
  — already exists in `L1_Invariants.md`. No new markdown
  authoring needed; registry entry resolves through
  `load_bundled_invariants()`.
- **Lock 9 parameterized e2e fixture row:**
  `dashboard_check=DashboardCheck(matview_name="{prefix}_inv_expected_eod_balance_breach", min_row_count=1)`.
- **Unit test:** assert post-plant matview row count ≥ 1 for the
  planted (account, day). Lock 9 Test 3 makes this a parameterized
  registry-walk test instead.

### 8.2 BU.3.2 — `chain_orphan` (L2FT Hygiene)

- **Primitive to add:** `add_chain_orphan_gap_rows` function in
  `common/l2/demo_etl_gaps.py`. INSERT one parent rail firing
  with a `transfer_id`; emit NO child firing citing that
  transfer_id as `transfer_parent_id`.
- **Picker function (defaults):** `_pick_chain_orphan_inputs` —
  walks `instance.chains` for Required edges where the parent is
  a Rail (not Template — simpler to synthesize) and the child has
  a known leg-shape; picks the first match.
- **Form primitives (registry):** parent_rail dropdown (cascaded
  from declared Required chains' parents) + child_rail/template
  dropdown (cascaded from picked parent's edges) + days_ago int.
- **Tour destination:** L2FT L2 Hygiene Exceptions sheet
  `#chain-orphans` anchor — sourced from
  `L2FTExceptionSection["chain_orphans"].sheet_anchor` per the
  round-4 Q18 collapse.
- **Matview surfaced:** `l2ft_exc_chain_orphans` (queried
  per-request, not a real matview — same shape).
- **Typed source (Lock 8):** `L2FTExceptionSection["chain_orphans"]`
  — already exists in `L2FT_Exceptions.md`. Registry entry
  resolves through `load_bundled_l2ft_exceptions()`.
- **Lock 9 parameterized e2e fixture row:**
  `dashboard_check=DashboardCheck(query_kind="l2ft_check", check_name="chain_orphans", min_row_count=1)`.
- **Unit test:** assert the per-check dataset returns ≥ 1 row for
  the planted (parent_name, child_name). Lock 9 Test 3 makes
  this parameterized.

### 8.3 BU.3.3 — `dead_bundles_activity` (L2FT Hygiene)

- **Primitive to add:** `add_dead_bundles_activity_gap_rows` in
  `demo_etl_gaps.py`. DELETE all
  `<prefix>_transactions` rows for one declared
  `Rail.bundles_activity` target rail (so the aggregating rail
  has no children to bundle).
- **Picker function (defaults):**
  `_pick_dead_bundles_activity_inputs` — walks `instance.rails`
  for any rail with a non-empty `bundles_activity`; picks the
  alphabetically-last (aggregating_rail, bundle_target) pair.
- **Form primitives (registry):** aggregating_rail dropdown +
  bundle_target dropdown (cascaded). DELETE-shape plant — destructive
  warning panel per the §2.6.3 `uncovered_rail` template.
- **Tour destination:** L2FT L2 Hygiene Exceptions sheet
  `#dead-bundles` anchor — sourced from
  `L2FTExceptionSection["dead_bundles_activity"].sheet_anchor`.
- **Matview surfaced:** `l2ft_exc_dead_bundles_activity`.
- **Typed source (Lock 8):** `L2FTExceptionSection["dead_bundles_activity"]`
  — already exists.
- **Lock 9 parameterized e2e fixture row:**
  `dashboard_check=DashboardCheck(query_kind="l2ft_check", check_name="dead_bundles_activity", min_row_count=1)`.
- **Unit test:** assert per-check dataset returns the planted
  pair. Lock 9 Test 3 parameterizes.

### 8.4 BU.3.4 — `dead_metadata` (L2FT Hygiene)

- **Primitive to add:** `add_dead_metadata_gap_rows` in
  `demo_etl_gaps.py`. **Implementation choice (was Q19,
  collapsed to here in round-4):** default to additive INSERTs of
  rows on the targeted rail with `metadata='{}'` so the (rail,
  metadata_key) check finds zero non-null carriers. Keeps the
  DELETE blast-radius zero; same pattern as
  `add_phantom_rail_gap_rows`. Two alternatives (DELETE rows
  carrying the key OR UPDATE the JSON object to strip the key)
  are equivalent at the check level but the additive shape is
  the lowest-blast-radius option. Operator flips if they prefer
  a different shape.
- **Picker function (defaults):** `_pick_dead_metadata_inputs` —
  walks `instance.rails` for any rail with non-empty
  `metadata_keys`; picks the first (rail, key) pair.
- **Form primitives (registry):** rail dropdown + metadata_key
  dropdown (cascaded) + count int (default 3).
- **Tour destination:** L2FT L2 Hygiene Exceptions sheet
  `#dead-metadata` anchor — sourced from
  `L2FTExceptionSection["dead_metadata_declarations"].sheet_anchor`.
  Note the slug mismatch: registry kind is `dead_metadata`;
  L2FTExceptionSection key is `dead_metadata_declarations`
  (per the existing parser's slug-from-title derivation). Lock 9
  Test 1's `canonical_section_kind` mapping handles the
  discrepancy.
- **Matview surfaced:** `l2ft_exc_dead_metadata`.
- **Typed source (Lock 8):** `L2FTExceptionSection["dead_metadata_declarations"]`
  — already exists. Renderer resolves the slug-mismatch via the
  registry's `section_kind: str | None = None` override field
  (defaults to `entry.kind`; non-None when the section's slug
  diverges).
- **Lock 9 parameterized e2e fixture row:**
  `dashboard_check=DashboardCheck(query_kind="l2ft_check", check_name="dead_metadata", min_row_count=1)`.
- **CRITICAL — direction note in plant page caption:** "This is
  the OPPOSITE direction from L2 Triage's `missing_metadata_key`
  plant. There, the ETL omitted a key the L2 declares; here, the
  L2 declares a key the ETL never emits."
- **Unit test:** assert per-check dataset returns the planted
  (rail_name, metadata_key) pair. Lock 9 Test 3 parameterizes.

### 8.5 BU.3.5 — `dead_limit_schedule` (L2FT Hygiene)

- **Primitive to add:** `add_dead_limit_schedule_gap_rows` in
  `demo_etl_gaps.py`. DELETE every outbound Debit posting matching
  one (parent_role, rail) LimitSchedule cell.
- **Picker function (defaults):**
  `_pick_dead_limit_schedule_inputs` — walks
  `instance.limit_schedules` for any cell with current outbound
  flow; picks the alphabetically-first.
- **Form primitives (registry):** parent_role dropdown +
  rail_name dropdown (cascaded to declared LimitSchedule cells).
  DELETE-shape; destructive warning panel.
- **Tour destination:** L2FT L2 Hygiene Exceptions sheet
  `#dead-limits` anchor — sourced from
  `L2FTExceptionSection["dead_limit_schedules"].sheet_anchor`.
- **Matview surfaced:** `l2ft_exc_dead_limit_schedules`.
- **Typed source (Lock 8):** `L2FTExceptionSection["dead_limit_schedules"]`
  — already exists.
- **Lock 9 parameterized e2e fixture row:**
  `dashboard_check=DashboardCheck(query_kind="l2ft_check", check_name="dead_limit_schedules", min_row_count=1)`.
- **Unit test:** assert per-check dataset returns the planted
  (parent_role, rail_name, cap) triple. Lock 9 Test 3
  parameterizes.

Each cell is self-contained — one plant function + one registry
row update + one parameterized fixture row + the typed section
already exists per Lock 8. BU.3 can parallelize across all 5
cells. After BU.3 lands, all 21 registry entries' `plant_function`
point at real implementations; no placeholder pages remain; Lock
9 Test 1 (bijectivity) gates that every kind has a typed-section
resolution.

---

*End of mockups. Pass to operator for review; flip any of the §7
open questions (Q17, Q20 are the round-4 survivors; Q16, Q18, Q19
collapsed by the round-4 typed-source locks); then BU.1 (vertical
slice — phantom_rail end-to-end through Locks 7 + 8 + 9 + 10) is
the first cell to land per BU.0's round-4 sequencing, followed by
BU.2a (L2TriageGapSection build) → BU.2b (full registry + shared
render shell).*
