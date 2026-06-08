# CN.0 — Handbook redesign + `?` help surface + repo hygiene

Replan for Phase CN. Captures the 7 open-question locks, the per-sheet
handbook page-tree mapping (30 sheets), the `QSParityBreak` registry
shape + initial population, the screenshot-driven validation contract
for CN.4/CN.7, and the refined sub-cell sequencing.

**Status:** decisions locked 2026-06-08. Authored solo by Claude.
Sub-cells CN.1–CN.8 unblocked after CN.0 sign-off.

---

## Why this phase

Three pain points converged into one branch (per PLAN.md::Phase CN
preamble):

1. `docs/audits/` has accumulated **116 files**, all <60 days old.
   Working set is unsustainable; content is too referentially-useful
   to delete.
2. Project root has **11 `.md` files** — load-bearing (PLAN, SPEC,
   CLAUDE, README) coexisting with sprint-archaeology (`docs/audits/_archive/CB_11_C_NOTES.md`,
   `docs/audits/_archive/Q3_CLI_REDESIGN.md`) and ambiguously-load-bearing (`docs/specs/SPEC_studio.md`,
   `docs/specs/SPEC_gap_feedback.md`).
3. The handbook (`docs/` outside `audits/` + `reference/`) is wildly
   stale — `x_2_design_thoughts.md` / `x_4_5_design_thoughts.md` /
   `bs_design_thoughts.md` are pre-architecture artifacts; no current
   operator-help content. Meanwhile the L1/L2/L3/persona vocabulary
   has stabilized + every dashboard sheet now teaches a specific
   error class (per `[[feedback_demo_teaches_error_classes]]`) — the
   handbook should reflect that.

The unification: treat the **handbook as a load-bearing operator
surface**, not static docs. Couple it to a typed `?` help button on
every dashboard sheet (per-sheet mapping). The repo-hygiene sweep
clears the working surface so the handbook rewrite has a clean
slate.

---

## Locks

### Lock CN-1: Audit retention = archive don't delete

Shipped + superseded audits move to **`docs/audits/_archive/`** (flat
directory — no year-bucketing, since names already carry phase
prefixes like `cg_0_*` / `bu_0_*`). Living invariants stay at
`docs/audits/` top level: e.g. `date_range_model_audit.md`,
`bv_0_replan.md`-style active phase replans, current-cycle sign-offs.

**Default:** flat `_archive/`. Operator confirms or overrides at CN.2
kickoff.

### Lock CN-2: Handbook = rewrite, coupled to `?` surface

The handbook is restructured around the dashboard tree's L1/L2/L3
vocabulary. **Per-sheet mapping**: each of the 30 dashboard sheets
maps to exactly one handbook page that explains what the sheet
teaches (per the error-class teaching contract). Page-tree mapping is
the §"Page-tree mapping" section below.

Old design-thoughts files (`bs_design_thoughts.md`,
`x_2_design_thoughts.md`, `x_4_5_design_thoughts.md`) get archived to
`docs/audits/_archive/` per Lock CN-1.

### Lock CN-3: `?` help surface = App2-only — DECLARED QS PARITY BREAK

Every App2 dashboard sheet gets a `?` button (top-right of the sheet
chrome) that opens the linked handbook page **in a side panel** (not
modal — preserves the operator's data context). The button is wired
via a typed `Sheet.handbook_path: HandbookPath | None` field.

QS embeds host the existing per-sheet description text + a link out
to the handbook URL. This widens the App2-vs-QS parity gap on
purpose; the gap is captured in the QSParityBreak registry (Lock
CN-5) and surfaced in `docs/reference/quicksight-quirks.md`.

**Operator decision 2026-06-08:** mapping is **per-sheet only** (not
per-dashboard with sheet override). 30 typed mappings — manageable,
matches the error-class teaching contract precisely.

### Lock CN-4: Help-content authoring = runtime fetch from `docs/handbook/`

Handbook markdown lives at **`docs/handbook/<app>/<sheet>.md`** (one
file per Sheet's `handbook_path`). App2's `?` button fetches it at
runtime via a new Starlette route `GET /handbook/<app>/<sheet>` that
reads the .md file, renders it through the existing markdown
pipeline, and returns HTML for the side panel to inject.

**Why runtime fetch (operator decision 2026-06-08):**
- Edit `.md` → reload → see change. Same tight iteration loop the
  Studio + dashboard surfaces already give.
- One source of truth for App2's `?` AND the mkdocs site build (CN.6
  gate ensures the mkdocs site picks up the same files).
- Bundle stays lean; no rebuild step.

The mkdocs site build (`recon-gen docs apply`) keeps the same
content as a static-rendered alternative for operators who don't run
App2.

### Lock CN-5: Typed `QSParityBreak` registry — INVARIANT-IN-TYPES (pure-registry shape, no site-comment lint)

Every place the App2 renderer deliberately exceeds what QS can host
gets a typed, registered declaration. Per
`[[feedback_invariants_in_types]]`: a typed dataclass at construction
time beats scattered prose.

The registry module is `src/recon_gen/common/parity/breaks.py` —
sibling of `common/sql/dialect.py`'s `Dialect` enum, same
"load-bearing typed enumeration" shape.

**Design call — pure registry, not annotation-at-site:** the 14 breaks
are heterogeneous (some sit on a single dataclass field, some on
workaround code paths, some on diffuse feature areas like all of
Studio, some on absent-features or conventions). A decorator-style
annotation can't sit cleanly on all of those — pure annotation would
force "decorator for the ones that fit + registry-only entries for the
diffuse ones," and that hybrid is harder to teach than one pattern.

Pure registry wins for these reasons:
- **One place to author** — adding a new break is one edit in
  `breaks.py`, not a comment + registry-entry pair.
- **One place to read** — auto-generated quirks doc reads one
  structure; `grep "<name>"` still finds anywhere code references it.
- **Anti-drift via reference resolution** — each entry carries
  `references: tuple[str, ...]` pointing at file paths / function
  qualnames / dataclass.field anchors; the lint resolves each and
  fails loud on a rename. Same anti-drift guarantee a decorator gives,
  without needing to mount a decorator on conventions or absent code.
- **No site-comment lint** — earlier draft proposed
  `# QS-PARITY-BREAK: <name>` comments at workaround sites with a
  lint linking comment → entry. Dropped: that's a two-write pattern
  for one concept. Site comments are optional + discretionary now
  (e.g. `# See PARITY_BREAKS["count_distinct_quirk_bl1"]` when the
  workaround is non-obvious); the registry is the source.

```python
# common/parity/breaks.py
from dataclasses import dataclass
from enum import Enum
from typing import Final


class ParitySeverity(Enum):
    """How much the App2 surface exceeds QS for this break.

    - ENHANCEMENT: App2 has a richer affordance (e.g. `?` help side
      panel); QS shows the underlying data fine, just without the
      extra.
    - WORKAROUND: App2 routes around a QS bug/limit (e.g. count-
      distinct quirk); QS leg would render incorrect data without
      the workaround.
    - HARD_DIVERGENCE: QS literally cannot host this; App2-only
      feature (e.g. Studio L2 editor — no QS edit surface exists).
    """
    ENHANCEMENT = "enhancement"
    WORKAROUND = "workaround"
    HARD_DIVERGENCE = "hard_divergence"


@dataclass(frozen=True)
class QSParityBreak:
    """A registered deliberate divergence between the App2 + QS
    renderers.

    Surfaced in `docs/reference/quicksight-quirks.md` (auto-generated
    from this registry — single source of truth, no copy-paste
    drift). The registry IS the declaration; site comments are
    optional + discretionary.
    """
    name: str                # unique slug
    severity: ParitySeverity
    surface: str             # one-line: what App2 does
    qs_limitation: str       # one-line: what QS can't / won't do
    discovered: str          # ISO date (YYYY-MM-DD)
    references: tuple[str, ...] = ()  # file paths / qualnames the
                                       # entry pins down; lint resolves
                                       # each. Empty for convention-
                                       # only breaks (e.g. sheet-ID
                                       # shape) where there's no
                                       # single code site.


PARITY_BREAKS: Final[tuple[QSParityBreak, ...]] = (
    # ... initial population, see §"QSParityBreak initial population"
)
```

**Lint check** (sketch — added to `tests/unit/test_typing_smells.py`):
1. Every `PARITY_BREAKS` entry's `references` paths resolve. File
   paths exist; function/class qualnames import cleanly. Convention-
   only breaks (empty `references`) are allowed but flagged in the
   lint output for sanity-check visibility.
2. Every `PARITY_BREAKS` entry has a non-empty `surface` +
   `qs_limitation` + a valid ISO `discovered` date.
3. Names are unique across the registry (frozenset-cardinality
   check).

No `# QS-PARITY-BREAK:` comment scan — the registry is the only
declaration.

**Doc generation** (CN.6): a single Python emitter consumes
`PARITY_BREAKS` and writes the canonical section into
`docs/reference/quicksight-quirks.md`. Existing 26-section quirks log
gets folded in as initial population (one `QSParityBreak` per
existing section).

### Lock CN-6: docs/specs/SPEC_studio.md + docs/specs/SPEC_gap_feedback.md → `docs/specs/`

Move both to **`docs/specs/`** (new subdir; sibling to `docs/handbook/`
+ `docs/reference/`). Stays grep-able + load-bearing. Mirrors how
`docs/audits/` hosts the active-replan documents — out of root, still
discoverable. Updates all in-repo references (PLAN.md, CLAUDE.md
mentions) in the same commit.

`docs/audits/_archive/CB_11_C_NOTES.md` + `docs/audits/_archive/Q3_CLI_REDESIGN.md` go to `docs/audits/_archive/`
per Lock CN-1 — they're sprint-archaeology, not live SPEC.

**Result:** root `.md` count drops from 11 → 7: PLAN, PLAN_ARCHIVE,
SPEC, SPEC_ARCHIVE, CLAUDE, README, RELEASE_NOTES. Hits the ≤7
target exactly.

### Lock CN-7: mkdocs CI gate fires on handbook ↔ dashboard tree drift

Per `[[feedback_cheapest_validation_must_fire]]`: a typed primitive
without a liveness check is a vibes-lock. The CN.6 mkdocs gate
catches three drift shapes:

1. **mkdocs build clean** — no broken markdown refs.
2. **Sheet.handbook_path liveness** — every `Sheet` that declares a
   `handbook_path` resolves to an actual `docs/handbook/<path>.md`
   file. Asserted via test that walks the four `App` trees (per the
   "tree IS the source of truth" convention) and stats each path.
3. **QSParityBreak registry resolution** — every `PARITY_BREAKS`
   entry's `references` paths resolve (files exist, qualnames import
   cleanly); names are unique; `surface` / `qs_limitation` /
   `discovered` non-empty. Asserted in the typing-smells lint per
   Lock CN-5.

The gate also fails if a Sheet **doesn't** carry a `handbook_path`
once CN.4 is complete (only the App Info canary sheets are exempt —
they get a hardcoded `handbook_path=APP_INFO_PATH`).

---

## Page-tree mapping (30 sheets → 30 handbook pages)

One `.md` file per sheet, under `docs/handbook/<app>/<sheet-slug>.md`.
The slug is the kebab-case of the sheet name (drops the `<app>-sheet-`
prefix that's already encoded in the parent directory). App-Info
sheets all share a single handbook page (per-app variation is just
matview-row-count tables — no teaching distinction).

### L1 Dashboard (`docs/handbook/l1/`)

| Sheet ID | Handbook page | Teaching focus |
|---|---|---|
| `l1-sheet-getting-started` | `getting-started.md` | What L1 is, the drift→overdraft→limit invariant chain |
| `l1-sheet-drift` | `drift.md` | Sub-ledger drift: stored ≠ computed, scope = internal non-parents |
| `l1-sheet-drift-timelines` | `drift-timelines.md` | Drift over time: when did the disagreement enter? |
| `l1-sheet-overdraft` | `overdraft.md` | Carry-forward balance < 0; reads from effective_balances post-CL.5 |
| `l1-sheet-limit-breach` | `limit-breach.md` | Per-account limit schedule enforcement |
| `l1-sheet-pending-aging` | `pending-aging.md` | `status='Pending'` + age > rail.max_pending_age |
| `l1-sheet-unbundled-aging` | `unbundled-aging.md` | `status='Posted'` + `bundle_id IS NULL` + age > rail.max_unbundled_age |
| `l1-sheet-supersession-audit` | `supersession-audit.md` | `transaction.supersedes` chain integrity |
| `l1-sheet-exceptions` | `exceptions.md` | Today's all-L1 unified exception view |
| `l1-sheet-daily-statement` | `daily-statement.md` | Per-account day-by-day balance + activity narrative (post-CL.8 sources) |
| `l1-sheet-transactions` | `transactions.md` | Raw transaction drill surface |
| `l1-sheet-app-info` | `app-info.md` (shared) | Per-matview row counts + deploy stamp |

### L2 Flow Tracing (`docs/handbook/l2ft/`)

| Sheet ID | Handbook page | Teaching focus |
|---|---|---|
| `l2ft-sheet-getting-started` | `getting-started.md` | What L2FT is, the chain/template/rail vocabulary |
| `l2ft-sheet-rails` | `rails.md` | Per-rail anomaly volume + metadata coverage |
| `l2ft-sheet-chains` | `chains.md` | Chain integrity: parent_disagreement + completion shape |
| `l2ft-sheet-transfer-templates` | `transfer-templates.md` | Template firings, XOR groups, fan-in disagreement |
| `l2ft-sheet-l2-exceptions` | `l2-exceptions.md` | Unified L2 exception view (mirror of L1's Today's Exceptions) |
| `l2ft-sheet-app-info` | `app-info.md` (shared) | (same as L1 App Info) |

### Investigation (`docs/handbook/investigation/`)

| Sheet ID | Handbook page | Teaching focus |
|---|---|---|
| `inv-sheet-getting-started` | `getting-started.md` | What Investigation is, AML question-shapes |
| `inv-sheet-fanout` | `fanout.md` | Recipient fanout: one-to-many money distribution |
| `inv-sheet-anomalies` | `anomalies.md` | Sliding-window std-dev z-score (CL.4 cadence-aware) |
| `inv-sheet-money-trail` | `money-trail.md` | WITH RECURSIVE walk over `transfer_parent_id` |
| `inv-sheet-account-network` | `account-network.md` | Sankey: who-pays-whom directed flow graph |
| `inv-sheet-app-info` | `app-info.md` (shared) | (same as L1 App Info) |

### Executives (`docs/handbook/executives/`)

| Sheet ID | Handbook page | Teaching focus |
|---|---|---|
| `exec-sheet-getting-started` | `getting-started.md` | What Exec is, scope-vs-operations framing |
| `exec-sheet-program-health` | `program-health.md` | Top-line "is reconciliation working?" KPIs (CF.2) |
| `exec-sheet-account-coverage` | `account-coverage.md` | Per-role coverage % across the L2 |
| `exec-sheet-transaction-volume` | `transaction-volume.md` | Daily / weekly / monthly volume rollups |
| `exec-sheet-money-moved` | `money-moved.md` | Sign-aware net money moved (post-BK.9 fix) |
| `exec-sheet-app-info` | `app-info.md` (shared) | (same as L1 App Info) |

**Shared page:** `docs/handbook/_shared/app-info.md` (referenced by
all four App Info sheets via `handbook_path="_shared/app-info.md"`).

**Sequencing for CN.4:** rewrite Getting Started pages first (one per
app — set the vocabulary), then per-sheet error-class pages, then
App Info shared page last.

---

## QSParityBreak initial population

Sweep of existing parity divergences. Each gets a `QSParityBreak`
entry in `PARITY_BREAKS`. The full list — to be expanded by CN.1's
sweep — currently:

**WORKAROUND class (App2 routes around QS bug/limit):**

1. **`count_distinct_quirk_bl1`** — `CategoricalMeasureField(COUNT)`
   silently renders distinct count when the column is also a Dim.
   Workaround: emit `NumericalMeasureField(SUM)` over a literal-1
   CalcField. (`common/models.py:1409`, `quicksight-quirks` §BL.1)
2. **`dependent_dropdown_no_refresh_x223`** — `MappedDataSetParameters`
   doesn't refresh dependent control widgets on URL-driven initial
   load. (`common/tree/controls.py:192`, `common/models.py:1409`,
   `[[project_qs_url_parameter_no_control_sync]]`)
3. **`static_values_32_cap`** — `DataSetParameter.DefaultValues.
   StaticValues` is capped at 32 elements. Workaround: sentinel-
   based match-all clause for unbounded universes.
   (`common/models.py::*DatasetParameterDefaultValues.__post_init__`)
4. **`recursive_cte_in_custom_sql`** — QS Direct Query can't run a
   `WITH RECURSIVE` inside a custom-SQL dataset. Workaround:
   pre-compute as a matview. (`docs/walkthroughs/investigation/
   where-did-this-transfer-originate.md`)

**ENHANCEMENT class (App2 richer, QS still works):**

5. **`handbook_help_panel`** ⬅ NEW from CN.5 — `?` button + side
   panel rendering the per-sheet handbook page. QS surface
   shows the sheet description + a link out to the handbook URL.
   (`common/tree/structure.py::Sheet.handbook_path`)
6. **`studio_inline_help`** ⬅ NEW from CN.5a — same `?` pattern on
   L2 editor form fields. QS has no edit surface (HARD_DIVERGENCE
   adjacent — Studio itself is HARD; the field-level `?` is the
   ENHANCEMENT extension).
7. **`xlsx_export_button`** ⬅ from CH.5 (ship 2026-06-08) — `↓ XLSX`
   download per dashboard table. QS has its own export-to-CSV/PDF on
   a different path. (`common/html/server.py::_emit_xlsx_workbook`)
8. **`markdown_prose_richer_than_qs_text`** — App2 renders full
   markdown in sheet descriptions; QS `SheetTextBox` is constrained
   to its XML vocab. (`common/rich_text.py`,
   `[[project_qs_text_box_rich_formatting]]`)
9. **`tree_help_chip_on_dropdowns`** ⬅ from QS dropdown click-target
   project — App2 dropdowns work on the whole widget; QS dropdowns
   only open on the inner grey bar.
   (`[[project_qs_dropdown_click_target]]`)

**HARD_DIVERGENCE class (QS literally cannot host):**

10. **`studio_l2_editor`** — Studio's `/l2_shape/*` editor surface.
    No QS edit-form vocabulary exists.
11. **`studio_etl_support`** — Studio's `/etl/*` triage + run
    surface. QS has no notion of source-data probes.
12. **`studio_training`** — Studio's `/training/*` plant surface.
    Plants mutate the demo DB; QS is read-side only.
13. **`embed_url_can_only_open_quicksight_paths`** — QS embed URL
    family is constrained; App2 hosts arbitrary Starlette routes.
    (`common/browser/helpers.py:105`)
14. **`sheet_id_kebab_not_uuid`** — App2 uses analyst-readable
    kebab-case sheet IDs; QS internally maps to a different shape.
    (`common/tree/structure.py:1494`)

**Initial population count: 14 entries.** CN.1's sweep refines:
likely 5-10 more from the existing 26-section quirks log + a
thorough grep of `src/`. Target: ≤25 registered breaks at CN.1
exit (anything beyond that suggests we're double-counting symptoms
of the same root).

---

## Validation contract — screenshot-driven per `[[feedback_cold_read_iterative_screenshots]]`

The bug-class history of this codebase (BU cold-read v2, BT cold-read
v3, BV.4.0 vertical-slice gate) showed that source-only validation
misses where-is-the-button friction and visual misalignments
catastrophically. CN inherits the same pattern at TWO points:

### CN.4 mid-flight: per-page screenshot check

Per handbook page, after the agent drafts the prose:
1. Open the corresponding sheet in Studio dashboards (DuckDB +
   `spec_example` fixture).
2. Screenshot the sheet.
3. Compare against the handbook page draft: does the draft describe
   what's visible? Does it use the same KPI names, filter labels,
   visual titles? Does it call out the actual current-cycle empty-
   state copy?
4. Revise. Iterate until prose matches screen.

This is a built-in validation step inside CN.4, not an after-the-
fact cold-read. Catches drift between handbook prose and live
rendering at write-time, not read-time.

### CN.7 cold-read v0: iterative-screenshot agent loop

Per `[[feedback_cold_read_iterative_screenshots]]`: drive a
SendMessage loop where the agent requests button-pushes + screenshots
are taken + sent back. Source-only handbook walkthroughs (the agent
reads markdown + code) miss:
- "I clicked `?` and nothing happened" (side-panel JS regression)
- "The handbook says 'Drift KPI' but the sheet shows 'Drift Total'"
  (label drift)
- "I followed the handbook's drill-from-X to Y but landed on the
  wrong sheet" (drill regression)
- "The `?` panel doesn't fit on a 1440-wide screen" (layout)

Agent loop shape (operator-driven):
1. Agent: "Open the L1 Drift sheet at `/dashboards/l1_dashboard?sheet=l1-sheet-drift`. Screenshot."
2. Operator: opens, screenshots, sends back the .png.
3. Agent: "Click the `?` button top-right. Screenshot the side panel."
4. Operator: clicks, screenshots, sends back.
5. Agent: "The side panel shows handbook content — does it match
   what you'd want to see if you'd never used this sheet? Note any
   teaching gap, label mismatch, or rendering issue."
6. Iterate across all 30 sheets + every cross-app drill the handbook
   references.

Output: `docs/audits/cn_7_cold_read_v0.md` listing per-sheet findings
graded P1/P2/P3. CN.7 sign-off requires zero P1s.

---

## Refined sub-cell sequencing

CN.1–CN.8 refined with operator decisions + the screenshot-validation
hooks. **No ordering changes** from the original PLAN.md outline; the
refinements are scope tightening + parallelism callouts.

| Cell | Refined scope | Parallel-fan-out fit |
|---|---|---|
| **CN.1** | Build `common/parity/breaks.py` module + 14-entry initial population (pure registry, no site-comment lint per Lock CN-5). Add typing-smells lint that resolves each entry's `references`. Wire doc-generation emitter into quirks log. | Modest fan-out: one agent sweeps `src/` for additional breaks, one sweeps `docs/audits/`, one sweeps `quicksight-quirks.md` for existing 26 sections to fold in. Synthesize. |
| **CN.2** | Move ~91 audits to `docs/audits/_archive/`. Decision rubric: "Is this an active replan / shipped-but-still-referenced invariant / current-cycle sign-off?" → keep. Otherwise archive. | **Strong fan-out fit (ultracode).** Bucket by phase prefix (BS/BT/BU/BV/CA/CB/CF/CG/etc.) → one agent per bucket triages keep-vs-archive against rubric. Synthesize. ~12 buckets × ~10 files each. |
| **CN.3** | Move `docs/specs/SPEC_studio.md` + `docs/specs/SPEC_gap_feedback.md` to `docs/specs/` (with `git mv`). Move `docs/audits/_archive/CB_11_C_NOTES.md` + `docs/audits/_archive/Q3_CLI_REDESIGN.md` to `docs/audits/_archive/`. Update PLAN.md + CLAUDE.md references. | Solo — small mechanical sweep. |
| **CN.4** | Rewrite 30 handbook pages — Getting Started ×4 first, then per-sheet error-class pages, then shared `app-info.md`. Per-page screenshot validation (per §Validation contract). | **Strong fan-out fit (ultracode).** Per-app pipeline (L1/L2FT/Inv/Exec): outline → draft → screenshot-validate → revise. 4 parallel app threads, each ~6-13 pages internally pipelined. |
| **CN.5** | Add `Sheet.handbook_path: HandbookPath \| None` typed field. App2 renderer: `?` button + side panel + Starlette `GET /handbook/<app>/<sheet>` route. Register `handbook_help_panel` in CN.1's PARITY_BREAKS. | Solo — single tree primitive + single renderer + single route. |
| **CN.5a** | Extend `?` pattern to `FieldSpec.handbook_path`. Survey ~6 fields that benefit (`epc`, `leg_rail_xor_groups`, `transfer_key`/`completion`, `bundles_activity`, `max_unbundled_age`, `metadata_value_examples`). Register `studio_inline_help`. | Solo — small extension of CN.5. |
| **CN.6** | mkdocs build gate + Sheet.handbook_path liveness check (test that walks all 4 Apps) + QSParityBreak comment-vs-registry consistency (typing-smells). | Solo — three test-gate additions. |
| **CN.7** | Cold-read v0 via iterative-screenshot loop. Operator drives; agent observes. Output: `docs/audits/cn_7_cold_read_v0.md`. | Solo per cold-read pattern (single agent + operator screenshots). |
| **CN.8** | Sign-off + sweep CN to PLAN_ARCHIVE.md. | Solo. |

**Total phase estimate:** ~3-4 sessions of focused work. CN.2 + CN.4
are the high-effort cells (mechanical sweep + 30-page rewrite); both
benefit from ultracode fan-out per the `[[feedback_design_for_claude_loops]]`
loop-design principle.

---

## Open backlog (out of CN scope)

- **mkdocs theming refresh** — the current theme is the mkdocs
  default. Once the handbook surface is canonical, a brand pass is
  worth doing. Filed as BX backlog at CN.4 close.
- **handbook search inside App2's side panel** — basic full-text
  search across `docs/handbook/`. CN ships the side panel; search is
  a follow-on if the cold-read surfaces "I can't find the page I
  need" friction.
- **per-FieldSpec `?` rollout beyond the initial 6 fields** — Lock
  the pattern in CN.5a; expand to remaining fields as cold-read
  feedback surfaces them. Track as BX backlog after CN.5a.

---

## Sign-off checklist for CN.0

- [x] 7 open design questions locked (3 operator-answered 2026-06-08:
  per-sheet, runtime-fetch, `docs/specs/`; 4 default-and-flagged:
  flat `_archive/`, side panel, mkdocs liveness gate, archive
  `docs/audits/_archive/CB_11_C_NOTES.md` + `docs/audits/_archive/Q3_CLI_REDESIGN.md`).
- [x] Page-tree mapping enumerated (30 sheets → 30 handbook paths +
  1 shared App Info page).
- [x] QSParityBreak registry shape designed; 14-entry initial
  population sketched.
- [x] Screenshot-driven validation hooks baked into CN.4 + CN.7 per
  operator request 2026-06-08 (`[[feedback_cold_read_iterative_screenshots]]`).
- [x] Sub-cell sequencing refined with parallelism callouts
  (CN.2 + CN.4 flagged for ultracode).
