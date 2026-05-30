# BTa design mockups

> **Status:** DRAFT 2026-05-30. First-cut agent design pass per
> `[[feedback_agent_driven_design_works]]`. Briefed against
> `docs/audits/bta_0_replan.md` (locks 1-4) +
> `docs/audits/bt_cold_read.md` §7-9 (with operator's inline triage
> marks) + the BT cold-read screenshots under
> `docs/audits/bt_cold_read_screenshots/`. Drives BTa.1-BTa.6
> implementation; resolves all referenced design questions or
> escalates them in §7.

---

## 0. Headline + lock reminders

Phase BTa addresses 18 of 20 BT cold-read recommendations across the
three ETL Support pages (`/etl/`, `/etl/run`, `/etl/triage`,
`/etl/probe`) plus the L2 editor round-trip. The shape of the
follow-on is dominated by four cross-cutting design decisions locked
in BTa.0 (`bta_0_replan.md`) before this mockup pass fired. Every
section below cites which lock(s) it consumes.

The intent: turn the BT-era *"three tools floating on a landing
page, each one a context-free hop"* into a *"numbered Refresh →
Triage → Probe loop with deep links forward, breadcrumb back, and a
glossary-on-demand side panel that absorbs the per-field help, the
chain-diagram, and the term definitions in one shared chrome."*
After BTa lands, a first-time ETL engineer's loop is: land →
"Refresh Data" → open the one accordion section that's red → click
the per-card CTA → land in the right L2 editor entity → fix → use
the sticky "← Back to Triage" breadcrumb → re-Refresh.

### Locks recap

| # | Lock                         | Shape                                                                                 | Section here       |
|---|------------------------------|---------------------------------------------------------------------------------------|--------------------|
| 1 | Side-panel drawer            | Right-edge slide-out, ~30-35% viewport, `hx-get` fragments, dismissable               | §2 (foundation)    |
| 2 | Numbered landing + tutorial  | 3 numbered cards + `→` arrows + dismissable "First time here?" banner with 5-step    | §1                 |
| 3 | Triage group-by              | 4 accordion sections per gap kind (default-collapsed), sub-sort by row count DESC    | §3                 |
| 4 | Back-breadcrumb              | `?from=/etl/...` query-string carryover, validated, survives POST→redirect           | §5 (Probe) + flow  |

Full lock rationale + rejected variants: `docs/audits/bta_0_replan.md`.

---

## 1. Numbered landing + tutorial banner (BTa.3)

**Consumes:** Lock 2.
**Before:** `bt_cold_read_screenshots/01_etl_landing.png` (3 equal
cards, tool-shaped subtitles, no sequence signal, "wipe" buried in a
parenthetical on the Run card).

### 1.1 Before — current landing

```
┌──────────────────────────────────────────────────────────────────────┐
│ Recon-Gen │ L2 Editor │ ETL Support [●] │ Training │ ...             │
├──────────────────────────────────────────────────────────────────────┤
│ Studio · ETL Support                              qsgen-sqlite       │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                │
│  │  Probe       │  │  Run         │  │  Triage      │                │
│  │              │  │              │  │              │                │
│  │ Investigate  │  │ Execute the  │  │ Find + fix   │                │
│  │ one L2 slice │  │ ETL pipeline │  │ gaps — diff  │                │
│  │ — pick a     │  │ (wipe → hook │  │ declared     │                │
│  │ rail, ...    │  │ → matview    │  │ contracts... │                │
│  └──────────────┘  └──────────────┘  └──────────────┘                │
└──────────────────────────────────────────────────────────────────────┘
```

Cold-read findings driving the redesign: P2.1 ("reads as three
tools, not a workflow"), workflow.1 ("first-time tutorial path"),
P1.3 ("tell me whose hook ran" — surfaces as the Refresh-card
description copy).

### 1.2 After — numbered cards + dismissable banner (collapsed)

```
┌──────────────────────────────────────────────────────────────────────┐
│ Recon-Gen │ L2 Editor │ ETL Support [●] │ Training │ ...    [ ? ]    │
├──────────────────────────────────────────────────────────────────────┤
│ Studio · ETL Support                              qsgen-sqlite       │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌────────────────────────────────────────────────────────────┬───┐  │
│  │ ● First time here?                                         │ ✕ │  │
│  │   Walk the Refresh → Triage → Probe loop.                  └───┤  │
│  │   ▸ Show me how                                                │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  ┌──────────────┐      ┌──────────────┐      ┌──────────────┐        │
│  │ 1. Refresh   │  →   │ 2. Triage    │  →   │ 3. Probe     │        │
│  │    Data      │      │              │      │              │        │
│  │              │      │ 4 gaps after │      │ Investigate  │        │
│  │ Run your ETL │      │ last refresh │      │ one L2 slice │        │
│  │ hook + score │      │ — open each  │      │ to compare   │        │
│  │ coverage     │      │ accordion to │      │ L2 contract  │        │
│  │              │      │ see cards    │      │ vs runtime   │        │
│  │ last: 14:23  │      │              │      │              │        │
│  │ ● success    │      │ ⚠ 4 gaps     │      │              │        │
│  └──────────────┘      └──────────────┘      └──────────────┘        │
│                                                                      │
│  Coverage report green = ETL contract satisfied.                     │
│  Not green? Open Triage to find the specific gaps.                   │
└──────────────────────────────────────────────────────────────────────┘
```

Operator-facing strings (new):
- Banner: `First time here? Walk the Refresh → Triage → Probe loop.`
- Banner link: `▸ Show me how` (chevron, click-to-expand inline).
- Banner dismiss: `✕` (top-right, `aria-label="Dismiss tutorial"`).
- Card titles: `1. Refresh Data` / `2. Triage` / `3. Probe`
  (numerals are part of the title, not a separate badge — they read
  as a step label, not chrome).
- Status pill on card 1: `last: <ISO timestamp>` + `● success` /
  `● halted` / `last: never`.
- Status pill on card 2: `⚠ N gaps` (warning color when N>0,
  `● 0 gaps` muted-success when N=0, `— not checked yet` muted when
  there's no prior Refresh).
- Top-nav `[ ? ]` button: opens the global glossary side panel
  (§2.b). Lives in the top-nav, visible site-wide.

### 1.3 After — banner expanded (5-step inline checklist)

```
│  ┌────────────────────────────────────────────────────────────┬───┐  │
│  │ ● First time here?                                         │ ✕ │  │
│  │   Walk the Refresh → Triage → Probe loop.                  └───┤  │
│  │   ▾ Show me how                                                │  │
│  │                                                                │  │
│  │   1.  ☐ Click "Refresh Data" (card 1). This wipes the demo    │  │
│  │           DB, runs the ETL hook, refreshes matviews. ~10s.    │  │
│  │   2.  ☐ Read the coverage report. Most rails / templates /    │  │
│  │           chains should be green ✓. Red ✗ = a gap.            │  │
│  │   3.  ☐ Open Triage (card 2). Each accordion section is one   │  │
│  │           kind of gap. Open the biggest section first.        │  │
│  │   4.  ☐ Click a card's CTA — it deep-links to the L2 editor   │  │
│  │           for that entity. Fix the rail / template / limit.   │  │
│  │   5.  ☐ Use "← Back to Triage" to return; click "Refresh      │  │
│  │           Data" again to confirm the gap is gone.             │  │
│  │                                                                │  │
│  │   (Card 3 — Probe — is for investigating a specific slice     │  │
│  │   when a gap is unclear; skip on first pass.)                 │  │
│  └────────────────────────────────────────────────────────────────┘  │
```

Operator-facing strings: each step is a sentence-case checkbox label
ending with a period; numerals match the card numerals. Checkboxes
are not persistent — they're a visual aid for "where am I in this
list right now," same role as a recipe's step list. The
parenthetical at the bottom answers cold-read §6a's "which one do I
click first" — Probe explicitly demoted to "skip on first pass."

### 1.4 Captions

- **Dismissal persistence:** `localStorage` key
  `recon-gen-etl-tutorial-dismissed:<deployment_name>` (per BTa.0
  Lock 2). Re-shown on a new deployment so the engineer onboarding
  to "prod" sees it even after dismissing on the dev fixture.
- **Per-step card status:**
  - Card 1 `last: <ts>` reads from the same `last_run` info the
    BT-era page already shows; status pill (`● success` /
    `● halted`) maps to existing pipeline exit code.
  - Card 2 `⚠ N gaps` reads from `detect_gaps(contracts, db)` (the
    BT.4 helper). Cached for the landing page; force-refreshes on
    next Refresh-Data click. Zero gaps → `● 0 gaps` muted-success.
  - Card 3 has no status pill (Probe is an investigation tool, not
    a state — there's no "last probe" coverage signal worth
    surfacing on the landing).
- **The arrows:** `→` between cards is a literal Unicode glyph,
  styled `text-secondary-fg text-2xl`, not an SVG. Cheaper to
  render, scales to RTL automatically if/when that ever matters.

---

## 2. Side-panel pattern (BTa.1)

**Consumes:** Lock 1 (foundation cell — lands first per BTa.0
sequencing).
**Before:** N/A — this is a new surface. Operator currently has no
help affordance; questions about `Rail` / `Slice` / `LimitSchedule`
require reading the L2 yaml or the SPEC.

The side panel is one drawer rendered per page; three trigger
shapes consume it (per-field help, global glossary, entity diagram).
Same chrome, same `hx-get` fetch pattern, three different content
URLs.

### 2.a Per-field help — `?` trigger next to a form label

Trigger placement: an inline `?` icon button immediately after a
Probe form label (or any field label where "what does this mean?"
applies). One sentence definition + an example.

```
┌──────────────────────────────────────────────┬───────────────────────┐
│ Studio · ETL · Probe          qsgen-sqlite   │   ✕  Help: Rail       │
├──────────────────────────────────────────────├───────────────────────┤
│                                              │                       │
│ Pick a slice of the L2 to probe:             │   A Rail is a single  │
│                                              │   payment-rail        │
│ ┌──────────┬───────────────────┬──────────┐  │   primitive — one     │
│ │ ◉ Rail [?]  ○ Transfer Tmpl [?] ○ Chain[?]│   "kind of money      │
│ └──────────┴───────────────────┴──────────┘  │   movement," like an  │
│             ^^^                              │   ACH credit or a     │
│             one click here opens the         │   wire transfer.      │
│             drawer with "what's a Rail?"     │                       │
│                                              │   Each Rail in the    │
│ Rail name:  [ — pick one — ▼ ]               │   L2 declares an      │
│                                              │   expected            │
│ Window: [Last 30d] [Last 90d] [All time ✓]   │   account_role set    │
│         From [ ] To [ ]   [Apply]            │   and a metadata-key  │
│                                              │   contract.           │
├──────────────────────────────────────────────│                       │
│ EXPECTED (from L2)    │ OBSERVED (window)    │   Example: in this    │
├───────────────────────┼──────────────────────│   L2, "ACHCredit" is  │
│ ...                   │ ...                  │   a Rail; rows with   │
│                                              │   rail_name='ACH      │
│                                              │   Credit' must hit    │
│                                              │   account_role ∈      │
│                                              │   {Customer,External} │
│                                              │   and carry a         │
│                                              │   metadata.trace_id.  │
│                                              │                       │
│                                              │   → See all Rails in  │
│                                              │     this L2           │
│                                              │     (/l2_shape/rail/) │
│                                              │                       │
└──────────────────────────────────────────────┴───────────────────────┘
```

Drawer opens from the right; takes ~30-35% of viewport width per
Lock 1. Page content stays put behind it (no shift / no overlay
darkening — the drawer pushes the page narrower or floats over with
a subtle backdrop, mockup intentionally noncommittal on which until
implementation). `✕` top-right dismisses; Escape also dismisses;
focus returns to the trigger `?` per Lock 1's focus-trap
requirement.

Operator-facing strings:
- Drawer title: `Help: <Term>` (e.g. `Help: Rail`,
  `Help: Transfer Template`, `Help: Chain`).
- Trigger button: bare `?` with `aria-label="Help: <Term>"` so
  screen readers announce context.
- Closing affordances: `✕` (visual), `Escape` (keyboard), click
  outside the drawer (mouse).

### 2.b Glossary — global `[?]` in top nav

The same drawer, opened from the site-wide `[?]` button in the top
nav. Content is a collapsible list of all defined terms, alphabetical.

```
┌──────────────────────────────────────────────┬───────────────────────┐
│ Recon-Gen │ ... │ ETL Support [●] │ ...  [?] │   ✕  Glossary         │
├──────────────────────────────────────────────├───────────────────────┤
│                                              │                       │
│ Studio · ETL · Triage          qsgen-sqlite  │   ▸ Chain             │
│                                              │   ▸ Coverage          │
│ 4 gap kinds · last refresh 14:23             │   ▸ Hook              │
│                                              │   ▾ L2                │
│ ▶ Unmatched rail_name • 47 cards • 256 rows  │      The YAML config  │
│ ▶ Unmatched template_name • 12 cards • 39 r  │      that declares    │
│ ▶ Missing LimitSchedule • 8 cards • 142 rows │      your institution │
│ ▶ Missing metadata key • 4 cards • 23 rows   │      's shape:        │
│                                              │      accounts,        │
│                                              │      payment rails,   │
│                                              │      transfer        │
│                                              │      templates,       │
│                                              │      chains, limit    │
│                                              │      schedules.       │
│                                              │      Recon-Gen        │
│                                              │      validates your   │
│                                              │      data AGAINST     │
│                                              │      what the L2      │
│                                              │      declares.        │
│                                              │                       │
│                                              │      (Not to be       │
│                                              │      confused with    │
│                                              │      "L2 Flow         │
│                                              │      Tracing" — the   │
│                                              │      dashboard tab    │
│                                              │      with the same    │
│                                              │      L2 in its name.) │
│                                              │   ▸ LimitSchedule     │
│                                              │   ▸ Matview           │
│                                              │   ▸ Rail              │
│                                              │   ▸ Singleton         │
│                                              │   ▸ Slice             │
│                                              │   ▸ Transfer Template │
└──────────────────────────────────────────────┴───────────────────────┘
```

Operator-facing strings:
- Top-nav trigger: `[?]` button with
  `aria-label="Open glossary"`.
- Drawer title: `Glossary`.
- Terms list (initial seed — operator confirms / extends in §7):
  `Chain`, `Coverage`, `Hook`, `L2`, `LimitSchedule`, `Matview`,
  `Rail`, `Singleton`, `Slice`, `Transfer Template`.
- Each term: chevron-toggle (`▸` collapsed / `▾` expanded), term
  name bold, definition in ~3 sentences max.

The cold-read's vocabulary table (`bt_cold_read.md` §7 "Non-landing
concept names") is the seed for which terms ship; the inline
parenthetical on `L2` directly addresses the cold-read's collision
flag with the `L2 Flow Tracing` dashboard tab.

### 2.c Entity diagram — chain parent→child visual

Triggered from the Probe page's Chain section via a "view diagram"
link. Drawer renders a small SVG arrow diagram for the chain.

```
┌──────────────────────────────────────────────┬───────────────────────┐
│ Studio · ETL · Probe          qsgen-sqlite   │   ✕  Chain diagram:   │
├──────────────────────────────────────────────│      ACHOrigination   │
│ ...                                          │      DailySweep       │
│                                              ├───────────────────────┤
│ EXPECTED (from L2)                           │                       │
├──────────────────────────────────────────────│   ┌─────────────┐     │
│ parent             =   ACHOrigination        │   │ ACHOrigin-  │     │
│                        DailySweep            │   │ ationDaily  │     │
│ child              =   ConcentrationTo       │   │ Sweep       │     │
│                        FRBSweep              │   │ (parent)    │     │
│ kind [?]           =   Required (singleton)  │   └──────┬──────┘     │
│ transfer_parent_id ≠   NULL                  │          │            │
│                                              │   transfer_parent_id  │
│ → Edit in L2                                 │          │            │
│ → View chain diagram                         │          ▼            │
│   ^^^                                        │   ┌─────────────┐     │
│   one click opens drawer →                   │   │ Concentrat- │     │
│                                              │   │ ionToFRB    │     │
│                                              │   │ Sweep       │     │
│                                              │   │ (child)     │     │
│                                              │   │             │     │
│                                              │   │ kind:       │     │
│                                              │   │ Required    │     │
│                                              │   │ singleton — │     │
│                                              │   │ exactly one │     │
│                                              │   │ child per   │     │
│                                              │   │ parent      │     │
│                                              │   └─────────────┘     │
│                                              │                       │
│                                              │   Other kinds:        │
│                                              │   ・ Optional         │
│                                              │     singleton         │
│                                              │   ・ Required fanout  │
│                                              │   ・ Optional fanout  │
│                                              │                       │
│                                              │   → Edit chain        │
│                                              │     definition in L2  │
└──────────────────────────────────────────────┴───────────────────────┘
```

Operator-facing strings:
- Trigger: `→ View chain diagram` (link, right under
  `→ Edit in L2` on the Probe Expected panel).
- Drawer title: `Chain diagram: <chain_name>`.
- Diagram caption: the `kind` definition is inlined under the child
  box so the diagram + the definition are co-located (no need to
  re-open glossary).
- "Other kinds" enum: addresses cold-read §3e ("'kind = Required
  (singleton)' reads like good L2 vocabulary but I don't know what
  alternatives exist").

### 2.d Helper signature sketch (for BTa.1 implementation)

```python
# src/recon_gen/common/html/_studio_side_panel.py

def render_side_panel_trigger(
    target_url: str,
    label: str = "?",
    aria_label: str = "Open help",
) -> str:
    """Inline button that opens the drawer; hx-get to target_url."""

def render_side_panel_drawer_container() -> str:
    """One per page; hx-target for all triggers on the page."""

def side_panel_javascript_snippet() -> str:
    """Escape key handler + focus trap + click-outside dismiss."""

def side_panel_css_classes() -> str:
    """Drawer chrome — 30-35% width, slide-in animation, ARIA."""

# Content-fragment helpers (one per use case):
def glossary_fragment(term: str | None = None) -> str: ...
def entity_help_fragment(entity_kind: str, entity_id: str) -> str: ...
def field_help_fragment(field_id: str) -> str: ...
```

The three fragment helpers are the content side; the four chrome
helpers are the shell. BTa.4 / BTa.5 / BTa.6 each pass their own
trigger URL pointing at one of the fragment helpers.

---

## 3. Triage rework (BTa.4)

**Consumes:** Lock 3 (group-by-kind accordions) + Lock 1 (the
"What does the L2 declare?" side-panel sub-trigger) + Lock 4 (deep
links carry `?from=` for the round-trip).
**Before:** `bt_cold_read_screenshots/04_etl_triage_initial.png` +
`09/10/11_triage_*_cards.png` (60 cards stacked flat, all visually
identical red ⚠ headers, `declared_rails:` repeated in every card —
operator's noise complaint).

### 3.1 Before — current Triage

```
┌──────────────────────────────────────────────────────────────────────┐
│ Studio · ETL · Triage                       qsgen-sqlite             │
├──────────────────────────────────────────────────────────────────────┤
│ 60 gaps detected · last triage 2026-05-30 14:23                      │
│                                                                      │
│ ┌──────────────────────────────────────────────────────────────────┐ │
│ │ ⚠ Unmatched rail_name                                            │ │
│ │ 256 rows arrived with rail_name='InternalBalanceMaintenance'...  │ │
│ │                                                                  │ │
│ │ declared_rails: [ACHCredit, ACHDebit, Wire, Check, Sweep, ATM,   │ │
│ │   ...30 names total, repeated in every single card...]           │ │
│ │                                                                  │ │
│ │ sample: { "tx-001": {...}, "tx-002": {...}, ...5 rows as JSON } │ │
│ │ [ Open Rails editor ]                                            │ │
│ └──────────────────────────────────────────────────────────────────┘ │
│ ┌──────────────────────────────────────────────────────────────────┐ │
│ │ ⚠ Unmatched rail_name                  ← same red ⚠ as above     │ │
│ │ 8 rows arrived with rail_name='ach'... ← but this is 8 rows      │ │
│ │ declared_rails: [...same 30 names again...]                      │ │
│ │ sample: {...JSON...}                                             │ │
│ │ [ Open Rails editor ]                                            │ │
│ └──────────────────────────────────────────────────────────────────┘ │
│ ... 58 more cards in this shape ...                                  │
└──────────────────────────────────────────────────────────────────────┘
```

Cold-read findings: P2.7 (declared_rails is 30 names × 60 cards of
pure noise), P2.8 (no volume badges — 256 rows reads identical to 8
rows in the title), P3.5 (4 gap kinds visually indistinguishable),
P3.6 (JSON sample blocks should be columnar), P1.4 (CTAs deep-link
to L2 editor home, not the entity), P1.5 (no breadcrumb back).

### 3.2 After — accordion sections, collapsed (first paint)

```
┌──────────────────────────────────────────────────────────────────────┐
│ Studio · ETL · Triage                       qsgen-sqlite             │
├──────────────────────────────────────────────────────────────────────┤
│ 4 gap kinds · 71 gaps total · 460 rows affected                      │
│ last triage 2026-05-30 14:23 · [ ↻ Re-check ]                        │
│                                                                      │
│ ┌──────────────────────────────────────────────────────────────────┐ │
│ │ ▶ ▎ ⚠ Unmatched rail_name        • 47 cards • 256 rows total     │ │
│ │   ▎    (color stripe — orange)                                    │ │
│ └──────────────────────────────────────────────────────────────────┘ │
│ ┌──────────────────────────────────────────────────────────────────┐ │
│ │ ▶ ▎ ⚠ Unmatched template_name    • 12 cards • 39 rows total      │ │
│ │   ▎    (color stripe — purple)                                    │ │
│ └──────────────────────────────────────────────────────────────────┘ │
│ ┌──────────────────────────────────────────────────────────────────┐ │
│ │ ▶ ▎ ⚠ Missing LimitSchedule [?]  • 8 cards • 142 rows total      │ │
│ │   ▎    (color stripe — teal)                                      │ │
│ └──────────────────────────────────────────────────────────────────┘ │
│ ┌──────────────────────────────────────────────────────────────────┐ │
│ │ ▶ ▎ ⚠ Missing metadata key       • 4 cards • 23 rows total       │ │
│ │   ▎    (color stripe — slate)                                     │ │
│ └──────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
```

All 4 sections default-collapsed (Lock 3) — first paint is 4 rows
of summary, not 60 cards of noise. The page now opens to "here are
your four kinds of problem, biggest first by row count, pick one to
investigate" — the operator's first action is one click on one
section.

Operator-facing strings:
- Page header: `4 gap kinds · 71 gaps total · 460 rows affected`
  (the BT-era `60 gaps detected` count is replaced by a kind /
  card / row triple).
- Section header: `▶ ⚠ <Kind> • N cards • M rows total` —
  chevron is the affordance, ⚠ stays for at-a-glance warning, kind
  name + cards count + rows count is the prioritization signal.
- Section sort: by `M rows total` DESC (Lock 3); kinds with more
  rows-affected float to the top.
- `[?]` next to `LimitSchedule`: opens the glossary entry inline
  (per Lock 1 / §2.b) — addresses cold-read 5c "I don't know what
  LimitSchedule does."

### 3.3 After — one section expanded

```
│ ┌──────────────────────────────────────────────────────────────────┐ │
│ │ ▾ ▎ ⚠ Unmatched rail_name        • 47 cards • 256 rows total     │ │
│ │   ▎                                                               │ │
│ │   ▎ ▸ What does the L2 declare? (30 rails)                        │ │
│ │   ▎                                                               │ │
│ │   ▎ ┌────────────────────────────────────────────────────────┐    │ │
│ │   ▎ │ ▎ ⚠ "ach" • 87 rows                                    │    │ │
│ │   ▎ │ ▎                                                       │    │ │
│ │   ▎ │ ▎ 87 rows arrived with rail_name="ach" but the L2 has   │    │ │
│ │   ▎ │ ▎ no Rail of that name. Closest declared: ACHCredit,    │    │ │
│ │   ▎ │ ▎ ACHDebit.                                             │    │ │
│ │   ▎ │ ▎                                                       │    │ │
│ │   ▎ │ ▎ Sample rows (3 of 87):                                │    │ │
│ │   ▎ │ ▎  column          tx-13422       tx-13501  tx-13509    │    │ │
│ │   ▎ │ ▎  ───────────     ───────────    ────────  ─────────   │    │ │
│ │   ▎ │ ▎  rail_name       ach            ach       ach         │    │ │
│ │   ▎ │ ▎  account_role    CustomerLedger CustDDA   ExtCorr     │    │ │
│ │   ▎ │ ▎  posted_at       2026-05-30     2026-05-  2026-05-30  │    │ │
│ │   ▎ │ ▎                  09:14          30 09:18  09:22       │    │ │
│ │   ▎ │ ▎  metadata.tr...  abc-001        abc-002   abc-003     │    │ │
│ │   ▎ │ ▎  + 1 row metadata key shown · + 84 more rows           │    │ │
│ │   ▎ │ ▎                                                       │    │ │
│ │   ▎ │ ▎ [ → Add "ach" to Rails ]   [ Hide this card ]         │    │ │
│ │   ▎ │ ▎  ^^ deep link:                                         │    │ │
│ │   ▎ │ ▎  /l2_shape/rail/new?name=ach&from=/etl/triage          │    │ │
│ │   ▎ └────────────────────────────────────────────────────────┘    │ │
│ │   ▎                                                               │ │
│ │   ▎ ┌────────────────────────────────────────────────────────┐    │ │
│ │   ▎ │ ▎ ⚠ "InternalBalanceMaintenance" • 47 rows              │    │ │
│ │   ▎ │ ▎ ... (cards sorted by row count DESC within the section)│   │ │
│ │   ▎ └────────────────────────────────────────────────────────┘    │ │
│ │   ▎ ... 45 more cards in this section ...                         │ │
│ └──────────────────────────────────────────────────────────────────┘ │
```

Three changes at the card level:
1. **Volume badge in the title**: `⚠ "<name>" • <N> rows`. Cold-read
   P2.8 — the title now carries the priority signal.
2. **Per-kind color stripe (left edge) + the section's icon**: ⚠
   stays as the icon (universal); the stripe color is the kind
   discriminator (see §3.4 below). Cold-read P3.5 — accessible
   because operator gets *all* of: icon, kind name in the section
   header, stripe color, and the diagnosis sentence's vocabulary.
3. **Columnar sample table** (the "+1 row metadata key shown" /
   "+84 more rows" footnotes scope what's truncated): cold-read P3.6.
   Columns are transposed — rows are field names, columns are
   sample tx IDs — so 5 fields × 3 samples fits a card without
   horizontal scroll.

The **"What does the L2 declare?" sub-panel** at the section header
(collapsible, default collapsed) holds the `declared_rails: [...]`
list that was the noise complaint. It appears once per section, not
once per card.

Operator-facing strings:
- Section sub-panel title: `▸ What does the L2 declare? (30 rails)`
  — chevron + parenthetical count. Click expands an inline
  comma-list of the 30 names (no further navigation).
- Card title: `⚠ "<name>" • <N> rows` — quotes around the
  ETL-provided value to signal "this is what your data said," row
  count is the priority anchor.
- Diagnosis sentence: stays close to the BT-era prose but
  templatized per kind (Unmatched rail/template/limit-triple/key);
  always names the row count + the value + the L2's closest
  alternatives.
- Sample table caption: `Sample rows (<shown> of <total>):` (the
  metadata-key truncation footnote — `+1 row metadata key shown`
  — flags that a wide sample row may have more fields than shown).
- Card CTA: `[ → <kind-specific verb> "<value>" ]` — e.g.
  `[ → Add "ach" to Rails ]` (if `ach` doesn't exist),
  `[ → Edit "InternalBalanceMaintenance" rail ]` (if it does but
  has a contract gap). The verb mismatch (Add vs Edit) is the
  operator's decision signal — "do I create or amend?" answered
  by the CTA's text.
- Card CTA URL shape: `<editor_url>?from=/etl/triage` (Lock 4).
- Card secondary action: `[ Hide this card ]` (per-card, scopes a
  triage session; in-memory only, cleared on next Refresh).

### 3.4 Per-kind color/icon table

Cold-read P3.5: "distinct color/icon per gap kind — keep it
accessible." Proposed (operator confirms in §7):

| Kind                       | Stripe color | Section icon | Glyph alternate | Notes                                            |
|----------------------------|--------------|--------------|-----------------|--------------------------------------------------|
| `Unmatched rail_name`      | orange       | ⚠            | ▎▎              | rails are the most common kind → warm color     |
| `Unmatched template_name`  | purple       | ⚠            | ▎▎▎             | templates are multi-leg → cooler color          |
| `Missing LimitSchedule`    | teal         | ⚠            | ▎▎▎▎            | distinct from rail/template; teal = not red/grn |
| `Missing metadata key`     | slate        | ⚠            | ▎▎▎▎▎           | metadata is structural; muted = "fix-but-not-fire"|

Accessibility notes:
- Icon stays ⚠ across all 4 kinds (operator's "keep it accessible"
  — color isn't load-bearing on its own).
- Section header kind-name + cards/rows triple is the screen-reader
  signal; stripe color + glyph alternate (the `▎` repetitions, used
  in the wireframes above as left-edge stripes) reinforce.
- All four colors pass WCAG AA on the standard Studio bg-surface
  background (orange #C44E10, purple #5E4694, teal #0E7C7B,
  slate #4A5568 — exact tokens picked in implementation against the
  studio theme; mockup shows hue intent).
- Operator pushback expected on the specific colors — see §7.

### 3.5 Caption — data sources

- `detect_gaps(contracts, db)` (already exists, BT.4) supplies the
  raw list of gaps with their kind tags. New code: group by `kind`,
  sub-sort by `len(evidence.rows)` DESC, render with the accordion
  structure.
- Sample columnar table: trivial pivot of `evidence.sample_rows`
  (already a list of dicts) — column-major instead of row-major.
- Closest declared values (e.g. "Closest declared: ACHCredit,
  ACHDebit"): new helper, `levenshtein_neighbors(unknown_value,
  declared_values, top_k=2)`. Cheap to compute on the dozen-to-
  thirty-name corpus typical of an L2.
- `?from=` query-string append: handled by the CTA URL builder
  (new helper, ~5 lines). Editor pages on the receiving side
  validate `from` starts with `/etl/` (Lock 4).

---

## 4. Run page polish (BTa.2 P1.2/P1.3 + BTa.6)

**Consumes:** Lock 1 (per-stage timing tooltips + log-level help via
side panel if operator wants), Lock 2 (status pill on the landing
card mirrors the Run page state), Lock 4 (run page is one of the
three `?from=` sources).
**Before:** `bt_cold_read_screenshots/03_etl_run_initial.png`
("Run ETL" button alone, no hook attribution, anxiety-inducing) +
`12_etl_run_post_click.png` (raw log wall, no timings, no level, no
flash).

### 4.1 Before — current Run page

```
┌──────────────────────────────────────────────────────────────────────┐
│ Studio · ETL · Run + coverage          qsgen-sqlite                  │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│      ┌────────────────────────┐                                      │
│      │   ▶  Run ETL           │   last run: 2026-05-30 14:23         │
│      └────────────────────────┘   duration: 12.4s · ● success        │
│                                                                      │
├──────────────────────────────────────────────────────────────────────┤
│ COVERAGE                                                             │
├──────────────────────────────────────────────────────────────────────┤
│ Rails 1/30 · Templates 1/3 · Chains 0/9                              │
│ ... metadata block with the 2-of-4 math bug ...                      │
└──────────────────────────────────────────────────────────────────────┘
```

### 4.2 After — button rename + hook attribution + "show failures only"

```
┌──────────────────────────────────────────────────────────────────────┐
│ Studio · ETL · Run + coverage          qsgen-sqlite                  │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│      ┌────────────────────────┐                                      │
│      │   ↻  Refresh Data      │   last refresh: 2026-05-30 14:23     │
│      └────────────────────────┘   duration: 12.4s · ● success        │
│                                                                      │
│      Ran the bundled demo hook                                       │
│      (recon_gen._dev.etl_hook.demo_hook) [?]                         │
│      → To wire your own hook, see <docs link>                        │
│                                                                      │
├──────────────────────────────────────────────────────────────────────┤
│ COVERAGE                                                             │
├──────────────────────────────────────────────────────────────────────┤
│ Rails 1/30 · Templates 1/3 · Chains 0/9                              │
│ Metadata 6 / 10 required keys landed [?]                             │
│                                                                      │
│ [ Show failures only ✓ ]   [ Show all ]                              │
│                                                                      │
│ ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                 │
│ │ Rails 1/30   │  │ Templates 1/3│  │ Chains 0/9   │                 │
│ │              │  │              │  │              │                 │
│ │ (when "Show  │  │              │  │              │                 │
│ │  failures    │  │ CardSettle ✗ │  │ ACHOrigDaily✗│                 │
│ │  only" is on,│  │ FeeAssess  ✗ │  │ MerchPayOut ✗│                 │
│ │  green ones  │  │              │  │ ConcToFRB   ✗│                 │
│ │  collapse;   │  │              │  │ ... 6 more   │                 │
│ │  the green   │  │              │  │   chains ✗   │                 │
│ │  count stays │  │              │  │              │                 │
│ │  in header.) │  │              │  │              │                 │
│ │              │  │              │  │              │                 │
│ │ ACHCredit ✗  │  │              │  │              │                 │
│ │ ACHDebit  ✗  │  │              │  │              │                 │
│ │ Wire      ✗  │  │              │  │              │                 │
│ │ Check     ✗  │  │              │  │              │                 │
│ │ ... 25 more  │  │              │  │              │                 │
│ │ rails ✗      │  │              │  │              │                 │
│ │ [see all 30] │  │              │  │              │                 │
│ └──────────────┘  └──────────────┘  └──────────────┘                 │
│                                                                      │
│ ┌──────────────────────────────────────────────────────────────────┐ │
│ │ Metadata                                                         │ │
│ │                                                                  │ │
│ │   6 / 10 required metadata keys landed (60%) [?]                 │ │
│ │                                                                  │ │
│ │   Denominator: 10 = sum across non-empty templates               │ │
│ │   (4 + 4 + 2). See per-template breakdown below.                 │ │
│ │                                                                  │ │
│ │ Per template:                                                    │ │
│ │   MerchantSettlement   3 / 4 keys ✗   missing: card_brand        │ │
│ │   Payroll              4 / 4 keys ✓                              │ │
│ │   ACHReturn            2 / 2 keys ✓                              │ │
│ │   (CheckClear, CardSettlement — no rows; not in denominator)     │ │
│ └──────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│ Coverage report green = ETL contract satisfied.                      │
│ Not green? → Open Triage to see specific gaps.                       │
└──────────────────────────────────────────────────────────────────────┘
```

### 4.3 After — post-refresh state (flash + log)

```
│  ┌────────────────────────────────────────────────────────────┬───┐  │
│  │ ✓ Refreshed at 14:23:13 — 14,221 transactions inserted     │ ✕ │  │
│  │   (browser-tab bell rang)                                  └───┤  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                      │
├──────────────────────────────────────────────────────────────────────┤
│ LAST-RUN LOG                                                         │
├──────────────────────────────────────────────────────────────────────┤
│ time      lv  stage             event             duration           │
│ ───────   ──  ───────────────   ──────────────    ────────           │
│ 14:23:01  ℹ   step2:wipe        start                                │
│ 14:23:01  ℹ   step2:wipe        truncated 2 tbls  0.3s ✓             │
│ 14:23:01  ℹ   step1:etl_hook    start (demo)                         │
│ 14:23:09  ⚠   step1:etl_hook    1 row skipped     —                  │
│           "  stderr: bad rail name 'ach' on row 13422                │
│ 14:23:09  ℹ   step1:etl_hook    wrote 14,221 tx   8.1s ✓             │
│ 14:23:09  ℹ   step3:generator   skipped (disabled)                   │
│ 14:23:09  ℹ   step4:matviews    start                                │
│ 14:23:13  ℹ   step4:matviews    refreshed 7 mvs   3.7s ✓             │
│ 14:23:13  ℹ   step5:reload      data_gen_id 42→43 0.1s ✓             │
│ 14:23:13  ●   deploy:done       total             12.4s ✓            │
│                                                                      │
│ Stage timings sum to 12.2s of the 12.4s total (overhead 0.2s).       │
└──────────────────────────────────────────────────────────────────────┘
```

### 4.4 Captions — operator-facing strings & data sources

- **Button rename:** `Run ETL` → `↻ Refresh Data`. Operator
  rejected modal/confirm friction; rename is the chosen surface
  (cold-read P1.2 comment). Glyph `↻` reinforces "redo the load."
- **Hook attribution (P1.3, operator green-lit "agreed and we
  should show its output / error code"):**
  - If `cfg.etl_hook` is unset / points at the bundled stub:
    `Ran the bundled demo hook (recon_gen._dev.etl_hook.demo_hook)
    [?]` — the `[?]` opens a side panel explaining what the demo
    hook covers and how to swap it.
  - If `cfg.etl_hook` is operator-wired:
    `Ran your hook (<path>) [?]` — the `[?]` opens a side panel
    explaining the etl_hook contract (stdin/stdout, expected exit
    codes, BS.2 dev-log shape).
  - Stdout/stderr tail (last ~5 lines) lives in the log itself
    (the `⚠` log row in §4.3 carries the stderr inline).
  - Exit code: present in the log's final `deploy:done` row
    (success = `✓`, halted = `✗` + the exit code).
- **Per-stage timings + log level (P3.3, operator green-lit):**
  log gets four columns — `time`, `lv` (`ℹ` info / `⚠` warn / `✗`
  error / `●` summary), `stage`, `event`, `duration`. The duration
  column reads as `<seconds>s ✓` on the closing row of each stage;
  intermediate rows have `—`. Total at the bottom + a
  "sum vs total" caption surfacing scheduling overhead.
- **"Show failures only" toggle (P2.5, operator green-lit):**
  pill toggle above the coverage cards (`[ Show failures only ✓ ]`
  / `[ Show all ]`). When on, green entries collapse out of the
  per-card lists; the header tally stays (`1/30` still reads, just
  the *list* hides greens). Single-shot UI state — not persisted
  per session (the operator's `failures-only` is the right default
  during debugging; switching to "Show all" is the rare moment).
- **Transient flash + browser-tab bell (P3.4, operator green-lit
  "or a 'bell' in the browser tab / sound would be very helpful"):**
  - Flash: dismissable banner above the log, success/warn/error
    color matched to the run exit state. Auto-dismisses after 10s
    OR on `✕` click.
  - Browser-tab bell: title bar pulses with `(✓) Refreshed` /
    `(⚠) Halted` for 5s post-run, then settles to the static
    title. Optional audible ping (default off; toggleable via a
    preference if the operator wants).
- **Metadata roll-up math fix (P2.6, operator green-lit):** the
  BT-era `2/4` denominator bug — caption was rolling up wrong.
  After:
  - Header: `6 / 10 required metadata keys landed (60%)`.
  - Caption: `Denominator: 10 = sum across non-empty templates
    (4 + 4 + 2). See per-template breakdown below.`
  - Per-template line: omits zero-row templates from the
    denominator BUT lists them in parentheses (`CheckClear,
    CardSettlement — no rows; not in denominator`) so the
    operator sees the "why" without the math drifting.
  - `[?]` next to the header opens a side panel walking through
    the math one more time (this is the kind of "why is the
    number 60%?" question that compounds over weeks of staring
    at the page — having the math one click away is cheap).

**Data sources:**
- Hook attribution: `cfg.etl_hook` (string — either the bundled
  identifier or the operator-wired path) — new minor field on
  the cfg dataclass; default = bundled.
- Per-stage timings: BS.2 dev-log events already carry timestamps;
  the renderer pivots them into per-stage durations.
- Log level: new field on the dev-log event tuple (`info` / `warn`
  / `error`); BS.2 wire-up needed in the pipeline.
- Metadata math fix: in `metadata_coverage_per_template`, compute
  the denominator over templates with `rows > 0` only; expose the
  excluded-templates list separately for the caption.

---

## 5. Probe polish (BTa.5) + back-breadcrumb (BTa.2.5)

**Consumes:** Lock 1 (chain diagram + glossary via side panel),
Lock 4 (`?from=/etl/probe?...` back-link to the L2 editor when
Probe's `→ Edit in L2` is clicked).
**Before:** `bt_cold_read_screenshots/02_etl_probe_initial.png`
(initial empty form, opaque vocabulary) +
`05_probe_rail_green_InternalBalanceMaintenance.png` (the
window-mismatch confusion) +
`08_probe_chain_ACHOriginationDailySweep.png` (chain expected
table, text-only).

### 5.1 After — Probe form polish

```
┌──────────────────────────────────────────────────────────────────────┐
│ Studio · ETL · L2-slice probe              qsgen-sqlite              │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│ Pick a slice of the L2 to probe:                                     │
│                                                                      │
│ ┌──────────────────────────────────────────────────────────────────┐ │
│ │ ◉ Rail [?]                                                       │ │
│ │     A single payment-rail primitive — one "kind of money         │ │
│ │     movement" (ACH credit, wire transfer, ATM withdrawal).       │ │
│ │ ○ Transfer Template [?]                                          │ │
│ │     A multi-leg event — e.g. a merchant settlement = batch       │ │
│ │     open + batch close + customer credit on each leg.            │ │
│ │ ○ Chain [?]                                                      │ │
│ │     A parent→child relationship between two transfer events      │ │
│ │     linked via transfer_parent_id.                               │ │
│ └──────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│ Rail name:                                                           │
│ ┌──────────────────────────────────────────────────────────────────┐ │
│ │ [ search rails... 🔍 ]                                           │ │
│ │                                                                  │ │
│ │ ✓ ACHCredit                  (256 rows in window)                │ │
│ │ ✗ ACHDebit                   (no rows in window)                 │ │
│ │ ✗ Wire                       (no rows in window)                 │ │
│ │ ✗ Check                      (no rows in window)                 │ │
│ │ ✓ InternalBalanceMaintenance (47 rows in window)                 │ │
│ │ ✗ MerchantWeeklyBatchClose   (no rows in window)                 │ │
│ │ ... 24 more rails ...                                            │ │
│ └──────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│ Observation window:                                                  │
│   [ Last 7d ] [ Last 30d ] [ Last 90d ] [ All time ✓ ]               │
│   From [ — ] To [ — ]   [Apply]                                      │
│   (Default: All time. Narrow with a chip or pick custom dates.)      │
│                                                                      │
├──────────────────────────────────────────────────────────────────────┤
│ EXPECTED (from L2)        │ OBSERVED (window)                        │
├───────────────────────────┼──────────────────────────────────────────┤
│ ...                       │ ...                                       │
│ → Edit in L2              │                                          │
│   (carries ?from=         │                                          │
│   /etl/probe?kind=rail&   │                                          │
│   name=ACHCredit)         │                                          │
└───────────────────────────┴──────────────────────────────────────────┘
```

Operator-facing strings:
- Radio definitions (P2.2, operator green-lit): one-sentence
  description under each radio label. `[?]` opens the side panel
  with the longer-form glossary entry (same drawer as §2.b).
- Name dropdown (P2.3, operator green-lit):
  - Search box at top (`[ search rails... 🔍 ]`) — typeahead
    filters the list as the operator types.
  - Each row prefixed with a status badge (`✓` = has rows in
    window, `✗` = no rows in window). Operator can scan for
    `✓` and pick the one rail with data without scrolling
    through 30 names. The cold-read's complaint ("how many rails?
    are they alphabetized? grouped by status?") gets a yes-yes-yes
    answer.
  - Per-row row-count parenthetical (`(256 rows in window)`)
    completes the picture; `(no rows in window)` is the explicit
    `✗` case so the operator doesn't have to wonder if `✗` means
    "doesn't exist."
- Date quick-pick chips (P2.4, operator green-lit + cold-read
  P1.1 comment "default to all for now"):
  - `[ Last 7d ] [ Last 30d ] [ Last 90d ] [ All time ✓ ]` — four
    chips above the date inputs.
  - Default = `All time` per BTa.2 P1.1 / operator's "default to
    all for now."
  - Picking a chip populates the From/To inputs + auto-applies;
    typing custom dates does the same on `[Apply]`.
  - Caption: `(Default: All time. Narrow with a chip or pick
    custom dates.)` — flips the BT-era "widen for backfill"
    helper text on its head: default is wide, narrow is the
    explicit action.
- `→ Edit in L2`: appends `?from=/etl/probe?kind=rail&
  name=ACHCredit` so the L2 editor's back-breadcrumb (§5.3) takes
  the operator straight back to the same Probe slice they were
  staring at.

### 5.2 After — Chain Probe Expected with side-panel diagram link

```
├──────────────────────────────────────────────────────────────────────┤
│ EXPECTED (from L2)                                                   │
├──────────────────────────────────────────────────────────────────────┤
│ parent             =   ACHOriginationDailySweep                      │
│ child              =   ConcentrationToFRBSweep                       │
│ kind [?]           =   Required (singleton)                          │
│ transfer_parent_id ≠   NULL                                          │
│                                                                      │
│ → Edit in L2                                                         │
│ → View chain diagram                                                 │
│   ^^^ opens side panel (§2.c) with parent → child diagram +          │
│       inlined "kind" definition + other-kinds enum                   │
└──────────────────────────────────────────────────────────────────────┘
```

Two new links under the Expected table:
1. `→ Edit in L2` (kept from BT) — carries `?from=/etl/probe?
   kind=chain&name=ACHOriginationDailySweep`.
2. `→ View chain diagram` — opens the side panel at §2.c (the
   diagram + kind definition + other-kinds enum is the side panel's
   content; no inline diagram on the page itself per Lock 1's
   "drawer keeps default layout intact").

Operator-facing strings:
- `kind [?]` triggers the side panel with just the `kind` field's
  enum + definitions (`Required singleton`, `Optional singleton`,
  `Required fanout`, `Optional fanout`).
- `→ View chain diagram` is verbatim; the side panel's title bar
  carries the chain's name so the diagram has its own context.

### 5.3 After — back-breadcrumb on L2 editor (sticky)

When the L2 editor is reached via `?from=/etl/...`, a sticky thin
bar appears at the top of the page until the operator commits an
edit or navigates away.

```
┌──────────────────────────────────────────────────────────────────────┐
│ Recon-Gen │ L2 Editor │ ETL Support │ Training │ ...        [ ? ]    │
├──────────────────────────────────────────────────────────────────────┤
│ ← Back to Triage                                                     │  ← sticky bar
├──────────────────────────────────────────────────────────────────────┤
│ Studio · L2 Editor · Rail                  qsgen-sqlite              │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│ Edit Rail: ACHCredit                                                 │
│ ...                                                                  │
│                                                                      │
│ [ Save ]   [ Cancel ]                                                │
└──────────────────────────────────────────────────────────────────────┘
```

Operator-facing strings:
- `← Back to Triage` (when `?from=/etl/triage`).
- `← Back to Probe` (when `?from=/etl/probe?kind=...&name=...` —
  the back-link round-trips to the same probe slice).
- `← Back to Run` (when `?from=/etl/run`).
- Save handler preserves `?from=` in its redirect target so "save
  then click back" works in two clicks (per Lock 4); the redirect
  takes the operator back to the read view of the edited entity,
  not bounce straight to Triage — they may want to verify the save
  rendered correctly before going back.

### 5.4 Captions — data sources

- Status badges (`✓` / `✗`) on the rail dropdown: per-rail
  `count_rows_in_window(rail_name, window)` query — one SELECT per
  rail or one GROUP BY across all rails (the latter is cheaper). On
  the order of milliseconds for a typical L2.
- Date chip auto-apply: pure JS — clicking a chip populates the
  date inputs + submits the form. No new server endpoint.
- Chain diagram side-panel content: existing chain topology helper
  (the L2 diagram already knows parent→child edges); render as a
  small SVG.
- Sticky back-breadcrumb: new helper in the L2 editor's render path,
  ~10 lines. Reads `request.query_params.get("from")`, validates it
  starts with `/etl/`, renders the bar.

---

## 6. Cross-page interaction flow

### 6.1 Pre-BTa flow (BT-era, broken)

```
            ┌──────────┐
   operator │  /etl/   │ ← 3 equal cards, no sequence
   lands ──▶│          │
            └────┬─────┘
                 │ guesses Run? Triage? Probe?
                 ▼
            ┌──────────┐
            │ /etl/run │ ← scary "Run ETL" button
            │  click   │
            └────┬─────┘
                 │ runs; coverage mostly red; whose hook ran?
                 ▼
            ┌──────────┐
            │ /etl/    │ ← 60 cards; declared_rails noise
            │ triage   │
            └────┬─────┘
                 │ clicks "Open Rails editor"
                 ▼
            ┌──────────┐
            │ L2       │ ← Editor home; no breadcrumb; lost
            │ Editor   │
            │ home     │
            └────╳─────┘ ← operator stuck
```

### 6.2 Post-BTa flow (the fix)

```
            ┌──────────────────┐
            │  /etl/           │ ← numbered cards 1→2→3
   first ──▶│  + tutorial      │   + "First time here?" banner
   visit    │  banner          │
            └────┬─────────────┘
                 │ follows "1. Refresh Data"
                 ▼
            ┌──────────────────┐
            │ /etl/run         │ ← "↻ Refresh Data"
            │  click           │   hook attribution shown
            │                  │   timed log + flash + bell
            └────┬─────────────┘
                 │ sees "Show failures only"; clicks
                 │ "Coverage report not green? → Open Triage"
                 ▼
            ┌──────────────────┐
            │ /etl/triage      │ ← 4 accordion sections collapsed
            │                  │   biggest-row-count at top
            └────┬─────────────┘
                 │ opens biggest section; sees per-card volume
                 │ badges; picks the biggest card
                 ▼
            ┌──────────────────┐
            │ /etl/triage card │ ← columnar sample
            │                  │   [ → Add "ach" to Rails ]
            └────┬─────────────┘
                 │ clicks deep-link CTA
                 │ (URL: /l2_shape/rail/new?name=ach
                 │       &from=/etl/triage)
                 ▼
            ┌──────────────────┐
            │ L2 Editor · Rail │ ← sticky "← Back to Triage"
            │ (new "ach" form  │   the form is pre-filled
            │  pre-filled)     │
            └────┬─────────────┘
                 │ saves; redirect preserves ?from=
                 ▼
            ┌──────────────────┐
            │ L2 Editor · Rail │ ← sticky "← Back to Triage" still
            │ (read view)      │   here; one click home
            └────┬─────────────┘
                 │ clicks "← Back to Triage"
                 ▼
            ┌──────────────────┐
            │ /etl/triage      │ ← refreshes; the "ach" card now
            │  re-rendered     │   gone; section row count drops
            └──────────────────┘
                 │ optional: "Refresh Data" again to confirm
                 │ no new gaps surface for "ach"
                 ▼
            (loop complete; ~6 clicks, no dead ends)
```

The post-BTa loop is the BT-era loop with three structural changes:
numerals + arrows on the landing tell the operator the sequence;
deep links carry context into the L2 editor; the sticky back-link
closes the round-trip. Everything else (Refresh button, coverage
cards, triage cards, L2 editor) is the BT-era surface with the
cold-read's polish applied on top.

---

## 7. Open questions for operator

Each is a design call that the locks didn't fully resolve and that
this mockup surfaces. Defaults are the agent's lean; the operator
confirms / flips before BTa.1 fires.

1. **Per-kind colors — orange / purple / teal / slate?**
   The §3.4 table proposes hues that pass WCAG AA on the studio bg
   and feel distinct from the red/green axis. Operator's "keep it
   accessible" pushback noted; but the specific color palette is
   subjective and may collide with the studio theme tokens already
   in use (`text-success` / `text-warning` / `text-danger`).
   **Default if no override:** ship as proposed; revisit on BTa.7
   cold-read v3 if the four kinds still feel undifferentiated.

2. **Glossary trigger surface — top-nav `[?]` only, or also inline
   `[?]` next to every term mention?**
   §2.b proposes the global top-nav button as the entry. §2.a + 2.c
   add inline `[?]` triggers next to specific terms. The risk of
   "too many `[?]` icons everywhere" is real (visual noise; harder
   to scan).
   **Default if no override:** ship the top-nav `[?]` + inline `[?]`
   only on the Probe radio labels + the `LimitSchedule` mention in
   Triage section headers + the chain `kind` field. Other terms
   route via the top-nav.

3. **Columnar sample format — metadata as one collapsed row, or
   each key as its own row?**
   §3.3's mockup shows `metadata.tr...` (truncated key name +
   value) as one collapsed row with a `+1 row metadata key shown`
   footnote. Each metadata key on its own row gives the operator
   more detail but blows up the card's vertical height (a row with
   5 metadata keys becomes a 9-row sample table instead of a
   4-row).
   **Default if no override:** show the FIRST metadata key inline
   (often the most identifying one — `trace_id`, `originator_id`);
   collapse the rest with a `+N more metadata keys` footnote per
   sample. Operator opens the L2 editor for the full picture.

4. **Audible bell on Run completion — default on or off?**
   §4.4 proposes the browser-tab title pulse always-on (visual);
   audible ping as a toggleable preference. Audible defaults on
   risks noise pollution for the operator in a quiet office;
   defaults off risks the cold-read's "we all know we're
   multi-tasking" feedback going unaddressed.
   **Default if no override:** title pulse ON always; audible ping
   OFF by default, toggleable via a preferences fragment (no new
   page; just a toggle in the side panel reachable via top-nav `[?]
   → Preferences`).

5. **Tutorial banner — re-shown after L2 schema change, or only
   after deployment_name change?**
   §1.4 keys the localStorage dismissal to `deployment_name`. But
   the L2 *schema* may evolve substantially within one deployment;
   the operator's "I learned this once" memory may not carry over
   to a redesigned L2.
   **Default if no override:** key the dismissal to
   `deployment_name` only; if operator wants a "show me the
   tutorial again" affordance, expose it in the top-nav `[?]` side
   panel as a "Reset onboarding" link (low-friction opt-in
   re-show).

6. **`?from=` validation — strict `/etl/` only, or also allow
   `/l2_shape/...` back-links for cross-editor flows?**
   Lock 4 specifies `/etl/` only as the security gate. But a
   future "edit rail X from the diagram view" flow might want
   `?from=/diagram` to work too.
   **Default if no override:** strict `/etl/` for BTa; expand the
   allowlist in a follow-on phase if a non-ETL caller actually
   needs the breadcrumb.

7. **Run page button colorway — keep accent or shift to muted?**
   §4.2 keeps the existing accent button styling. The rename to
   "Refresh Data" softens the destructive connotation but doesn't
   eliminate it — the button still wipes the demo DB. A muted /
   secondary button styling might better match the rename's intent
   (less "primary action," more "iterate-loop step").
   **Default if no override:** keep the accent button. The operator
   explicitly rejected modal/confirm friction; further softening
   the button risks making it too hard to find on a busy page.

---

*End of mockups. Pass to operator for review; flip any of the §7
open questions; then BTa.1 (side-panel infra) lands first per
BTa.0's sequencing.*
