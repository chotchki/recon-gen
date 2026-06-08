# BU.0 — Phase BU REPLAN (Training mode design locks)

> **Status:** REPLAN DRAFT 2026-05-30 (round-4 revision same day —
> typed-violation-class anti-drift pass per operator directive).
> Locks 11 cross-cutting design decisions before BU.1-6 fire. Output
> of BU.0; feeds BU.0.5 (`bu_design_mockups.md`).
>
> **Round-2 diff summary:** added Lock 0 (Trainer covers L1+L2);
> revised Lock 1 (12→17 cards, 5→7 families); revised Lock 3
> (per-kind tour destination mapping table); added Lock 6 (L2
> plant cleanup parity); updated sequencing + out-of-scope + BU.0.5
> mockup brief. Locks 2, 4, 5 unchanged.
>
> **Round-3 diff summary:** added §0.5 violation coverage matrix
> (canonical source of truth for the kind universe — proves nothing
> falls through the gap that bit round-2); added Lock 7 (shared
> plant-registry pattern — one data-driven registry drives all per-
> kind UI, no bespoke pages); expanded Lock 1 family list 7→8
> (added `L2FT Hygiene` family — round-2's "L2" was lumping ETL
> Triage + L2FT Hygiene + ETL Coverage together, masking that
> L2FT_Exceptions.md declares 6 checks of which only 2 had plant
> coverage); rewrote Lock 3 around registry's `tour_destination`
> column; added BU.X needs-build cells for the 4 missing L2FT
> hygiene plants + `expected_eod_balance_breach`. Each updated
> section leads with `**Updated 2026-05-30 round-3 scope:**`.
>
> **Round-4 diff summary:** added Lock 8 (registry is a THIN INDEX
> over existing typed violation-class catalogues — display strings
> live on `InvariantSection` / `L2FTExceptionSection`, NOT on
> `PlantKindEntry`); Lock 9 (anti-drift = parameterized test
> contract: bijectivity, tour-URL liveness, plant→matview
> round-trip, docs-freshness); Lock 10 (typed source coverage —
> `L2FTExceptionSection` already exists in
> `common/handbook/l2ft_exceptions.py`; L2 Triage labels live in
> `_studio_routes.py::_GAP_KIND_LABELS` — round-4 consolidates them
> into a typed `L2TriageGapSection` paralleling the L1 / L2FT
> pattern); Lock 11 (the registry walk is the SoT for documentation
> generation — handbook pages, trainer panels, demo-mode
> disclosures, `recon-gen docs export` all consume it). §0.5
> matrix grows one column (`Violation class source (SoT)`); Lock 7
> shrinks because display strings move out; §7 collapses Q1 + Q16
> (registry's secondary-link field + typed sections answer both);
> sequencing redrawn (8 cells: BU.0 → BU.1 vertical slice → BU.2a
> typed sources → BU.2b registry skeleton → BU.3.x needs-build
> plants → BU.4 registry populate → BU.5 documentation generation
> → BU.6 cold-read). Net doc growth ~zero (Lock 7 shrinks by what
> Locks 8-11 add).

## Headline

Phase BU stands up the Training mode at `/training/` — the third
authoring surface alongside `/` (L2 Editor) and `/etl/` (ETL Support).
Top-nav already advertises the entry (`render.py:965`) but no route
handler exists, so the link 404s today. The seed for the new surface
is `common/html/_studio_training.py::render_training_pane` — the
right-column pane on `/data` that lists one card per L1 invariant
kind with a deep-link to the App2 dashboard sheet that surfaces it.
BU promotes that pane to a first-class section that owns plant
generation + a guided before/after tour.

Persona per SPEC (line 61-62): **Trainer** — a senior engineer or
customer-success rep who generates controlled invariant-violation
scenarios in the demo DB so they can walk End Users through "here's
what Drift looks like on the L1 Dashboard," etc. Distinct from the
Integrator (`/`) and the ETL Engineer (`/etl/`); Trainer's job is
pedagogy + scenario generation, not L2 shape authorship or feed
debugging.

The locks below commit to the cross-cutting shape questions
(landing pattern, plant-picker UX, tour mechanics, reset semantics,
existing-pane disposition, L2 plant cleanup parity). They mirror
BTa.0's locks-then-mockup sequencing. Every locked shape names
its rejected variants.

## §0.5 Violation coverage matrix (canonical source of truth)

**Updated 2026-05-30 round-4 scope:** added `Violation class source
(SoT)` column. Every kind now names the typed dataclass the registry
will reference for its display strings (title, should-statement,
remediation, columns). Three sources cover the universe:
`InvariantSection` (existing, `common/handbook/invariants.py`),
`L2FTExceptionSection` (existing, `common/handbook/l2ft_exceptions.py`),
and `L2TriageGapSection` (round-4-build, consolidates the labels
currently scattered in `common/html/_studio_routes.py::_GAP_KIND_LABELS`).
A glance at the column confirms "every plant kind has a typed source
class" — anti-drift becomes a typed gate, not a manual review.

**Updated 2026-05-30 round-3 scope:** new section. The round-2 design
treated `demo_etl_gaps.py`'s 5 plants as the entire L2 universe and
missed 4 of the 6 L2FT Hygiene Exceptions (`src/recon_gen/docs/L2FT_Exceptions.md`).
This matrix is the canonical kind universe — every plant kind the
Trainer must expose AND every violation surface those kinds appear on.
The Lock 7 registry IS this table at runtime; rows below ARE registry
rows.

**Sources walked** (no other "is this kind handled?" question
answered without checking these):

- `common/handbook/invariants.py::INVARIANT_KIND_TO_SHEET` — 12 L1
  invariant kinds → 8 dashboard sheets.
- `src/recon_gen/docs/L2FT_Exceptions.md` — 6 L2FT Hygiene checks
  (Chain Orphans / Unmatched Rail Name / Dead Rails / Dead Bundles
  Activity / Dead Metadata Declarations / Dead Limit Schedules).
- `common/l2/seed.py` — 21 `Plant` dataclasses fielded on
  `ScenarioPlant`.
- `common/l2/auto_scenario.py` — picker functions
  (`_pick_*_inputs`) + `filter_scenario_plants`'s kind-name set.
- `common/l2/demo_etl_gaps.py` — 5 L2 ETL-feed gap plant functions
  (`add_*_gap_rows`).
- `apps/l1_dashboard/app.py::SHEET_*` — 8 L1 exception-bearing
  sheets (Drift / Drift Timelines / Overdraft / Limit Breach /
  Pending Aging / Unbundled Aging / Supersession Audit / Today's
  Exceptions); plus 4 orientation sheets (Getting Started /
  Daily Statement / Transactions / App Info) that do NOT surface
  violations.
- `apps/l2_flow_tracing/datasets.py::build_exc_*` — 6 per-check
  L2FT datasets + `l2ft_unified_exceptions` UNION ALL feeding
  L2FT's L2 Hygiene Exceptions sheet.

**Coverage matrix — every kind the Trainer exposes:**

Legend for "Plant primitive exists?":
- `existing` — plant code in seed.py / demo_etl_gaps.py + picker
  in auto_scenario.py; BU.2 just wraps it in a registry row + UI.
- `partially-exists` — plant code exists but doesn't cover the
  exact violation shape (e.g. `phantom_rail` plants an undeclared
  rail — same shape as the L2FT `unmatched_rail_name` check, but
  is currently only categorized under ETL Triage).
- `needs-build` — no plant code; BU.X cell in §Sequencing.

| Kind                            | Family            | Surface (sheet / check)                                       | Plant primitive exists? | Registry entry name              | Violation class source (SoT)                                                          | Notes                                                                                          |
|---------------------------------|-------------------|---------------------------------------------------------------|-------------------------|----------------------------------|---------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------|
| **L1 Conservation**             |                   |                                                               |                         |                                  |                                                                                       |                                                                                                |
| `drift`                         | L1 Conservation   | L1 Drift sheet + Today's Exceptions                           | existing                | `drift`                          | `InvariantSection["drift"]` (existing)                                                | `DriftPlant`; picker `_pick_template` + `_pick_inbound_2leg_rail`.                              |
| `ledger_drift`                  | L1 Conservation   | L1 Drift sheet + Today's Exceptions + Drift Timelines         | existing                | `ledger_drift`                   | `InvariantSection["ledger_drift"]` (existing)                                         | Same `DriftPlant` shape; sasquatch_pr's bundled DDA-Control plant exercises this.              |
| `overdraft`                     | L1 Conservation   | L1 Overdraft sheet + Today's Exceptions                       | existing                | `overdraft`                      | `InvariantSection["overdraft"]` (existing)                                            | `OverdraftPlant`; picker `_pick_template`.                                                     |
| **L1 Cap**                      |                   |                                                               |                         |                                  |                                                                                       |                                                                                                |
| `limit_breach` (outbound)       | L1 Cap            | L1 Limit Breach sheet + Today's Exceptions                    | existing                | `limit_breach_outbound`          | `InvariantSection["limit_breach"]` (existing, shared with inbound)                    | `LimitBreachPlant`; picker `_pick_breach_inputs`. Two registry rows share one section + a per-row direction qualifier in `entry.kind_qualifier`. |
| `limit_breach` (inbound)        | L1 Cap            | L1 Limit Breach sheet + Today's Exceptions                    | existing                | `limit_breach_inbound`           | `InvariantSection["limit_breach"]` (existing, shared with outbound)                   | `InboundCapBreachPlant`; picker `_pick_inbound_breach_inputs`. Mirror of outbound (AB.1).      |
| `expected_eod_balance_breach`   | L1 Cap            | L1 Today's Exceptions (no dedicated sheet)                    | **needs-build**         | `expected_eod_balance_breach`    | `InvariantSection["expected_eod_balance_breach"]` (existing)                          | Matview exists; no `ScenarioPlant` field carries the primitive. BU.3.1 cell.                  |
| **L1 Aging**                    |                   |                                                               |                         |                                  |                                                                                       |                                                                                                |
| `stuck_pending`                 | L1 Aging          | L1 Pending Aging sheet + Today's Exceptions                   | existing                | `stuck_pending`                  | `InvariantSection["stuck_pending"]` (existing)                                        | `StuckPendingPlant`; picker `_pick_first_with(max_pending_age)`.                               |
| `stuck_unbundled`               | L1 Aging          | L1 Unbundled Aging sheet + Today's Exceptions                 | existing                | `stuck_unbundled`                | `InvariantSection["stuck_unbundled"]` (existing)                                      | `StuckUnbundledPlant`; picker `_pick_first_with(max_unbundled_age)`.                           |
| **L1 Chain coherence**          |                   |                                                               |                         |                                  |                                                                                       |                                                                                                |
| `chain_parent_disagreement`     | L1 Chain          | L1 Today's Exceptions                                         | existing                | `chain_parent_disagreement`      | `InvariantSection["chain_parent_disagreement"]` (existing)                            | `ChainParentDisagreementPlant`; picker `_pick_two_template_chain_inputs`.                     |
| `xor_group_violation` (missed)  | L1 Chain          | L1 Today's Exceptions                                         | existing                | `xor_group_missed`               | `InvariantSection["xor_group_violation"]` (existing, shared with overlap)             | `XorVariantMissedFiringPlant`; picker `_pick_xor_missed_firing_inputs`.                       |
| `xor_group_violation` (overlap) | L1 Chain          | L1 Today's Exceptions                                         | existing                | `xor_group_overlap`              | `InvariantSection["xor_group_violation"]` (existing, shared with missed)              | `XorVariantOverlapPlant`; picker `_pick_xor_overlap_inputs`.                                  |
| `fan_in_disagreement` (missing) | L1 Chain          | L1 Today's Exceptions                                         | existing                | `fan_in_missing_parent`          | `InvariantSection["fan_in_disagreement"]` (existing, shared with extra)               | `FanInChainMissingParentPlant`; picker `_pick_fan_in_chain_inputs`.                           |
| `fan_in_disagreement` (extra)   | L1 Chain          | L1 Today's Exceptions                                         | existing                | `fan_in_extra_parent`            | `InvariantSection["fan_in_disagreement"]` (existing, shared with missing)             | `FanInChainExtraParentPlant`; picker drops when expected_parent_count unset.                  |
| `multi_xor_violation` (missed)  | L1 Chain          | L1 Today's Exceptions                                         | existing                | `multi_xor_missed`               | `InvariantSection["multi_xor_violation"]` (existing, shared with overlap)             | `MultiXorMissedPlant`; picker `_pick_multi_xor_chain_inputs`.                                 |
| `multi_xor_violation` (overlap) | L1 Chain          | L1 Today's Exceptions                                         | existing                | `multi_xor_overlap`              | `InvariantSection["multi_xor_violation"]` (existing, shared with missed)              | `MultiXorOverlapPlant`; picker reuses `_pick_multi_xor_chain_inputs`.                         |
| **L1 Audit**                    |                   |                                                               |                         |                                  |                                                                                       |                                                                                                |
| `supersession_audit`            | L1 Audit          | L1 Supersession Audit sheet                                   | existing                | `supersession_audit`             | `InvariantSection["supersession_audit"]` (existing)                                   | `SupersessionPlant`; picker `_pick_supersession_rail`. Diagnostic, not a SHOULD.              |
| **L2 Triage (ETL feed)**        |                   |                                                               |                         |                                  |                                                                                       |                                                                                                |
| `phantom_rail`                  | L2 Triage         | /etl/triage `unmatched_rail` section + L2FT `unmatched_rail_name` check | existing      | `phantom_rail`                   | `L2TriageGapSection["unmatched_rail"]` (**round-4-build** per Lock 10) — primary; secondary `L2FTExceptionSection["unmatched_rail_name"]` for the dual-surface callout | `add_phantom_rail_gap_rows`; INSERT. **Dual surface** — same plant feeds Triage AND L2FT.    |
| `phantom_template`              | L2 Triage         | /etl/triage `unmatched_template` section                      | existing                | `phantom_template`               | `L2TriageGapSection["unmatched_template"]` (**round-4-build**)                        | `add_phantom_template_gap_rows`; INSERT. L2FT has no template-name hygiene check.            |
| `missing_metadata_key`          | L2 Triage         | /etl/triage `missing_metadata_key` section                    | existing                | `missing_metadata_key`           | `L2TriageGapSection["missing_metadata_key"]` (**round-4-build**)                      | `add_missing_metadata_gap_rows`; INSERT row missing a required key. **NOT same direction as L2FT Dead Metadata** — see L2FT family below. |
| **L2 Coverage (ETL feed)**      |                   |                                                               |                         |                                  |                                                                                       |                                                                                                |
| `uncovered_rail`                | L2 Coverage       | /etl/run Coverage Rails card + L2FT `dead_rails` check        | existing                | `uncovered_rail`                 | `L2TriageGapSection["uncovered_rail"]` (**round-4-build** — L2 Coverage entries live in the same typed catalogue as L2 Triage; the family discriminator is on `PlantKindEntry.family`, not on the section type) — secondary `L2FTExceptionSection["dead_rails"]` for the dual-surface callout | `add_uncovered_rail_gap_rows`; DELETE. **Dual surface** — Coverage card AND L2FT Dead Rails. |
| `uncovered_template`            | L2 Coverage       | /etl/run Coverage Templates card                              | existing                | `uncovered_template`             | `L2TriageGapSection["uncovered_template"]` (**round-4-build**)                        | `add_uncovered_template_gap_rows`; DELETE. L2FT has no template hygiene check.                |
| **L2FT Hygiene**                |                   |                                                               |                         |                                  |                                                                                       |                                                                                                |
| `chain_orphan`                  | L2FT Hygiene      | L2FT L2 Hygiene Exceptions sheet (`chain_orphans` check)      | **needs-build**         | `chain_orphan`                   | `L2FTExceptionSection["chain_orphans"]` (existing — parser already ships)             | Parent rail fires; no child fires citing parent_transfer_id within SLA. BU.3.2 cell.          |
| `dead_bundles_activity`         | L2FT Hygiene      | L2FT L2 Hygiene Exceptions sheet (`dead_bundles_activity`)    | **needs-build**         | `dead_bundles_activity`          | `L2FTExceptionSection["dead_bundles_activity"]` (existing)                            | Declared (aggregating_rail, bundle_target) pair with no posting on bundle_target. BU.3.3.    |
| `dead_metadata`                 | L2FT Hygiene      | L2FT L2 Hygiene Exceptions sheet (`dead_metadata`)            | **needs-build**         | `dead_metadata`                  | `L2FTExceptionSection["dead_metadata_declarations"]` (existing — note slug mismatch with registry kind; bijectivity test gates this) | Declared (rail, metadata_key) with no posting carrying a non-null value for the key. BU.3.4. **Opposite direction from `missing_metadata_key`** — see Notes section after table. |
| `dead_limit_schedule`           | L2FT Hygiene      | L2FT L2 Hygiene Exceptions sheet (`dead_limit_schedules`)     | **needs-build**         | `dead_limit_schedule`            | `L2FTExceptionSection["dead_limit_schedules"]` (existing)                             | LimitSchedule (parent_role, rail_name, cap) with no outbound Debit flow against the cell. BU.3.5. |

**The two metadata directions — DO NOT collapse:**

The round-2 design conflated `missing_metadata` (Triage) with the
L2FT `dead_metadata` check. They are **opposite directions** of the
same axis:

- `missing_metadata_key` (Triage / `demo_etl_gaps.py`): some
  posting tagged with a real template arrives WITHOUT that
  template's declared `transfer_key`. The ETL violated a per-row
  contract. Triage flags partial coverage ("12 of 14 rows have
  it"). **Fix:** the ETL.
- `dead_metadata` (L2FT Hygiene): the L2 declares
  `Rail.metadata_keys = ['foo']` but ZERO postings in the window
  carry a non-null `$.foo`. The declaration is dead; the integrator
  declared a key the ETL never emits. **Fix:** drop the
  declaration OR start emitting the key.

Both must be plantable kinds; they have different primitive
shapes + different remediation prose. Round-2 had only the first.

**The dual-surface kinds — also DO NOT collapse:**

`phantom_rail` (Triage) and the L2FT `unmatched_rail_name` check
fire on the SAME data shape — postings against an undeclared rail.
One plant lights up two surfaces. Same goes for `uncovered_rail`
(Coverage card + L2FT `dead_rails`). The registry lists ONE entry
each (sharing the plant function); the `tour_destination` field
points at the operator-facing surface (Coverage card for
`uncovered_rail`; both Triage AND L2FT for `phantom_rail`). The
registry's secondary-destination list (Lock 7) handles
dual-surface tours.

**Count headline:** 21 violation kinds, 16 with existing plant
primitives, 5 needing build (1 L1 + 4 L2FT). 6 families across 2
top-level groups (L1 invariant: Conservation / Cap / Aging / Chain
/ Audit; L2 feed-contract: Triage / Coverage / L2FT Hygiene). The
"L2FT Hygiene" family is the round-3 addition.

**Typed-source headline (round-4):** 21 registry rows → 3 typed
catalogues. 15 rows reference `InvariantSection` (existing module,
`common/handbook/invariants.py`). 4 rows reference `L2FTExceptionSection`
(existing module, `common/handbook/l2ft_exceptions.py`). 5 rows
reference `L2TriageGapSection` (round-4-build module per Lock 10 —
consolidates the labels currently scattered in
`_studio_routes.py::_GAP_KIND_LABELS` + `_GAP_KIND_EDITOR_LABELS` +
diagnosis prose in `common/l2/triage.py::detect_gaps`). Lock 9's
bijectivity test asserts every registry kind maps to a typed
section that exists and every typed section is referenced by at
least one registry row — typos at any of the 21 rows surface as
test failures, not silent dashboard drift.

**`missing_limit_schedule` is intentionally NOT a kind here.**
Per `demo_etl_gaps.py` module docstring: it's an L2-shape gap
(rail/role pair without a schedule), not a transaction-stream gap —
you can't synthesize it via INSERT/DELETE. Operator addresses it
in the L2 editor by adding a LimitSchedule. The Trainer doesn't
plant L2-shape gaps; those belong to the Integrator surface.

## Lock 0: scope — Trainer covers BOTH L1 invariant violations AND L2 ETL-feed contract violations

**Updated 2026-05-30 round-3 scope:** scope expanded to include the
4 L2FT Hygiene plants missed in round-2 (Chain Orphans / Dead
Bundles Activity / Dead Metadata / Dead Limit Schedules), plus the
`expected_eod_balance_breach` L1 plant flagged as needs-build in
the §0.5 matrix. Decision restated: Trainer covers EVERY violation
kind surfaced on EITHER the L1 dashboard exception sheets OR the
L2 Flow Tracing L2 Hygiene Exceptions sheet OR /etl/triage OR
/etl/run Coverage. No silent gaps. The §0.5 matrix is the
canonical "what is exposed" set; this lock is the architectural
ratification of "expose all of it."

**Decision:** `/training/` exposes 21 plant kinds across 8 families
(see §0.5 matrix for the canonical list):
- **15 L1 invariant kinds** — 14 existing primitives (drift /
  ledger_drift / overdraft / limit_breach outbound + inbound /
  stuck_pending / stuck_unbundled / 6 chain coherence variants /
  supersession_audit) flow through `common/l2/auto_scenario.py` +
  `emit_full_seed`; 1 needs-build primitive
  (`expected_eod_balance_breach`).
- **5 L2 ETL-feed contract kinds** (existing) — surfaced on
  `/etl/triage` (3 kinds) AND `/etl/run` Coverage cards (2 kinds);
  plant primitives in `common/l2/demo_etl_gaps.py` ship as raw
  INSERT/DELETE overlays applied AFTER the generator pipeline
  finishes. Two of them ALSO surface on L2FT hygiene (dual-surface).
- **4 L2FT Hygiene kinds** (NEW round-3, all needs-build) — Chain
  Orphans / Dead Bundles Activity / Dead Metadata / Dead Limit
  Schedules. Surface ONLY on the L2FT L2 Hygiene Exceptions sheet
  (not on /etl/triage — different operator-facing surface; see
  Lock 3). Implementation goes in `common/l2/demo_etl_gaps.py`
  alongside the existing 5 ETL-feed plants (same integration point
  — raw DML overlay; new module file optional, judgment call in
  BU.3).

The 5 EXISTING L2 ETL-feed plant kinds, per `demo_etl_gaps.py`:

| Plant kind             | Surface              | DML     | Function                              |
|------------------------|----------------------|---------|---------------------------------------|
| `phantom_rail`         | /etl/triage + L2FT   | INSERT  | `add_phantom_rail_gap_rows`           |
| `phantom_template`     | /etl/triage          | INSERT  | `add_phantom_template_gap_rows`       |
| `missing_metadata_key` | /etl/triage          | INSERT  | `add_missing_metadata_gap_rows`       |
| `uncovered_rail`       | /etl/run + L2FT      | DELETE  | `add_uncovered_rail_gap_rows`         |
| `uncovered_template`   | /etl/run Coverage    | DELETE  | `add_uncovered_template_gap_rows`     |

The 4 NEW round-3 L2FT-Hygiene plant kinds (all needs-build):

| Plant kind              | Surface                                 | Likely DML | Notes                                                                                |
|-------------------------|-----------------------------------------|------------|--------------------------------------------------------------------------------------|
| `chain_orphan`          | L2FT L2 Hygiene Exceptions sheet        | INSERT     | Parent rail fires; no child rail/template fires citing parent's transfer_id.        |
| `dead_bundles_activity` | L2FT L2 Hygiene Exceptions sheet        | DELETE     | Empty postings against an L2-declared `bundle_target` rail name.                    |
| `dead_metadata`         | L2FT L2 Hygiene Exceptions sheet        | DELETE / NULL-strip | Strip non-null values for a declared `Rail.metadata_keys` entry from in-window postings. |
| `dead_limit_schedule`   | L2FT L2 Hygiene Exceptions sheet        | DELETE     | Empty outbound Debit postings against a (parent_role, rail_name) LimitSchedule cell. |

Today these 5 plants fire ONLY when `cfg.etl_hook is None` and ONLY
as a side-effect of `/etl/run` POST. The Trainer rework promotes
them to first-class operator-controlled plants — same enable /
disable / per-kind count / target controls the L1 plants get.

The architectural argument: SPEC's `D3 — Dogfood gap close` frames
three parallel round-trips (L2 / ETL / Training). The Training loop
is supposed to teach an End User what each violation kind looks
like and what to do about it; restricting the Training surface to
L1-only artificially halves the curriculum. An End User who reads
"phantom_rail" in a triage card needs the same plant-walk-toggle
flow the L1 invariant kinds get.

**Rejected variants:**
- **Keep L2 plants on `/etl/` only as a side-effect of Run.** The
  status quo: invisible to the Trainer, no per-kind control, no
  before/after toggle. Loses the curriculum claim entirely.
- **Spin up a separate `/training-l2/` surface for the L2 kinds.**
  Splits the persona surface again right after BS.0 collapsed it.
  The Trainer's job is "pick a violation, walk a trainee through
  it" — the L1 vs L2 origin is implementation detail, not a
  persona axis.
- **Fold L2 plants into a single `etl_gaps` mega-card** rather than
  per-kind cards. Loses the per-kind page's form (each L2 plant
  has different primitives — count, name string, target dropdown);
  loses the per-kind tour mapping (3 kinds tour Triage, 2 kinds
  tour Coverage). Same anti-pattern Lock 2 rejects for L1.

**Cross-references:**
- `common/l2/demo_etl_gaps.py` — the 5 plant functions + the
  composition wrapper.
- `common/l2/triage.py::detect_gaps` — the gap detector that
  surfaces 3 of the 5 plants on `/etl/triage`.
- `common/l2/coverage.py::coverage_for` — the Coverage card data
  source for the other 2 plants.
- SPEC's D3 (`Dogfood gap close`) — the three-round-trip
  architectural framing that motivates union scope.

## Lock 1: landing pattern — per-kind violation grid, not Refresh→Triage→Probe numerals

**Updated 2026-05-30 round-3 scope:** card count expanded 17→21
(15 L1 + 5 L2 ETL-feed + 1 dual-listing — Inbound/Outbound limit
breach are sub-tabs of one card per Lock 7 — net 21 unique
plantable entry points). Family count expanded 7→8 (added `L2FT
Hygiene` for the 4 round-3 plants — distinct family from `L2
Triage` because tour destination differs: L2FT Hygiene tours the
L2 Flow Tracing app's L2 Hygiene Exceptions sheet, NOT
`/etl/triage`). Accordion grid pattern unchanged — operator's
"pick one" mental model holds.

**Decision:** the `/training/` landing renders a 21-card grid (one
card per registry entry from Lock 7 — drives directly off the §0.5
matrix), sub-grouped by violation family. NOT the numbered-card
linear-flow pattern BTa.3 uses on `/etl/`. Card chrome, accordion
chrome, status pill, CTAs all data-drive off the Lock 7 registry
entry — no per-kind HTML.

ETL Support's three pages model a strict loop (Refresh → Triage →
Probe — each step has a single sensible "next"); the numbered cards
+ `→` arrows BTa.0 Lock 2 codified read as a sequence because they
ARE a sequence. Training has no such sequence: a Trainer picks
"which violation am I demoing today?" and dives into the per-kind
flow for that one. The first action is selection-from-many, not
step-1-of-3. A 17-card grid surfaces the selection axis directly,
and the L1-vs-L2 family split reinforces the "pick one" framing
(the operator's first sub-decision is "L1 invariant or L2 feed
contract?" before they pick a kind).

**Shape:**
- Page header: `Training` h1 + `<prefix>` mono badge (matches `/etl/`
  header chrome).
- A short intro paragraph: "Plant controlled invariant violations
  into the demo DB, then walk an End User through how each one
  surfaces on the dashboards. Each card describes one L1 invariant
  kind; click 'Plant this' to overlay it, or 'Take the tour' for the
  guided before/after."
- A "Reset to baseline" affordance pinned top-right of the page
  header (Lock 4 owns the semantics; the trigger lives here).
- Per-family sub-headers, default-collapsed accordion shells (Lock 3
  pattern reuse from BTa.4). 8 families total — 5 L1 + 3 L2 — in a
  visually-distinct two-group layout (L1 group with one stripe color,
  L2 group with another; §1 of the mockups doc owns the exact
  treatment). Per the §0.5 matrix family column:
  - **L1 invariant violations** (5 families, 15 kinds): `L1
    Conservation` (drift / ledger_drift / overdraft) / `L1 Cap`
    (limit_breach outbound + inbound + expected_eod_balance_breach) /
    `L1 Aging` (stuck_pending / stuck_unbundled) / `L1 Chain` (6
    chain coherence variants: chain_parent_disagreement +
    xor_group_missed/overlap + fan_in_missing/extra +
    multi_xor_missed/overlap) / `L1 Audit` (supersession_audit).
  - **L2 feed-contract + hygiene violations** (3 families, 9 kinds
    total — 5 ETL + 4 Hygiene): `L2 Triage` (phantom_rail /
    phantom_template / missing_metadata_key — surface
    /etl/triage) / `L2 Coverage` (uncovered_rail /
    uncovered_template — surface /etl/run Coverage cards) /
    `L2FT Hygiene` (chain_orphan / dead_bundles_activity /
    dead_metadata / dead_limit_schedule — surface the L2 Flow
    Tracing app's L2 Hygiene Exceptions sheet; NOT /etl/triage,
    which is a different surface).
  Family list IS Lock 7's registry-grouped-by-family enumeration.
  Counts per family in the section header (e.g. `L1 Conservation
  (3 kinds)`, `L2FT Hygiene (4 kinds)`).
- Inside each family, one card per invariant kind. Card shape:
  - kind badge (`drift`, `overdraft`, …) — mirrors the existing
    `_studio_training` card's `<span class="data-training__kind">`.
  - human title (from `InvariantSection.title`).
  - the SHOULD statement, one paragraph.
  - `**Action.**` remediation paragraph (from `what_to_do`).
  - Two CTAs, side-by-side:
    - `[ Plant this → ]` (Lock 2 — opens the per-kind plant page).
    - `[ Take the tour → ]` (Lock 3 — opens the per-kind tour page).
  - A status pill: `● not planted` / `● planted (last refresh
    14:23)` / `⚠ planted but reset since` (the third state appears
    when the operator planted then refreshed/reset). State sources
    from a small `<prefix>_training_state` KV row or a process-local
    cache — implementation picks, both viable.
- A side-panel `[?]` trigger per card on the kind badge (per BTa.0
  Lock 1 reuse pattern) — opens the same glossary entry the
  dashboard sheet-bottom panel uses, so the Trainer can refresh
  their memory mid-flow.

**Rejected variants:**
- **Numbered 3-card "Browse → Plant → Tour" flow.** Same shape as
  `/etl/`. Loses the "pick which invariant" axis — a single "Browse"
  card would just deep-link into the grid anyway. The Trainer's
  first decision IS selection-from-12; the landing should expose
  that selection directly.
- **One mega-page listing every kind flat, no families.** Spent
  variant — the existing pane on `/data` does this, and it's hard
  to scan when the operator's looking for one specific kind. Family
  sub-grouping is the same group-by-kind move BTa.0 Lock 3 made on
  Triage.
- **Sidebar rail with kinds + content pane on the right.** Burns
  horizontal space; doesn't degrade as well on narrow viewports; and
  every card's plant CTA + tour CTA already wants horizontal
  breathing room. The accordion grid pattern reuses BTa.4 chrome,
  keeps the surface flat, and matches the operator's "pick one"
  mental model.
- **Defer landing entirely + redirect `/training/` straight to first
  kind's plant page.** Discards the selection step; the Trainer
  rarely wants the same kind every session, and arriving on
  `drift`'s page when you came to demo `overdraft` is confusing.

**Cross-references:**
- `_studio_training.py::render_training_pane` for card structure +
  the existing `_L1_KIND_TO_SHEET_ID` mapping (re-used by Lock 3's
  tour).
- `common/handbook/invariants.py::InvariantSection` for the
  per-kind text fields each card renders.
- BTa.4's accordion pattern (`bta_design_mockups.md` §3.2 / 3.3)
  for the per-family default-collapsed shell.

## Lock 2: plant-picker UX — per-kind page with one form, NOT a single global picker with kind chips

**Decision:** each invariant kind owns its own page at
`/training/plant/<kind>` rendering one form scoped to that kind's
plant primitive(s). NOT a single `/training/plant` page with chips
or tabs to select the kind.

The plant primitives in `common/l2/auto_scenario.py` are
heterogeneous — `DriftPlant` takes `(account_id, days_ago,
delta_money, rail_name, counter_account_id)`; `LimitBreachPlant`
takes a different shape; `SupersessionPlant` carries an
`original_amount`/`corrected_amount` pair; the chain plants carry
`chain_parent_rail_name`/`child_template_name`. A single form
trying to host every shape via conditional fields will turn into
the same anti-pattern BF.x fixed in the L2 editor (one mega-form
collapsing under its own conditional weight). One page per kind
gives every primitive its own typed UI, no cross-kind field
collisions, no "is this field active for this kind?" branching.

The flip side — "but then the operator has 12 pages to learn" — is
mitigated by Lock 1's grid landing (every kind reachable in one
click) + a left-edge "Other kinds:" rail on each plant page (the
same kind list, current kind underlined; one click switches).

**Shape:**
- URL: `/training/plant/<kind>` where `<kind>` is one of
  `_DISPLAY_ORDER`'s 12 values.
- Page header: `Plant: <Human title>` + the SHOULD blockquote
  underneath, so the Trainer sees what they're planting + why.
- A `← Back to Training` sticky breadcrumb (Lock 4's `?from=`
  pattern carried over from BTa.0).
- The form itself, fields-per-kind. Most fields default to
  auto-scenario's heuristic picks (BU.2 — "expose existing plants"
  is literally exposing `default_scenario_for`'s pickers as
  pre-populated form values). The operator can override any value;
  defaults make "just plant something sensible" a one-click
  operation.
- A "Plant defaults preview" expandable details block: shows the
  picker's reasoning ("Picked rail `ACHCredit` because it's the
  first 2-leg Rail whose destination_role matches the template
  role"). Surfaces what `default_scenario_for` does so the Trainer
  doesn't have to reverse-engineer it from the source.
- Primary CTA: `[ Plant + refresh → ]`. Runs the plant overlay +
  triggers a matview refresh (so the result surfaces on the
  dashboards immediately). Toast on success: `Planted ✓ — view on
  L1 Dashboard ▸` (the link opens the App2 sheet for the kind,
  per the existing `_L1_KIND_TO_SHEET_ID` map).
- Secondary CTA: `[ Take the tour with these settings → ]`. Hand
  off into Lock 3's tour with the form values pre-populated, so the
  operator can preview the dashboard before/after without leaving
  the trainer flow.
- Left-edge rail: per-kind list of all 12 kinds. Current kind
  highlighted. Click switches to that kind's plant page (preserving
  any `?from=` breadcrumb).

**Rejected variants:**
- **One global `/training/plant` picker with kind chips switching
  the form inline.** Same form, conditional fields per kind = the
  "every field branches" mess noted above. Per-kind URLs also give
  the Trainer a bookmarkable surface: "send the colleague the link
  to the drift planting page" is one URL, not "go to
  `/training/plant` and pick drift from the chip row."
- **Plant via a modal on the landing page.** Operator dislikes
  modals (`[[feedback_no_modals]]` — codified BTa.0 Lock 1's
  rejection of modals). A modal also fights the "show me the
  defaults' reasoning" expandable details block that belongs
  alongside the form.
- **No form at all — just one `[ Plant defaults ]` button per kind
  on the landing.** Loses the override axis. The operator may want
  to plant drift on a specific customer account, or with a
  specific delta_money, to mirror a real incident from their
  bank's history. Pre-populated form + override is the right
  cost/benefit.

**Cross-references:**
- `common/l2/auto_scenario.py::default_scenario_for` (pickers per
  plant primitive) — the per-kind form's default values source.
- `common/l2/auto_scenario.py::filter_scenario_plants` — the
  existing kind-subset projector. BU re-uses this to apply one
  plant kind at a time (passes `kinds=("drift",)` for the drift
  page's submission, etc.).
- BTa.2.5 `?from=` breadcrumb pattern (BTa.0 Lock 4).

## Lock 3: tour-mode UX — embedded App2 iframe with before/after toggle, NOT side-by-side panes

**Updated 2026-05-30 round-3 scope:** tour destination is now a
field on the Lock 7 registry entry, NOT a hand-written mapping
table in this doc. The mapping below survives as a
human-readable index of "what the registry's
`tour_destination` column needs to evaluate to per kind." When
adding a new violation kind, edit the registry row — this doc
stays in sync because §0.5 + Lock 7 are the canonical sources.

Round-3 also disambiguates L2 Triage from L2FT Hygiene — round-2's
mapping treated them as the same surface, which was wrong. Per-
category convention:
- **L1 → App2 L1 Dashboard sheet** (via
  `_L1_KIND_TO_SHEET_ID`-derived URL).
- **L2 Triage** → `/etl/triage` (operator's ETL-debug surface).
- **L2 Coverage** → `/etl/run?failures-only=1#coverage-*` (operator's
  ETL-coverage surface).
- **L2FT Hygiene** → App2 L2 Flow Tracing app's L2 Hygiene
  Exceptions sheet (`/dashboards/l2_flow_tracing/sheets/l2ft-sheet-l2-exceptions`).
  This is a dashboard sheet operators look at; `/etl/triage` is an
  integrator-debug page. Distinct audiences, distinct URLs. Two of
  the 4 L2FT Hygiene plants light up dual surfaces (phantom_rail
  also fires Triage's `unmatched_rail` check; uncovered_rail also
  drops the Coverage card to ✗) — these get a secondary tour link
  in the "What to point out" callout, not a different primary
  destination.

**Decision:** the tour at `/training/tour/<kind>` renders ONE
destination page in an embedded iframe, with a top control strip
that toggles between "before" (baseline, no plant) and "after"
(plant overlaid + matview refreshed where applicable). NOT side-
by-side iframes. Destination URL is derived per-kind from a
locked mapping table.

Side-by-side comparison sounds compelling but breaks for three
reasons:
1. App2 dashboard sheets are vertically dense — a single sheet eats
   most of a typical viewport's height. Two side-by-side iframes at
   half-width clip nearly everything inside each.
2. App2's interactive filters (date range, rail picker, slider) are
   per-iframe — the Trainer would have to apply every filter twice
   to keep the two views in sync. Single-iframe-toggle keeps the
   Trainer's filter state intact across before/after; only the
   underlying data changes.
3. The pedagogical claim — "here's the SAME view, with vs without
   this violation" — reads cleaner when the visual literally swaps
   in place. Two iframes ask the operator to look-here-then-there;
   one iframe just flickers the relevant chart cells when the
   toggle flips.

The chosen pattern: embedded iframe (URL per locked mapping table
below; mounted inline rather than opened in a new tab) + a top bar
with `[ ◯ Before  ●  After ]` toggle + a one-paragraph caption
strip below the toggle that explains what changed.

**Tour-destination mapping (sourced from Lock 7 registry):**

The full per-kind destination is one column on the registry
(`tour_destination`). The table below renders the registry's
projection on that column — when adding a new kind, you add a
registry row, NOT edit a hand-written table.

| Plant kind                                    | Category      | `tour_destination` URL                                                          | Secondary tour link (callout)                  |
|-----------------------------------------------|---------------|---------------------------------------------------------------------------------|-----------------------------------------------|
| `drift`, `ledger_drift`                       | L1            | `/dashboards/l1_dashboard/sheets/l1-sheet-drift`                                | —                                              |
| `overdraft`                                   | L1            | `/dashboards/l1_dashboard/sheets/l1-sheet-overdraft`                            | —                                              |
| `limit_breach_outbound`, `limit_breach_inbound` | L1          | `/dashboards/l1_dashboard/sheets/l1-sheet-limit-breach`                         | —                                              |
| `expected_eod_balance_breach`                 | L1            | `/dashboards/l1_dashboard/sheets/l1-sheet-todays-exceptions`                    | — (no dedicated sheet exists)                 |
| `stuck_pending`                               | L1            | `/dashboards/l1_dashboard/sheets/l1-sheet-pending-aging`                        | —                                              |
| `stuck_unbundled`                             | L1            | `/dashboards/l1_dashboard/sheets/l1-sheet-unbundled-aging`                      | —                                              |
| `chain_parent_disagreement`, all chain coherence variants | L1 | `/dashboards/l1_dashboard/sheets/l1-sheet-todays-exceptions`                    | —                                              |
| `supersession_audit`                          | L1            | `/dashboards/l1_dashboard/sheets/l1-sheet-supersession-audit`                   | —                                              |
| `phantom_rail`                                | L2 Triage     | `/etl/triage` (lands in `unmatched_rail` section)                               | L2FT Hygiene Exceptions sheet (Unmatched Rail) |
| `phantom_template`                            | L2 Triage     | `/etl/triage` (lands in `unmatched_template` section)                           | —                                              |
| `missing_metadata_key`                        | L2 Triage     | `/etl/triage` (lands in `missing_metadata_key` section)                         | —                                              |
| `uncovered_rail`                              | L2 Coverage   | `/etl/run?failures-only=1#coverage-rails`                                       | L2FT Hygiene Exceptions sheet (Dead Rails)     |
| `uncovered_template`                          | L2 Coverage   | `/etl/run?failures-only=1#coverage-templates`                                   | —                                              |
| `chain_orphan`                                | L2FT Hygiene  | `/dashboards/l2_flow_tracing/sheets/l2ft-sheet-l2-exceptions#chain-orphans`     | —                                              |
| `dead_bundles_activity`                       | L2FT Hygiene  | `/dashboards/l2_flow_tracing/sheets/l2ft-sheet-l2-exceptions#dead-bundles`      | —                                              |
| `dead_metadata`                               | L2FT Hygiene  | `/dashboards/l2_flow_tracing/sheets/l2ft-sheet-l2-exceptions#dead-metadata`     | —                                              |
| `dead_limit_schedule`                         | L2FT Hygiene  | `/dashboards/l2_flow_tracing/sheets/l2ft-sheet-l2-exceptions#dead-limits`       | —                                              |

The `?failures-only=1` deep-link is critical for the uncovered-*
kinds — the Coverage card displays every declared rail/template, and
a planted ✗ buried among 30 ✓s is invisible at a glance. The BTa.6
toggle filters to failures-only so the operator's eye lands on the
planted ✗ row directly. (Mockup §3 confirms the iframe inherits the
query string cleanly.)

**Before/After semantics per category:**

The Before/After toggle does roughly the same thing across both
categories, but the visual delta reads differently:

- **L1 plants:** the dashboard sheet has KPIs + charts that
  literally swap values (KPI goes from "0 drifted accounts" to "1";
  chart adds a bar). High-contrast visual delta; the toggle pattern
  delivers maximum pedagogical value here.
- **L2 plants on /etl/triage:** the page is a list-of-cards, no
  chart that animates. Before = the section is empty ("No
  unmatched rails detected ✓"); After = a new card appears with
  the diagnosis + evidence. Lower-contrast delta but still clear —
  "section was empty, now has a card." §7 Q4 captures the
  question of whether to keep the toggle pattern here vs. just
  showing the After state.
- **L2 plants on /etl/run Coverage:** the card flips one
  ✓ → ✗ row. With `failures-only=1` engaged, the Before state shows
  "All rails covered ✓ (28 of 28)" + an empty list; After shows
  "27 of 28 covered" + the one ✗ row. Clear delta even though it's
  a list page.

**Shape:**
- URL: `/training/tour/<kind>`.
- Page header: `Tour: <Human title>` + the SHOULD statement.
- Tour control strip (sticky just under the header):
  - `[ ◯ Before  ●  After ]` toggle (two-state pill).
  - A "What you're looking at" caption that updates with the
    toggle:
    - Before: "Baseline demo data. The <sheet name> shows N rows;
      none of them violate the <kind> invariant."
    - After: "After planting one <kind> violation. The <sheet>
      now shows N+1 rows; the new row is highlighted." (The
      highlight bit may not be feasible in a hands-off iframe —
      see §7 Q4 in the mockups doc.)
  - A `[ Re-plant ]` button that re-runs the plant overlay (e.g.
    after a user edits the plant params on the plant page +
    bounces back). Disabled in `Before` state.
  - A `[ Done ]` button that returns to the landing page.
- The iframe itself, full viewport-width, ~70vh tall.
- Below the iframe, a "What to point out to your trainee" callout
  card. Bullet list of the specific cells/visuals the Trainer
  should walk through. Sourced from a `tour_notes` field on
  `InvariantSection` (new — BU augments `L1_Invariants.md`).
- Backend: toggling `Before`→`After` runs the plant overlay +
  matview refresh in-process (same code path the plant page's
  primary CTA uses); toggling `After`→`Before` runs a reset (Lock 4)
  so the iframe re-renders against clean data.

**Dashboards-renderer integration locked: App2 only.**
The `_studio_training` pane already documents this (module
docstring lines 17-22): App2's URL-param control-sync defect
(`[[project_qs_url_parameter_no_control_sync]]`) breaks deep-link
narrowing on QS, so trainer→trainee handoff only works cleanly
through the self-hosted renderer. The tour iframe inherits the
same constraint — the tour deep-links the dashboard sheet with
filter parameters set; if QS dropped the filter narrowing the
"after" view would show ALL the data, not just the planted
violation, defeating the point.

**Rejected variants:**
- **Side-by-side iframes.** Vertical-density + filter-sync +
  visual-flow reasons above.
- **Open the dashboard in a new tab (current behavior).** Loses
  the embedded-tour flow; the Trainer ping-pongs between tabs and
  has to manually narrate "click back to the trainer tab to see
  the after state." Single-page tour keeps the flow contiguous.
- **Render the dashboard sheet's HTML inline (no iframe).** App2
  dashboard sheets carry their own theme + JS + filter chrome; the
  trainer page would inherit all of it and have to wrestle with
  z-index, theme inheritance, and filter-state collisions. Iframe
  is the cheap isolation boundary.
- **Animated step-through (auto-advance through "before" → plant
  → "after" with a 3s pause).** Operator probably wants control
  over pacing; auto-advance gets in the way during a real demo to
  a trainee.

**Cross-references:**
- `_studio_training.py::_L1_KIND_TO_SHEET_ID` — the kind→sheet map
  the L1 iframe URL builder uses.
- `cli/_html_serve.REAL_APPS` — the App2 dashboard slug
  (`l1_dashboard`).
- `common/html/_studio_routes.py` — the `/etl/triage` + `/etl/run`
  route handlers that host the L2 tour destinations.
- `common/l2/triage.py::detect_gaps` + `common/l2/coverage.py::coverage_for`
  — the data sources behind the L2 tour iframes.
- BU.5's "before/after dashboard tour" SPEC ask: `SPEC.md` line
  ~360-362.
- BTa.6's `?failures-only=1` toggle on the Coverage card.

## Lock 4: reset semantics — destructive `truncate + reseed baseline`, NOT undo-the-last-plant

**Decision:** `Reset to baseline` runs the same code path as
`Refresh Data` on `/etl/run` — truncate(demo_db) → ETL hook (or
bundled generator) → matview refresh. It wipes EVERY plant the
Trainer has overlaid this session and returns to the seed-baseline
state. NOT a per-plant undo or a snapshot/restore.

Per-plant undo would require tracking the exact INSERT rows each
plant produced (transaction_ids, daily_balances rows) and emitting
DELETE statements that undo them precisely — and it would have to
get the dependency ordering right when multiple plants overlap
(e.g. a chain plant + a parent-rail plant share rows).
Snapshot/restore is the SQL-level equivalent and is feasible in
SQLite (file copy) but expensive on Postgres/Oracle without
backend-specific dump/restore plumbing — `[[workflow.4
snapshot/restore around Run]]` deferred from BTa for exactly this
reason.

Truncate-and-reseed is destructive but unambiguous: one button,
one outcome, no edge cases. It IS what `/etl/run` already does;
re-using that code path keeps the surface honest.

**Shape:**
- `Reset to baseline` button pinned to the top-right of every
  `/training/*` page header. (NOT just the landing — a Trainer
  mid-tour may want to reset without backing out to the landing.)
- Click behavior: NO confirm modal. The button is wide enough to
  read as deliberate (operator dislikes modals per BTa.0 Lock 1's
  same reasoning); the reset is fast (matches the `/etl/run`
  Refresh duration ~10s); and if the operator misclicks, the
  remediation is "plant the kind again," which is one click. The
  modal-aversion goes both ways: confirms ARE friction the
  operator rejected.
- Post-click flash: `✓ Reset to baseline — N plants removed
  (~12.4s)`. Auto-dismisses after 10s. Same flash pattern BTa.6
  introduced for `/etl/run`.
- Disabled state: while a reset OR a plant is in flight, the
  button shows a spinner + `Resetting…` / `Planting…` and rejects
  clicks. Browser-tab title pulse on completion (same pattern as
  `/etl/run` per BTa.0 §7 Q4).
- Affordance for the cautious operator: the button styling is the
  same `text-warning border-warning` shape `/etl/run`'s "Refresh
  Data" uses — it reads as a destructive-but-frequent action,
  not as a primary CTA.

**Cross-references:**
- `/etl/run`'s Refresh Data code path (`_studio_routes.py::_render_etl_run_page`
  + the trigger plumbing) — Reset to baseline calls the same
  function. The button just lives on a different page.
- `[[feedback_no_modals]]` — codified operator preference.
- BTa.0 Lock 2 (the rejected variants discuss the same modal
  question for `/etl/run`).

**State derivation:** the "● planted (last refresh)" status pill
on the landing page (Lock 1) reads the same `<prefix>_training_state`
KV row the plant page writes on success. Reset clears the KV row;
landing flips every card's pill back to `● not planted`.

## Lock 5: subsume the existing `/data` right-column pane, NOT split

**Decision:** the current trainer pane on `/data` (rendered via
`_studio_routes._render_data_page` at line 3853, content from
`_studio_training.render_training_pane`) is **removed** as part of
BU.4. The right column of `/data` reverts to either empty (single-
column layout) or to whatever ETL-debugging widget makes sense
there for the ETL Engineer persona (out of scope for BU — defer to
whoever revisits `/data`'s mode after Training is gone).

The current pane's content + chrome migrates to the
`/training/` landing (Lock 1). The deep-link CTAs on each card
already point at App2 dashboard sheets via the constants in
`_studio_training.py`; those constants move to the new
`/training/` route module wholesale.

The split alternative — keep both — has two failure modes:
1. **Drift between the two surfaces.** Cards get added to one and
   not the other; the SHOULD-statement wording diverges; the
   `_L1_KIND_TO_SHEET_ID` map lives in two places. The
   `_studio_training` module's existing test guardrails would have
   to be doubled. Operator's `[[feedback_quirks_log_ever_growing]]`
   posture — "fix at the seed" — applies here too.
2. **Persona confusion.** The `/data` page is the ETL Engineer's
   surface (`SPEC.md` line 60-65); putting a Trainer-targeted pane
   on it conflates personas. BS.0 Lock 2's "flat top-nav across
   the whole binary" already split the personas at the URL layer;
   the pane is leftover scaffolding from the pre-split era.

**Shape (the removal):**
- `_studio_routes._render_data_page` drops the `<section id="data-training">`
  block (lines 3853-3855) + the `render_training_pane()` import.
- The `<main>` grid drops back to single-column (or whatever the
  new `/data` layout decides — outside BU's scope).
- `_studio_training.py` becomes the BU module's helper — its
  rendering primitives + the kind→sheet map are reused; the
  module rename to `_studio_training_card.py` (or similar) is a
  cleanup task in BU.4. Or the module is deleted outright and its
  contents fold into the new `common/html/_training/` package.
  Implementation picks.
- Tests pinning the `/data` page renders the trainer pane
  (`tests/unit/test_studio_training_pane.py` if it exists — verify
  in BU.4) get updated to assert the pane is *absent* on `/data` +
  *present* on `/training/`.

**Rejected variants:**
- **Keep both pages, sync content via a shared render call.** The
  shared call is `render_training_pane()` today; cost of keeping
  both surfaces showing the same content is the persona-confusion
  cost above. Even with zero drift, the pane on `/data` doesn't
  belong to the ETL Engineer's job-to-be-done.
- **Keep the pane on `/data` as a "quick reference" + put the
  full Training surface at `/training/`.** Compromise that pleases
  no-one — ETL Engineer still sees a Trainer-targeted pane (no
  win); Trainer still has to navigate from `/training/` for
  anything beyond a glance (no win). Cleanest call is full
  migration.
- **Add a "Take me to Training" link on `/data` where the pane
  was.** Adds a navigation crumb but burns vertical space on
  `/data` for a single-purpose link. The top-nav `Training` entry
  is enough; the link would be chrome-redundant.

**Cross-references:**
- `_studio_routes.py:3765, 3853-3855` — current pane mount points.
- `_studio_training.py` — full module migrates / is deleted.
- `SPEC.md` D2: "URL split + flat top-level nav" — the architectural
  basis for treating `/data` and `/training/` as distinct surfaces.

## Lock 6: cleanup/reset semantics — L2 plants ride the same reset code path as L1, despite different integration points

**Updated 2026-05-30 round-2 scope:** new lock. The round-1 Lock 4
(reset semantics) was written L1-only; this lock confirms the same
truncate-and-reseed semantics work cleanly for L2 plants too,
despite their different integration point (raw DML overlay vs
emitter-pipeline output).

**Decision:** `Reset to baseline` runs the same code path for both
plant categories — truncate(demo_db) → ETL hook (or bundled
generator) → matview refresh. Both L1 plants (baseline + overlay
emitter pipeline) and L2 plants (post-pipeline INSERT/DELETE
overlays from `demo_etl_gaps.py`) get wiped by the truncate step;
the reseed re-emits the baseline alone, without any plant overlays.
No per-plant DELETE bookkeeping needed for either category.

**Why this works despite the integration-point difference:**

- **L1 plants** are `ScenarioPlant` dataclass entries that
  `emit_full_seed` interpolates into the baseline seed SQL. They
  ship as INSERT statements alongside the generator's baseline
  rows; truncate wipes both equally.
- **L2 plants** are raw INSERT/DELETE statements `demo_etl_gaps.py`
  emits AFTER `emit_full_seed` finishes. They mutate the same
  `<prefix>_transactions` table the baseline lives in; truncate
  wipes the table, and re-running the baseline emit alone leaves
  no L2 plant residue.

The key invariant: BOTH categories live entirely inside
`<prefix>_transactions` (+ `<prefix>_daily_balances` for L1
balance plants). Truncate-and-reseed is the universal solvent.

**Partial-state-on-cancel framing applies to L2 plants too:**

The round-1 Lock 4 noted that mid-action cancel isn't supported —
once `[ ⚡ Plant + refresh → ]` fires, the server runs to
completion. Same applies to L2 plants. An `uncovered_rail` plant
runs a DELETE that can't be partially undone mid-flight; a
`phantom_rail` plant runs N INSERTs that complete-or-not. The
in-flight progress bar + button disabling pattern from Lock 4
covers both cases uniformly.

**The one asymmetry — uncovered-* plants on real-hook deployments:**

The L2 `uncovered_rail` / `uncovered_template` plants run a DELETE
against the demo DB. On a deployment with `cfg.etl_hook` wired, the
hook may immediately re-populate the deleted rows on the next
refresh cycle (especially if it's a streaming hook). This is the
mirror of the round-1 Lock 4 caveat about reset re-running the
hook: a real hook's "source of truth" wins. Side-panel caption on
the L2 plant page should document this for the `uncovered_*` kinds:
"Your ETL hook may re-emit these rows on the next refresh. For a
durable demo, plant + tour in a single session." (§7 Q5 of the
mockup doc captures the operator confirmation.)

**Rejected variants:**

- **Per-plant undo for L2 (track INSERT/DELETE row IDs +
  reverse).** Same complexity as the L1 per-plant undo round-1
  rejected. The `uncovered_*` DELETEs are the hardest case — undoing
  a DELETE requires re-synthesizing the deleted rows from the
  generator, which IS what truncate-and-reseed does.
- **Snapshot/restore as a per-category opt-in (L1 = truncate, L2 =
  snapshot the affected rows).** Two reset paths = two failure
  modes + two test surfaces. Pick one.

**Cross-references:**
- Lock 4 (the L1-side reset framing this lock generalizes).
- `common/l2/demo_etl_gaps.py::emit_demo_etl_gap_sql` — the
  composition wrapper.
- `cli/_helpers.build_default_scenario` — the baseline emit chain
  reset re-runs.

## Lock 7: all violation kinds plug into ONE shared registry; UI + plant invocation + tour destinations data-drive off it

**Updated 2026-05-30 round-4 scope:** registry is a THIN INDEX —
`PlantKindEntry` no longer carries display strings (title,
short_statement, action remediation, columns). Those resolve at
render time from the typed violation-class section indexed by
`entry.kind`. See Lock 8 for the full rationale + rendering
pattern; this lock now describes the registry's NON-display fields
only (plant_function, primitives, tour_destination,
dashboard_check). Net: registry sketch shrinks from ~10 fields to
~6; the 3 display fields move to `InvariantSection` /
`L2FTExceptionSection` / `L2TriageGapSection` (the latter built in
Lock 10).

**Updated 2026-05-30 round-3 scope:** new lock. Operator's
directive: "A key thing in my mind is how can we support all
these violations with minimal supporting infrastructure
duplication." The round-2 design implicitly assumed one bespoke
plant page + form + render path per kind (12 pages → 17 pages
when L2 entered → would have been 21 with round-3's L2FT plants).
Each new violation kind would mean new HTML, new controller, new
test. Wrong cost curve.

**Decision:** ONE shared registry maps violation kinds to
operator-controlled metadata. The Trainer UI is a thin shell that
data-drives off the registry. Adding a new violation kind = adding
ONE row to the registry + writing ONE plant function (if no
existing primitive covers it). Zero new UI files. Zero new
controllers. The form, the tour iframe, the landing card, the
sub-nav strip — all one shared render path per shape.

**Registry shape:**

```python
# common/l2/plant_registry.py — new module (BU.2 lands)

class PlantCategory(StrEnum):
    L1_INVARIANT = "l1_invariant"       # surfaces on L1 dashboard
    L2_TRIAGE = "l2_triage"             # surfaces on /etl/triage
    L2_COVERAGE = "l2_coverage"         # surfaces on /etl/run Coverage
    L2FT_HYGIENE = "l2ft_hygiene"       # surfaces on L2FT L2 Hygiene Exc sheet


@dataclass(frozen=True, slots=True)
class PrimitiveField:
    """Base type for one form field. Subclassed per shape."""
    name: str            # form-field name
    label: str           # human label
    help_text: str       # caption under the field
    default_picker: Callable[[L2Instance], Any]  # heuristic for the pre-populated value


@dataclass(frozen=True, slots=True)
class PrimitiveStringField(PrimitiveField):
    """Free-text string. Renders as <input type="text">."""
    pass


@dataclass(frozen=True, slots=True)
class PrimitiveIntField(PrimitiveField):
    """Integer; renders as <input type="number">. Optional min/max."""
    min_value: int | None = None
    max_value: int | None = None


@dataclass(frozen=True, slots=True)
class PrimitiveDecimalField(PrimitiveField):
    """USD amount; renders as <input type="number" step="0.01">."""
    pass


@dataclass(frozen=True, slots=True)
class PrimitiveDropdownField(PrimitiveField):
    """Single-select from an L2-instance-derived value set.
    Renders as <select>; options sourced from L2."""
    options_source: Callable[[L2Instance], list[tuple[str, str]]]
    # ^ returns [(value, label), ...]


@dataclass(frozen=True, slots=True)
class TourDestination:
    """Where the tour iframe points."""
    primary_url_template: str            # template; {kind}, {form_<field>} expand
    secondary_links: tuple[tuple[str, str], ...] = ()  # (label, url) callout links


@dataclass(frozen=True, slots=True)
class PlantKindEntry:
    """Thin index — display strings live on the typed section
    referenced by ``kind`` (see Lock 8). Renderer pattern:
    ``section = resolve_section(entry)`` then read
    ``section.title`` / ``section.short_statement`` /
    ``section.what_to_do``.
    """
    kind: str                            # canonical machine name; matches §0.5 col
    category: PlantCategory
    family: str                          # drives accordion; uniformly L1/L2/L2FT
    plant_function: Callable[..., str]   # the function that returns the SQL/DML
    primitives: tuple[PrimitiveField, ...]
    tour_destination: TourDestination
    dashboard_check: DashboardCheck      # Lock 9 — parameterized e2e contract
    # NO display strings here — pulled from the violation class at render time.


PLANT_REGISTRY: Final[tuple[PlantKindEntry, ...]] = (
    # 21 entries — one per row in the §0.5 matrix.
    PlantKindEntry(
        kind="drift",
        category=PlantCategory.L1_INVARIANT,
        family="L1 Conservation",
        plant_function=_invoke_drift_plant,        # adapter into emit_full_seed
        primitives=(
            PrimitiveDropdownField(
                name="account_id", label="Account",
                help_text="Picked: first template instance materialized ...",
                default_picker=lambda l2: _pick_template(l2).account_id,
                options_source=_list_internal_accounts,
            ),
            PrimitiveIntField(name="days_ago", label="Days ago", ...),
            PrimitiveDecimalField(name="delta_money", label="Delta money", ...),
            PrimitiveDropdownField(name="rail_name", label="Rail", ...),
            PrimitiveDropdownField(name="counter_account_id", ...),
        ),
        tour_destination=TourDestination(
            primary_url_template="/dashboards/l1_dashboard/sheets/l1-sheet-drift",
        ),
        dashboard_check=DashboardCheck(
            matview_name="{prefix}_inv_drift",
            min_row_count=1,
        ),
    ),
    # ... 20 more entries ...
)
```

The 21 entries match the §0.5 matrix one-for-one. The form-field
primitives map directly to each plant function's keyword args
(per `seed.py`'s Plant dataclass field list, or `demo_etl_gaps.py`'s
function signature for L2 plants). Display strings — title,
SHOULD-statement, `**Action.**` remediation — come from the
typed section resolved per Lock 8.

**Locks on the UI side:**

- **ONE registry module** — `common/l2/plant_registry.py` (new in
  BU.2). 21 entries total. Adding a kind = adding ONE entry +
  (if needed) ONE typed-section row in the corresponding handbook
  catalogue.
- **ONE `_render_plant_page(entry: PlantKindEntry)` function** for
  every plant page (`/training/plant/<kind>`). The function
  resolves `section = resolve_section(entry)` (an
  `InvariantSection` / `L2FTExceptionSection` /
  `L2TriageGapSection` per the kind's source — see Lock 8),
  renders the page title as `<h1>{section.title}</h1>`, the
  SHOULD blockquote as `{section.short_statement}` (when present),
  the body as `{section.body}`, the action block as
  `**Action.** {section.what_to_do}`. Then walks
  `entry.primitives` and calls per-primitive-type render helpers
  (`_render_string_field` / `_render_int_field` /
  `_render_decimal_field` / `_render_dropdown_field`); wires the
  defaults preview from `entry.plant_function.__doc__` + each
  primitive's `default_picker.__doc__`. NO per-kind controller.
  NO per-kind string in the registry.
- **ONE `_render_tour_page(entry)` function** for every tour
  (`/training/tour/<kind>`). Reads `entry.tour_destination`; iframes
  the primary URL with form values templated in; renders secondary
  links as callout pills below the iframe.
- **ONE landing-accordion renderer** — iterates `PLANT_REGISTRY`
  grouped by `entry.family`, wraps each family in the BTa.4
  accordion shell, renders each entry through ONE shared
  `_render_card(entry)` helper.
- **ONE sub-nav-strip renderer** — the strip at the top of every
  `/training/*` page iterates the registry to show "all kinds at a
  glance" + highlights the current kind. Driven by the same data.

**Per-primitive render helpers:**

| Primitive type            | HTML rendered                                                  |
|---------------------------|----------------------------------------------------------------|
| `PrimitiveStringField`    | `<input type="text" name="..." value="<default>"> <span class="help">{help_text}</span>` |
| `PrimitiveIntField`       | `<input type="number" name="..." value="<default>" min=... max=...>` |
| `PrimitiveDecimalField`   | `<input type="number" step="0.01" name="..." value="<default>">` |
| `PrimitiveDropdownField`  | `<select name="..."><option ... selected>...</option></select>` (options from `options_source(l2)`) |

The mockup doc §2 collapses to ONE canonical mockup showing the
shared shell + a small table of the 4 primitive renderers. The
21 per-kind variants no longer warrant separate mockups — the
form is fully derived from the entry.

**What this buys, concretely:**

- Adding the 4 round-3 L2FT plants = adding 4 PlantKindEntry rows
  + writing 4 new plant emitter functions (in
  `demo_etl_gaps.py`). NO new UI code. The landing card appears,
  the plant page renders, the tour page renders, the sub-nav
  shows the new kind — all from the registry row.
- Adding `expected_eod_balance_breach` (the missing L1 plant) =
  same: one entry row + one new `ExpectedEodBalanceBreachPlant`
  dataclass on `ScenarioPlant`. UI free.
- Future kinds (say, a new chain-coherence variant lands in
  `seed.py`) = one row in the registry. No mockup churn, no test
  fan-out, no per-kind controller.
- Tour destination move (someone splits Today's Exceptions into a
  dedicated `expected_eod_balance_breach` sheet later) = edit one
  field on one registry row. UI auto-rewires.

**Rejected variants:**

- **One bespoke page per kind w/ hand-written forms** (round-1 +
  round-2 implicit assumption). Cost curve is N — each new kind
  adds an HTML file, a controller, tests. The §0.5 matrix would
  surface 21 today and grow with each new check. Operator's
  directive explicitly rejects this.
- **Convention-over-config (no registry; introspect Plant
  dataclasses at import time)**. Introspecting `ScenarioPlant`
  field types via `dataclasses.fields()` + `typing.get_hints()`
  could derive primitives from the dataclass. Rejected because:
  (1) display family + action remediation + tour destination
  AREN'T on the dataclass and would need a parallel mapping
  anyway (= a registry by another name); (2) L2 plants don't go
  through `ScenarioPlant` at all (they're standalone functions in
  `demo_etl_gaps.py`) so we'd need a second mechanism; (3) the
  registry doubles as a doc-able source of truth (operator can
  grep `PLANT_REGISTRY` to see the full kind universe in one
  file).
- **Code-gen the per-kind controllers from a YAML registry** (the
  registry-but-in-YAML variant). Python dataclasses are already
  the right level — pyright catches typos, IDE autocompletes
  fields, the registry IS the code. YAML adds an extra
  parse-and-validate step with no win.

**Cross-references:**

- §0.5 violation coverage matrix — the canonical "what is in the
  registry" set.
- Lock 1 (landing) — the accordion renderer iterates the registry.
- Lock 2 (per-kind plant page) — `_render_plant_page(entry)` IS
  the page handler.
- Lock 3 (tour mapping) — `entry.tour_destination` IS the
  per-kind URL.
- `common/l2/auto_scenario.py::filter_scenario_plants` — the
  existing kind-subset projector; the registry's `plant_function`
  for L1 entries wraps this.
- BTa.4 accordion chrome — landing-renderer reuse.

## Lock 8: registry is a THIN INDEX over typed violation-class catalogues

**Updated 2026-05-30 round-4 scope:** new lock. Operator's
directive after reading round-3: "How do we keep this registry
from drifting? There's a lot of strings in that plant_registry
spec for help text, is that already in the violation space?"
Round-3's `PlantKindEntry` carried `display_name` /
`short_description` / `action_remediation` as registry fields.
That's a parallel string catalogue — every L1 invariant's
SHOULD-statement now lives in TWO places (the handbook
markdown + the registry row). The drift problem is built in:
a typo in either copy goes uncaught.

**Decision:** `PlantKindEntry` references existing typed
violation-class sections by `kind`-keyed lookup. Display
strings — title, SHOULD-statement, columns, what_to_do — come
FROM the referenced section, NOT from the registry. The registry
holds the index + plant-specific behavior only.

**Section sources per category:**

- **L1** → `InvariantSection` from `common/handbook/invariants.py`
  (existing module). Parses `src/recon_gen/docs/L1_Invariants.md`.
  Already serves the L1 dashboard sheet-bottom panels and the
  `_studio_training.py` trainer pane — proves the pattern works
  at runtime.
- **L2FT Hygiene** → `L2FTExceptionSection` from
  `common/handbook/l2ft_exceptions.py` (existing module). Parses
  `src/recon_gen/docs/L2FT_Exceptions.md`. Already serves the L2
  Hygiene Exceptions sheet panel — same pattern, parallel to L1.
- **L2 Triage + L2 Coverage** → `L2TriageGapSection` from
  `common/handbook/l2_triage_gaps.py` (**round-4-build** per
  Lock 10 — the existing labels in `_studio_routes.py::_GAP_KIND_LABELS`
  + `_GAP_KIND_EDITOR_LABELS` + the diagnosis prose generated in
  `common/l2/triage.py::detect_gaps` get consolidated into a typed
  catalogue paralleling the L1 / L2FT pattern). One module, one
  dataclass, one bundled markdown source — same `parse_l1_invariants`
  shape.

**Renderer pattern (one function across all three section types):**

```python
def resolve_section(entry: PlantKindEntry) -> SectionLike:
    """Look up the typed violation-class section for ``entry.kind``.

    Returns one of InvariantSection / L2FTExceptionSection /
    L2TriageGapSection per the entry's category. All three expose
    the same shape used by _render_plant_page: ``title``,
    ``short_statement`` (may be ""), ``body``, ``columns``,
    ``what_to_do``. Treat as structural duck typing OR define a
    common Protocol — Lock 10 picks.
    """
    match entry.category:
        case PlantCategory.L1_INVARIANT:
            return _L1_SECTIONS[entry.kind]   # InvariantSection
        case PlantCategory.L2FT_HYGIENE:
            return _L2FT_SECTIONS[entry.kind]  # L2FTExceptionSection
        case PlantCategory.L2_TRIAGE | PlantCategory.L2_COVERAGE:
            return _L2_TRIAGE_SECTIONS[entry.kind]  # L2TriageGapSection
```

`_render_plant_page(entry)` calls this once at the top, then
renders `section.title` / `section.short_statement` /
`section.what_to_do` directly. NO display string in the registry
sketch above (already shrunk in Lock 7's revised dataclass).

**Why this kills the drift problem at its root:**

- One source per kind. Operator edits `L1_Invariants.md`; the
  registry's display surface updates automatically because it
  reads through the section. No copy-paste.
- Anti-drift test (Lock 9) becomes bijectivity: every registry
  kind has a section; every section is referenced by ≥1 registry
  kind. A typo in `entry.kind` surfaces as `KeyError` on the
  next `resolve_section` call — caught by the unit test, not by
  a human at code review.
- Adding a new kind = one registry row + (if no existing section
  covers it) one new section in the markdown handbook. No
  per-kind string duplication, no SHOULD-statement drifting
  between dashboard panel + trainer card + handbook page.

**The shared-section caveat (L1 chain-coherence kinds):**

The L1 chain-coherence registry rows split each
`InvariantSection` into 2 sub-kinds (xor_group_missed /
xor_group_overlap; fan_in_missing_parent / fan_in_extra_parent;
multi_xor_missed / multi_xor_overlap; limit_breach_outbound /
limit_breach_inbound). The §0.5 matrix shows these as "shared
with overlap" / "shared with missed" / etc. Resolution: the
shared section provides the umbrella title + SHOULD; the
registry row adds a `kind_qualifier: str | None` field (e.g.
`"missed firing"` / `"overlap firing"`) that the renderer
appends to the title (`<h1>{section.title} — {entry.kind_qualifier}</h1>`).
The qualifier is the ONE display string that legitimately lives
on the registry, because it discriminates same-section sub-kinds
the markdown doesn't enumerate. Lock 9's bijectivity test
accommodates the 1-to-N section→registry mapping.

**Rejected variant — keep display strings on the registry as
"copy of canonical for fast lookup":** Solves nothing. The drift
problem IS the copy. A renderer cache (compute once, memoize)
gives the same speed without the drift surface.

**Cross-references:**

- `common/handbook/invariants.py::InvariantSection` (existing).
- `common/handbook/l2ft_exceptions.py::L2FTExceptionSection`
  (existing).
- `common/handbook/l2_triage_gaps.py::L2TriageGapSection`
  (round-4-build, Lock 10).
- Lock 7 (`PlantKindEntry` sketch — display fields removed per
  this lock).
- Lock 9 (anti-drift tests).
- Lock 11 (documentation generation — the section catalogues
  also drive `recon-gen docs export`).

## Lock 9: anti-drift = parameterized test contract over registry × typed sections

**Updated 2026-05-30 round-4 scope:** new lock. The registry's
consistency with the typed-section universe is a first-class test
contract, not a manual review or a "we'll catch it in cold-read"
hope. Operator's directive: "How do we keep this registry from
drifting?" Answer: every consistency invariant is a
parameterized test row.

**Decision:** five parameterized tests cover the registry's
consistency with every adjacent surface. All five parameterize
over `PLANT_REGISTRY` itself — adding a kind = one registry row,
zero test-code changes (the test fixture is `PLANT_REGISTRY`).

**1. Section-bijectivity (registry ↔ typed-section catalogues).**

```python
@pytest.mark.parametrize("entry", PLANT_REGISTRY, ids=lambda e: e.kind)
def test_registry_kind_resolves_to_typed_section(entry):
    section = resolve_section(entry)  # raises KeyError on miss
    assert section.title  # non-empty
    # L1 + L2FT shared-section sub-kinds: section.kind may not == entry.kind
    # (limit_breach_outbound + limit_breach_inbound share one section).
    # Accommodate via the canonical_section_kind helper.

def test_every_typed_section_has_at_least_one_registry_entry():
    referenced = {canonical_section_kind(e.kind, e.category) for e in PLANT_REGISTRY}
    for section_kind in load_bundled_invariants():
        assert section_kind in referenced or section_kind in _KNOWN_DIAGNOSTIC_ONLY
    # Same for L2FTExceptionSection + L2TriageGapSection.
```

Catches: registry typo in `kind`; new section added to markdown
but no registry row; registry row pointing at a section that got
renamed.

**2. Tour-URL liveness (registry → TestClient → real app).**

```python
@pytest.mark.parametrize("entry", PLANT_REGISTRY, ids=lambda e: e.kind)
def test_tour_destination_url_resolves(entry, studio_client):
    resp = studio_client.get(entry.tour_destination.primary_url_template.format(prefix=PREFIX))
    assert resp.status_code in (200, 302), \
        f"{entry.kind}: tour URL {entry.tour_destination.primary_url_template} returns {resp.status_code}"
```

Catches: sheet rename without updating the registry; broken
anchor IDs; misconfigured deep-link query params.

**3. Plant→matview round-trip (the round-3 dashboard_check nudge,
now a lock).**

```python
@pytest.mark.parametrize("entry", PLANT_REGISTRY, ids=lambda e: e.kind)
def test_plant_surfaces_on_dashboard_check(entry, l2_instance, db_pool):
    entry.plant_function(l2_instance, defaults=True)
    refresh_matviews(l2_instance, db_pool)
    rows = await fetch_rows(db_pool, entry.dashboard_check.matview_name)
    assert len(rows) >= entry.dashboard_check.min_row_count, \
        f"{entry.kind}: planted but {entry.dashboard_check.matview_name} returned {len(rows)} rows"
```

Catches: plant function writes to wrong table; matview SQL
silently drops the planted row shape; refresh order broken.

**4. Primitive-coverage of plant_function signature.**

```python
@pytest.mark.parametrize("entry", PLANT_REGISTRY, ids=lambda e: e.kind)
def test_primitives_cover_plant_function_kwargs(entry):
    plant_params = inspect.signature(entry.plant_function).parameters
    primitive_names = {p.name for p in entry.primitives}
    required = {p for p in plant_params if plant_params[p].default is inspect.Parameter.empty
                                            and p not in _IMPLICIT_KWARGS}  # l2, db_pool
    assert required <= primitive_names
```

Catches: plant function grows a new required kwarg; registry
forgets to add the matching primitive (the form would render
without a control for the new kwarg, and submission would
500 at the server).

**5. Documentation-freshness (when Lock 11 lands the checked-in
generated docs).**

```python
def test_docs_export_is_byte_identical_to_checked_in():
    generated = docs_generate_all_kinds(PLANT_REGISTRY)
    checked_in = (DOCS_ROOT / "violations.md").read_text()
    assert generated == checked_in, \
        "Run `recon-gen docs export` to regenerate; commit the diff."
```

Mirror of the locked-seed determinism gate. Catches: section
prose edit lands in markdown but the generated docs index gets
stale.

**Each test is parameterized over `PLANT_REGISTRY` directly** —
adding a kind = one registry row, zero test-code changes. The
test ID matches the registry's `kind`, so a CI failure surfaces
which exact kind broke.

**Cross-references:**

- Lock 7 (registry shape — `dashboard_check` field added per
  this lock).
- Lock 8 (typed sections — bijectivity gate sits on the boundary).
- Lock 11 (docs generation — Test 5's "checked-in artifact" comes
  from there).

## Lock 10: L2 Triage typed source — build it, paralleling L1 / L2FT

**Updated 2026-05-30 round-4 scope:** new lock. Two of the three
typed-section catalogues already exist (L1 via `InvariantSection`,
L2FT Hygiene via `L2FTExceptionSection`); the third (L2 Triage +
L2 Coverage) does NOT. The labels currently live in three
scattered spots:

- `common/html/_studio_routes.py::_GAP_KIND_LABELS` — short
  display labels (`"Unmatched rail_name"` / etc.).
- `common/html/_studio_routes.py::_GAP_KIND_EDITOR_LABELS` —
  per-kind editor CTA text.
- `common/l2/triage.py::detect_gaps` — per-gap-row diagnosis
  prose strings generated inline (e.g. `f"{count} rows arrived
  with rail_name=\"{value}\" but the L2 declares no Rail of that
  name."`).

There's no Columns line, no SHOULD-statement, no `**What to do:**`
remediation paragraph — operator-facing remediation prose for L2
Triage gaps lives nowhere typed today. Lock 8's renderer can't
resolve `section.what_to_do` if there's no source.

**Decision:** build `L2TriageGapSection` paralleling
`InvariantSection` + `L2FTExceptionSection`. Same pattern, same
parser shape, new bundled markdown source.

**Module + shape:**

```python
# common/handbook/l2_triage_gaps.py — new module (round-4)

@dataclass(frozen=True)
class L2TriageGapSection:
    """One parsed section from L2_Triage_Gaps.md.

    Parallel to InvariantSection + L2FTExceptionSection. L2 Triage
    + L2 Coverage entries share this section type — the family
    discriminator (Triage vs Coverage vs L2FT-secondary) lives on
    the registry's PlantKindEntry, not the section.
    """
    kind: str           # e.g. "unmatched_rail" / "uncovered_template"
    title: str          # e.g. "Unmatched rail_name"
    body: str           # prose paragraphs
    columns: tuple[str, ...]
    what_to_do: str     # remediation paragraph
    editor_cta: str     # e.g. "Open Rails editor" — was _GAP_KIND_EDITOR_LABELS
    triage_url_fragment: str | None  # e.g. "unmatched_rail" for /etl/triage#X anchor

_L2_TRIAGE_KIND_TO_SHEET: dict[str, str] = {
    # 5 entries — 3 Triage kinds + 2 Coverage kinds. Used the same
    # way INVARIANT_KIND_TO_SHEET maps L1 kinds to sheets.
    ...
}
```

**Bundled markdown source:**

New file `src/recon_gen/docs/L2_Triage_Gaps.md`, 5 sections (one
per L2 Triage + L2 Coverage kind). Operator-facing remediation
prose authored here (not in Python). Parser mirrors
`parse_l2ft_exceptions` shape — `### N. <Title>` headings, no
SHOULD blockquote (L2 gaps are runtime checks, not SHOULDs),
`**Columns:**` + `**What to do:**` lines extracted into typed
fields.

**Migration of existing labels:**

- `_GAP_KIND_LABELS` → consumed by `L2TriageGapSection.title`;
  `_studio_routes.py` reads through `load_bundled_l2_triage_gaps()`
  + drops the in-module Mapping.
- `_GAP_KIND_EDITOR_LABELS` → consumed by
  `L2TriageGapSection.editor_cta`; same migration.
- `detect_gaps`'s inline diagnosis prose stays as runtime-built
  evidence text (it carries row counts + observed values that
  vary per detection). The TYPED operator-facing remediation
  ("Decide: real rail (declare it) or wrong tag (fix the ETL)")
  moves to the markdown source's `**What to do:**` line.

**Sequencing (per §Sequencing below):** Lock 10's build IS the
BU.2a cell. Prereq for BU.3.x L2-side registry entries (the
registry's `resolve_section` for L2 Triage / L2 Coverage rows
calls `load_bundled_l2_triage_gaps()`).

**Rejected variants:**

- **Skip the typed catalogue; keep registry display strings for L2
  Triage only** (asymmetric). Violates Lock 8's "every category
  has a typed source" invariant; bijectivity test from Lock 9 can't
  fire on the L2 Triage rows. Dead end.
- **Generate the markdown FROM the existing label maps + put the
  remediation prose in Python first, migrate later.** Inverts the
  source-of-truth direction (Python → markdown rather than markdown
  → Python). Operator prefers prose in markdown for the same reason
  L1 invariants live in markdown: editable by docs writers, not
  conditional on Python edits.
- **Reuse `InvariantSection` for the L2 catalogues** (one typed
  shape for all three categories). `InvariantSection` carries a
  SHOULD `short_statement` field; L2 Triage gaps don't have
  SHOULDs (they're runtime feed-shape checks, not L1 conservation
  invariants). Forcing a SHOULD field would either be empty (then
  why is it there) or misleading (drift between "real" L1 SHOULDs
  and synthesized L2-Triage SHOULDs). Three distinct types per
  category, joined at the renderer via the section-like duck
  type / Protocol.

**Cross-references:**

- `common/handbook/invariants.py` — pattern reference.
- `common/handbook/l2ft_exceptions.py` — pattern reference.
- `common/html/_studio_routes.py::_GAP_KIND_LABELS` /
  `_GAP_KIND_EDITOR_LABELS` — migrated away.
- `common/l2/triage.py::detect_gaps` — runtime evidence-text
  generation unchanged; typed remediation extracted.
- Lock 8 (the renderer that consumes this section type).
- Lock 11 (docs export consumes this section type for the L2
  Triage handbook pages).

## Lock 11: documentation generation = registry walk + section catalogue consumption

**Updated 2026-05-30 round-4 scope:** new lock. Operator's
directive: "Once built, generating documentation off of this and
the registry being a single source of truth becomes a lot easier."
The payoff Lock 8's typed-source pattern delivers: ONE generator
produces every doc surface that needs per-kind content. No more
per-surface hand-maintained tables.

**Decision:** `PlantKindEntry` exposes a render contract
(`docs_generate_section(entry) -> str`) that produces the
canonical markdown rendering of one entry. `recon-gen docs export`
walks `PLANT_REGISTRY` and emits markdown for every consuming
surface. Five surfaces consume the registry walk:

**1. Per-violation-kind handbook page.** Already exists for L1
via `L1_Invariants.md`. Lock 11 unifies: instead of three
hand-maintained markdown files (L1 / L2FT / L2 Triage), the
generator emits one per-kind page per registry row, citing the
typed section's prose verbatim + the registry's metadata
(family, surface, plant primitives, tour destination).
**Implementation:** `src/recon_gen/docs/violations/` directory of
generated markdown, one file per kind. The bundled source
markdown (`L1_Invariants.md` / `L2FT_Exceptions.md` /
`L2_Triage_Gaps.md`) remains the SoT for prose; the generator
synthesizes per-kind pages by joining section + registry.

**2. Trainer-pane card per kind.** Already exists for L1 via
`_studio_training.py::render_training_pane`. Lock 11 unifies:
the pane renderer walks `PLANT_REGISTRY` (not just L1 invariant
kinds), each card calls `resolve_section(entry)` for its display
content. The 21 cards in BU's `/training/` landing (Lock 1) AND
the residual `_studio_training` pane (until Lock 5 removes it)
share one render path.

**3. SPEC.md auto-generated coverage table.** The §0.5 matrix in
this doc is hand-maintained today — adding a kind requires
editing this doc. Lock 11 commits to: BU.5 emits
`docs/audits/bu_coverage_matrix.md` from `PLANT_REGISTRY` +
section catalogues at every build. The §0.5 matrix moves to that
generated file; `bu_0_replan.md` keeps the prose context + cites
the generated file. Mirrors how the locked-seed SQL works
(checked-in artifact, freshness-gated by test).

**4. Demo-mode "what's planted" disclosure on `/etl/triage` +
`/etl/run`.** BTb.3's banner enumerates demo-plant kinds; today
that list is hand-maintained in `_studio_routes.py`. Lock 11:
the banner iterates `PLANT_REGISTRY`-filtered-to-L2 categories
+ checks the `<prefix>_training_state` KV row for each. Adding a
new L2 plant kind automatically appears in the disclosure banner.

**5. `recon-gen docs export` CLI** — registry walk that emits
markdown for any of the above surfaces. `--format` flag picks
the consumer (handbook / trainer-card / spec-matrix / disclosure-
banner). Default: full handbook + matrix bundle, written to
`docs/violations/` + `docs/audits/bu_coverage_matrix.md`.
Freshness-gated by Test 5 in Lock 9: re-running the export
produces byte-identical output, or the test fails.

**Render contract on `PlantKindEntry`:**

```python
def docs_generate_section(entry: PlantKindEntry) -> str:
    """Canonical markdown for one entry. Joins:

    - Typed section (resolved per Lock 8) — prose, columns,
      remediation.
    - Registry metadata — family, category, surface URLs,
      primitive form fields, dashboard_check matview.
    - Plant function docstring — picker reasoning.

    Output shape (subject to BU.5 design): one h2 header per
    entry, with sub-sections for SHOULD, Columns, Action,
    Primitives, Tour, Matview-check. Consistent across all 21
    kinds.
    """
```

**Cost of adding a new kind, with Lock 11:** one registry row +
(maybe) one typed-section markdown row. The new kind appears in:
- `/training/` landing card (Lock 1 + Lock 7).
- `/training/plant/<kind>` page (Lock 2 + Lock 7 + Lock 8).
- `/training/tour/<kind>` page (Lock 3 + Lock 7 + Lock 8).
- Per-violation handbook page (Lock 11.1).
- L1-dashboard sheet-bottom panel (Lock 11.2, when applicable).
- §0.5 coverage matrix (Lock 11.3).
- `/etl/triage` disclosure banner (Lock 11.4, when L2-side).
- Anti-drift bijectivity + tour-URL + plant-roundtrip tests
  (Lock 9).

Zero hand-maintained per-kind code anywhere.

**Rejected variants:**

- **Generate handbook pages from registry; keep matrix
  hand-maintained.** Same partial-source-of-truth problem Lock 8
  rejects for display strings. The matrix IS a documentation
  surface; if it doesn't generate, it'll drift.
- **Defer docs generation to a post-BU phase.** Reasonable on
  scope grounds, but the typed-source infrastructure is the
  payoff Lock 8 specifically enables; deferring it means the
  registry's "single source of truth" claim has no consumer
  beyond the trainer surface. Operator named docs generation as
  the destination payoff; honor that by locking it now and
  building it as BU.5.

**Cross-references:**

- Lock 7 (`docs_generate_section` is a method on
  `PlantKindEntry`).
- Lock 8 (the typed sections this generator consumes).
- Lock 9 (Test 5 — docs-freshness gate).
- `recon-gen docs export` (existing CLI surface,
  `cli/docs/__init__.py` — round-4 extends with new `--format`
  flag).
- `_studio_training.py` (existing consumer; migrates to walking
  PLANT_REGISTRY in BU.4 / BU.5).

## Sequencing implications

**Updated 2026-05-30 round-4 scope:** sequencing redrawn around
the typed-source dependency graph. BU.1 becomes a vertical slice
(`phantom_rail` end-to-end through registry + tour +
dashboard_check + bijectivity test scaffolded) to validate the
shape before BU.2a/b lands the typed catalogues + registry
skeleton. BU.2 splits: BU.2a builds the L2TriageGapSection +
markdown source (Lock 10); BU.2b lands the registry module +
shared render shell. BU.5 grows to include documentation
generation (Lock 11). BU.6 is cold-read v5. Net: 8 cells in BU.

**Updated 2026-05-30 round-3 scope:** BU.3 needs-build cells
itemized per Deliverable 5; BU.1 explicitly covers L2FT Hygiene
check inventory.

- **BU.1 (vertical slice — `phantom_rail` end-to-end)** — proves
  the round-4 architectural shape before BU.2a/b builds the full
  surface. ONE registry entry (`phantom_rail` — chosen because
  it's the only dual-surface L2 plant + the L2TriageGapSection
  parsing is the new pattern + the tour iframe destination
  exists), wired through:
  - A scaffolded `L2TriageGapSection` parser handling JUST the
    `unmatched_rail` section (full 5-section build is BU.2a).
  - A registry-skeleton module with the one entry + the shared
    `resolve_section` + `_render_plant_page` shell.
  - Lock 9 Test 1 (bijectivity) + Test 2 (tour-URL liveness) +
    Test 3 (plant→matview) scaffolded to parameterize over the
    1-entry registry; they expand to 21 entries when BU.2b lands
    the full registry.
  Validates the shape end-to-end on the smallest non-trivial
  example. Operator demo-able: `/training/plant/phantom_rail`
  renders, plants, tours, and the tests gate.
- **BU.2a (typed source for L2 Triage + L2 Coverage — Lock 10)** —
  lands `common/handbook/l2_triage_gaps.py` + the
  `src/recon_gen/docs/L2_Triage_Gaps.md` bundled markdown source
  (5 sections — 3 Triage + 2 Coverage). Migrates
  `_GAP_KIND_LABELS` + `_GAP_KIND_EDITOR_LABELS` to read through
  `load_bundled_l2_triage_gaps()`. Drops the in-module Mappings.
  Prereq for BU.2b's full registry — without the typed sections
  the L2 Triage / Coverage registry rows can't resolve display
  strings.
- **BU.2b (registry + shared render shell — Lock 2 + Lock 7 +
  Lock 8)** — lands the full registry module + the shared
  `_render_plant_page` + per-primitive render helpers + the
  `resolve_section` switch + the L1+L2 adapters that wrap
  `filter_scenario_plants` / `demo_etl_gaps.py` functions through
  the registry's `plant_function` signature. All 16 existing-
  primitive kinds get working `/training/plant/<kind>` pages out
  of one shell; the 5 needs-build kinds render the "BU.3
  placeholder" page (same shell, different `plant_function` that
  returns the "not-yet-implemented" panel). Lock 9's parameterized
  tests expand from 1 entry (BU.1) to 21 entries.
- **BU.3 (needs-build plant primitives — 5 cells; see Deliverable
  5 in BU.0.5 brief)** — implements the plant primitives the §0.5
  matrix flags as needs-build:
  - **BU.3.1** `expected_eod_balance_breach` — add
    `ExpectedEodBalanceBreachPlant` dataclass to `ScenarioPlant`;
    wire into `emit_seed`'s plant_adapter; update
    `filter_scenario_plants` selection set. Surfaces on Today's
    Exceptions.
  - **BU.3.2** `chain_orphan` — add `add_chain_orphan_gap_rows` to
    `demo_etl_gaps.py`: emit parent rail firing with NO child
    citation. Primitives: parent rail dropdown + child
    rail/template dropdown (filtered to declared chain edges) +
    days_ago. Surfaces on L2FT L2 Hygiene `chain_orphans`.
  - **BU.3.3** `dead_bundles_activity` — add
    `add_dead_bundles_activity_gap_rows` to `demo_etl_gaps.py`:
    DELETE postings against a declared `Rail.bundles_activity`
    target rail. Primitives: aggregating-rail dropdown +
    bundle-target dropdown (cascaded). Surfaces on L2FT
    `dead_bundles_activity`.
  - **BU.3.4** `dead_metadata` — add `add_dead_metadata_gap_rows`
    to `demo_etl_gaps.py`: strip non-null values for one declared
    `Rail.metadata_keys` entry by setting the JSON path to NULL
    via UPDATE (or DELETE the rows that carry it, depending on
    matview semantics — implementation detail for BU.3.4).
    Primitives: rail dropdown + metadata_key dropdown (cascaded).
    Surfaces on L2FT `dead_metadata`. **NOT the same as
    `missing_metadata_key`** — direction matters.
  - **BU.3.5** `dead_limit_schedule` — add
    `add_dead_limit_schedule_gap_rows` to `demo_etl_gaps.py`:
    DELETE outbound Debit postings matching a declared
    LimitSchedule (parent_role, rail) cell so the matview's NOT
    EXISTS finds zero rows. Primitives: parent_role dropdown +
    rail dropdown (cascaded to declared LimitSchedule cells).
    Surfaces on L2FT `dead_limit_schedules`.
  Each cell: one plant function + one PlantKindEntry row update
  (the entry's `plant_function` flips from placeholder to real)
  + unit test asserting the plant surfaces a non-empty matview
  row.
- **BU.4 (landing + tour + sub-nav, Lock 1 + Lock 3 + Lock 5)** —
  lands the `/training/` landing page (Lock 1's 21-card accordion
  grid via Lock 7's registry-driven renderer, 8 families, L1/L2
  visual grouping), the `/training/tour/<kind>` page with the
  embedded-iframe before/after toggle (Lock 3) via the registry's
  `tour_destination` field; destinations resolve per-category
  (L1 → dashboard sheet; L2 Triage → `/etl/triage`; L2 Coverage
  → `/etl/run` Coverage; L2FT Hygiene → L2 Flow Tracing L2
  Hygiene Exceptions sheet); removes the `/data` right-column
  pane (Lock 5) + folds `_studio_training` into the new
  `_training/` package walking PLANT_REGISTRY; wires the top-nav
  `Training` entry to a real handler; lands the reset-to-baseline
  plumbing (Lock 4 + Lock 6 — button on every `/training/*`
  page header).
- **BU.5 (documentation generation — Lock 11)** — extends
  `recon-gen docs export` with `--format` flag; emits per-kind
  handbook pages to `docs/violations/`; regenerates §0.5 matrix
  as `docs/audits/bu_coverage_matrix.md` from PLANT_REGISTRY +
  section catalogues; migrates the BTb.3 disclosure banner on
  `/etl/triage` + `/etl/run` to iterate the registry; lands
  Lock 9 Test 5 (docs-freshness byte-identity gate).
- **BU.6 (cold-read v5)** — similar pattern to BTb.6: verify the
  unified surface works end-to-end. Drive a SendMessage loop per
  `[[feedback_cold_read_iterative_screenshots]]`; verify the 21
  kinds across both groups land cleanly; sweep cold-read
  findings into BV / BW as appropriate.

BU.2a → BU.2b is serial (typed source must exist before registry
references it). BU.3.x can parallelize internally across the 5
cells (each is a self-contained plant function + one registry
row update). BU.4 + BU.5 can run in parallel after BU.2b ships,
since both reuse the registry the BU.2b shell already iterates.
BU.1 (vertical slice) is the earliest validating commit before
any of the wider builds; it shrinks-to-nothing after BU.2b lands
(the 1-entry registry it stood up is absorbed into the 21-entry
registry).

## Out of scope for BU (deferred)

**Updated 2026-05-30 round-4 scope:** the typed-section
catalogues + registry-driven docs generation are IN scope (BU.2a
+ BU.5 per Locks 10 + 11). The cold-read v5 (BU.6) is the
release gate; anything cold-read surfaces becomes BV/BW. No
round-4 deferrals beyond round-3's.

**Updated 2026-05-30 round-3 scope:** L2FT Hygiene plants moved
IN scope (4 needs-build cells in BU.3); `expected_eod_balance_breach`
moved IN scope (BU.3.1). The "future L2 invariants" deferral
language from round-2 is obsolete — §0.5 matrix is now the
canonical universe and everything on it is IN scope.

- `missing_limit_schedule` remains a non-plantable L2-shape gap
  per `demo_etl_gaps.py` module docstring (operator addresses
  via the L2 editor, not the Trainer). NOT a Trainer kind.
- Multi-kind composite plants ("plant drift AND overdraft on the
  same customer to show interaction," or "plant phantom_rail + the
  L1 invariant kind that the phantom rail's rows trigger"). Single-
  kind is the BU primary; composites are a follow-on if the
  operator wants them.
- Persistent plant state across studio process restarts. Plants
  + reset state live in the demo DB itself (durable) but the
  "● planted" tracking KV is per-deploy; restart = lose the
  status pill state. Acceptable for BU; tracking-table durability
  is a follow-on.
- Mobile / responsive layout for `/training/`. Same call as BS/BT
  — out of scope.
- Tour notes prose for every kind. BU.5's `tour_notes` field on
  `InvariantSection` is a new field on the markdown source
  (`L1_Invariants.md`). BU.5 lands the field + populates 2-3
  kinds; the long tail of authored notes is BU.7 or BV.

## Next: BU.0.5

**Updated 2026-05-30 round-4 scope:** mockup updates target three
spots: (i) §2 canonical mockup shows display strings as
`{section.title}` / `{section.short_statement}` / `{section.what_to_do}`
template variables resolved from the typed section per Lock 8,
NOT hardcoded strings; (ii) §7 open questions sweep — Q1 + Q11 +
Q15 (already collapsed round-3), Q16 collapses (typed sections +
`TourDestination.secondary_links` answer it), Q18-Q20 partially
collapse (Q18 anchor IDs become part of the L2FTExceptionSection
contract; Q19 dead_metadata implementation moves into BU.3.4's
cell description; Q20 stays since it's about the safety gate, not
the registry); (iii) §8 each BU.3.x cell description grows to
include the typed-section markdown source (most already exist; L2
Triage entries need their `L2_Triage_Gaps.md` sections written as
part of BU.2a, not BU.3.x).

**Updated 2026-05-30 round-3 scope:** mockup brief collapses
around the Lock 7 registry — cells (b) + (c) shrink to ONE
canonical mockup each (plus a primitive-renderer table) instead
of per-kind variants. Cell (a) grows to 21 cards across 8
families. Cell (e) is now driven by the registry, so its mockup
is one shape applied to all kinds. Cell (g) is new — the
canonical render of a registry entry.

Hand BU.0 (with §0.5 matrix + Lock 7) + SPEC's BU section + the
existing `_studio_training.py` pane source + `common/l2/demo_etl_gaps.py`
+ `common/l2/seed.py` (Plant dataclasses) + `src/recon_gen/docs/L2FT_Exceptions.md`
+ the BTa.0.5 mockup doc (as format reference) to the design-
mockup agent. Per `[[feedback_agent_driven_design_works]]`,
brief once comprehensively. Mockup deliverables per cell:

- (a) `/training/` landing — 21-card accordion grid + 8 family
  sub-headers (5 L1 + 3 L2 families, visually distinguished L1
  group vs L2 group) + per-card plant/tour CTAs + reset-to-
  baseline button. Driven by Lock 7 registry iteration; the
  mockup shows ALL 8 families' headers (collapsed) + one
  family fully expanded per group (1 L1 + 1 L2 expansion as
  reference shapes) (Lock 0 + 1 + 4 + 7)
- (b) `/training/plant/<kind>` per-kind plant page — ONE
  canonical mockup of the `_render_plant_page(entry)` shape +
  a sub-table showing how each `PrimitiveField` subtype renders
  (string / int / decimal / dropdown). Show 2-3 entry instances
  applied to the same template (one L1 — drift; one L2 Triage —
  phantom_rail; one L2FT Hygiene — chain_orphan) to demonstrate
  the template covers all categories. NO per-kind variants —
  if a kind isn't shown, it follows the same template (Lock 2
  + Lock 7)
- (c) `/training/tour/<kind>` per-kind tour page — ONE canonical
  mockup of the `_render_tour_page(entry)` shape + a sub-table
  showing the per-category iframe destination convention:
  - L1 → `/dashboards/l1_dashboard/sheets/<sheet>`
  - L2 Triage → `/etl/triage`
  - L2 Coverage → `/etl/run?failures-only=1#coverage-*`
  - L2FT Hygiene → `/dashboards/l2_flow_tracing/sheets/l2ft-sheet-l2-exceptions`
  Same Before/After toggle, same callout structure across all
  4 categories; the iframe URL is the only delta. NO per-kind
  variants beyond ~3 examples (Lock 3 + Lock 7)
- (d) Reset-to-baseline flow — button + flash + browser-tab
  pulse (Lock 4 + Lock 6 — confirm L2 plants AND the new L2FT
  Hygiene plants are wiped cleanly by the same truncate-and-
  reseed code path)
- (e) Cross-page navigation strip — sub-nav mirroring `/etl/`'s
  pattern (e.g. `Landing | Plant <current> | Tour <current> |
  ← Loop overview`), where `<current>` shows the kind the user
  picked. ONE mockup applied uniformly — registry iteration
  populates the kind list
- (f) The `/data` right-column pane removal (Lock 5) — before/
  after of `_studio_routes._render_data_page`'s `<main>` grid
  (unchanged from round-1)
- (g) `PlantKindEntry` reference card — short mockup showing
  what one registry entry looks like in code + what the rendered
  card / form / tour iframe URL evaluate to. Tied to (b) + (c)
  as the canonical "template instantiated" example. Educational
  for the next person adding a kind

Output: `docs/audits/bu_design_mockups.md`.

## Glossary refs

- **BTa.0** (`docs/audits/bta_0_replan.md`, 2026-05-30): the format
  reference for this doc + the source of Lock 1 (side-panel
  drawer), Lock 2 (numbered landing), Lock 3 (group-by accordions),
  Lock 4 (`?from=` breadcrumb) — BU re-uses 1, 3, 4; supersedes 2
  with the grid-per-kind pattern explained in Lock 1.
- **BS.0 D2** (`SPEC.md` line 96-120): "URL split + flat top-level
  nav" — the architectural basis for `/training/` being a
  first-class peer of `/etl/` + `/` rather than a `/studio/`
  sub-page.
- **BS.0 D5** (`SPEC.md` line 213-230): "verify then expose" — the
  plant-inventory-first sequencing BU.1 implements.
- **`[[project_qs_url_parameter_no_control_sync]]`**: QS embedded
  URL parameters bypass the control widget sync — locks the tour
  iframe to App2 (Lock 3).
- **`common/handbook/invariants.py::InvariantSection`** — existing
  L1 typed section catalogue + parser; reference pattern for
  Lock 10's L2TriageGapSection.
- **`common/handbook/l2ft_exceptions.py::L2FTExceptionSection`** —
  existing L2FT typed section catalogue + parser; Locks 8 + 11
  consume it for L2FT Hygiene display + docs generation.
- **`[[feedback_invariants_in_types]]` /
  `[[feedback_invariants_in_process]]`** — Rust-background
  preference for "encode invariants in the type system, not
  validation tests." Round-4's Lock 8 (typed section reference
  per kind) + Lock 9 (parameterized anti-drift) are the
  invariants-in-types posture applied to the registry surface.
- **`[[feedback_cheapest_validation_must_fire]]`** — Lock 9's
  five anti-drift tests are sized so the cheapest gate (Test 1
  bijectivity) catches the most common drift (registry typo); the
  pricier gates (Test 3 plant-roundtrip, Test 5 docs-freshness)
  catch the rarer-but-costlier drift modes.
