# O.0.a — Content + persona-string audit

Audit of every prose file across the three docs surfaces (mkdocs `docs/`,
`docs/walkthroughs/`, `training/handbook/`) plus the docs-root files
(`Schema_v6.md`, `Training_Story.md`). For each file: dominant voice
classification + persona-string inventory + suggested vocabulary key.

Drives the O.0 decisions:
- O.0.b — template engine choice (audit-time call: `mkdocs-macros-plugin`
  + Jinja2; in-tree, low-friction)
- O.0.c — `HandbookVocabulary` schema (sketch in §5)
- O.0.e — merged information architecture (sketch validated in §6)
- O.0.f — diagram catalog

## §1 — Per-file inventory

**62 prose files** across the three surfaces + 2 docs-root files.

| File | Lines | Voice | Persona strings | Notes |
|---|---|---|---|---|
| **docs/ root** | | | | |
| `docs/Training_Story.md` | 264 | **narrative + scenario** | ≈52 (SNB, Bigfoot Brews, Sasquatch Sips, Yeti Espresso, Cascade Timber Mill, Pinecrest Vineyards, Big Meadow Dairy, Harvest Moon Bakery, Federal Reserve Bank, Payment Gateway Processor, Pacific Northwest, Margaret Hollowcreek, Farmers Exchange Bank) | **Single biggest persona-leak source** in the doc surface. Reads as a "Cast of characters" page for the scenarios. Lift the structural shape into vocabulary; persona-flavored prose becomes templated. |
| `docs/Schema_v6.md` | 447 | **reference** | 8 (SNB, Sasquatch National Bank, Federal Reserve Bank in passing) | Mostly clean — persona used as illustrative example in a couple of paragraphs. Easy to template. |
| **docs/handbook/** | | | | |
| `docs/handbook/l1.md` | 185 | **reference** | SNB (1) | "Switch the L2 → switch the persona, prose, and scenario coverage" |
| `docs/handbook/l2_flow_tracing.md` | 158 | **reference** | SNB (1) | Contract-focused, structural |
| `docs/handbook/investigation.md` | 132 | **mixed (reference + narrative)** | Sasquatch National Bank (2), Juniper Ridge LLC (2), Cascadia (4) | Narrative intro + reference content |
| `docs/handbook/etl.md` | 173 | **mixed (reference + narrative)** | Sasquatch National Bank (1), SNB (1), Fed (5 contextual) | Voice + contract |
| `docs/handbook/customization.md` | 237 | **mixed (reference + walkthrough)** | Sasquatch National Bank (1) | Reference structure with how-to bits |
| **docs/walkthroughs/l1/** (11 files) | | | | |
| `getting-started.md` | 35 | **reference** | clean | |
| `drift.md` | 52 | **reference** | clean | |
| `drift-timelines.md` | 51 | **reference** | clean | |
| `overdraft.md` | 43 | **reference** | clean | |
| `limit-breach.md` | 47 | **reference** | clean | |
| `pending-aging.md` | 47 | **reference** | clean | |
| `unbundled-aging.md` | 49 | **reference** | clean | |
| `supersession-audit.md` | 57 | **reference** | clean | |
| `todays-exceptions.md` | 60 | **reference** | clean | |
| `daily-statement.md` | 59 | **reference** | clean | |
| `transactions.md` | 66 | **reference** | clean | |
| **docs/walkthroughs/investigation/** (4 files) | | | | |
| `who-is-getting-money-from-too-many-senders.md` | 143 | **walkthrough** | Juniper Ridge (1), Cascadia (1) | Demo scenario embedded |
| `which-pair-just-spiked.md` | 167 | **walkthrough** | Cascadia Trust Bank (1), Juniper (1) | Walkthrough mechanics + demo scenario |
| `where-did-this-transfer-originate.md` | 184 | **walkthrough** | SNB (1), Cascadia (2), Juniper Ridge (1) | Narrative scenario |
| `what-does-this-accounts-money-network-look-like.md` | 182 | **walkthrough** | Juniper Ridge (2), Cascadia (1) | Task-oriented + demo context |
| **docs/walkthroughs/etl/** (6 files) | | | | |
| `how-do-i-populate-transactions.md` | 191 | **walkthrough** | SNB (2), Fed (5 contextual) | Narrative walkthrough |
| `how-do-i-add-a-metadata-key.md` | 193 | **walkthrough** | SNB (1), Fed (2 contextual) | Step-by-step |
| `how-do-i-prove-my-etl-is-working.md` | 234 | **walkthrough** | clean | Engineering pre-flight pattern |
| `how-do-i-validate-a-single-account-day.md` | 243 | **walkthrough** | clean | Post-load verification |
| `how-do-i-tag-a-force-posted-transfer.md` | 168 | **walkthrough** | SNB (1), Fed (3 in "GL vs Fed Master Drift" check name) | Spec walkthrough |
| `what-do-i-do-when-demo-passes-but-prod-fails.md` | 284 | **walkthrough** | clean | Troubleshooting recipes |
| **docs/walkthroughs/customization/** (9 files) | | | | |
| `how-do-i-map-my-database.md` | 201 | **walkthrough** | clean | Narrative product-owner walkthrough |
| `how-do-i-configure-the-deploy.md` | 240 | **walkthrough** | clean | Reference walkthrough |
| `how-do-i-run-my-first-deploy.md` | 268 | **walkthrough** | clean | Step-by-step |
| `how-do-i-reskin-the-dashboards.md` | 223 | **walkthrough** | SNB (2), Sasquatch National Bank Theme (1) | Customization walkthrough |
| `how-do-i-swap-dataset-sql.md` | 247 | **walkthrough** | clean | Walkthrough + reference |
| `how-do-i-add-a-metadata-key.md` | 254 | **walkthrough** | clean | Reference walkthrough |
| `how-do-i-extend-canonical-values.md` | 252 | **walkthrough** | Bigfoot Brews (1) | Extension walkthrough |
| `how-do-i-author-a-new-app-on-the-tree.md` | 243 | **walkthrough** | clean | Architecture walkthrough |
| `how-do-i-test-my-customization.md` | 313 | **walkthrough** | clean | QA walkthrough |
| **training/handbook/** (18 files) | | | | |
| `README.md` | 77 | **narrative** | Sasquatch National Bank (1), Bigfoot Brews (1) | Orientation page |
| `concepts/double-entry.md` | 46 | **narrative** | SNB demo (1) | Conceptual explanation |
| `concepts/eventual-consistency.md` | 56 | **narrative** | SNB (1) | Conceptual mechanics |
| `concepts/sweep-net-settle.md` | 62 | **narrative** | SNB demo (1) | Conceptual walkthrough |
| `concepts/open-vs-closed-loop.md` | 56 | **narrative** | SNB (1) | Conceptual definition |
| `concepts/escrow-with-reversal.md` | 67 | **narrative** | SNB (1) | Conceptual pattern |
| `concepts/vouchering.md` | 69 | **narrative** | SNB (1) | Conceptual definition |
| `for-accounting/00-why-this-exists.md` | 93 | **narrative** | clean | Problem statement |
| `for-accounting/01-dashboard-literacy.md` | 125 | **narrative** | SNB (1) | User guide |
| `for-customer-service/00-why-this-exists.md` | 104 | **narrative** | clean | Role-oriented narrative |
| `for-developers/00-why-this-exists.md` | 147 | **narrative** | clean | Role-oriented narrative |
| `for-developers/extending.md` | 164 | **narrative** | SNB (1) | Extension narrative |
| `for-product-owner/00-how-to-present-this.md` | 180 | **narrative** | clean | Presentation narrative |
| `scenarios/01-dollars-in-the-pool.md` | 142 | **narrative + scenario** | Big Meadow Dairy (2), Bigfoot Brews (1), Cascade Timber (1) | Scenario narrative |
| `scenarios/02-what-happened-to-this-money.md` | 153 | **narrative + scenario** | clean | Scenario walkthrough |
| `scenarios/03-vouchers-dont-match-sales.md` | 155 | **narrative + scenario** | Big Meadow Dairy (1) | Scenario narrative |
| `scenarios/extending-template.md` | 143 | **narrative + scenario** | Bigfoot Brews (1), Big Meadow Dairy (1) | Extension scenario |
| `training/QUICKSTART.md` | 32 | **narrative** | clean | Quick-reference |

## §2 — Voice distribution

Total: 62 prose files (60 from the audit + 2 docs-root).

| Voice | Count | % | Where it dominates |
|---|---|---|---|
| **reference** | 17 | 27% | All 11 L1 walkthroughs + 2 handbook pages + Schema_v6.md + 3 mixed pages skewing reference |
| **narrative** | 21 | 34% | All 7 concepts + 4 role-tracks + 4 scenarios + Training_Story.md + 5 meta/intro |
| **walkthrough** | 18 | 29% | All Investigation walkthroughs (4) + ETL walkthroughs (6) + customization (8) |
| **mixed** | 6 | 10% | Handbook index pages (l2_flow_tracing.md, investigation.md, etl.md, customization.md) — these benefit from being split into a per-section reference page + a separate index hub |

Surface profiles:
- **`docs/handbook/`** — 5 files, mostly mixed reference. The index pages bridge into the per-app sections.
- **`docs/walkthroughs/`** — 30 files, 80% walkthrough or pure reference. Per-sheet L1 pages are pure reference; investigation/ETL/customization are task-oriented.
- **`training/handbook/`** — 18 files, ~95% narrative. Foundation conceptual reading + persona-specific quickstarts + scenario walkthroughs.
- **`docs/` root** — 2 files. Schema_v6.md is reference; Training_Story.md is the single largest persona-leak page.

## §3 — Persona-string totals (top counts)

| String | Count | Type | In `SNB_PERSONA`? |
|---|---|---|---|
| SNB | 71+ | institution acronym | ✓ |
| Cascadia | 23+ | investigation persona | ✗ (deferred — see §5) |
| Sasquatch National Bank | 14+ | institution name | ✓ |
| Big Meadow Dairy | 11+ | merchant | ✓ |
| Juniper Ridge | 7 | investigation persona | ✗ (deferred — see §5) |
| Cascade Timber | 6 | merchant | ✓ |
| Harvest Moon Bakery | 4 | merchant | ✓ |
| Bigfoot Brews | 4 | merchant | ✓ |
| Payment Gateway Processor | 3 | stakeholder | ✓ |
| Sasquatch Sips | 2 | merchant | ✗ (Training_Story-only) |
| Yeti Espresso | 2 | merchant | ✗ (Training_Story-only) |
| Pinecrest Vineyards | 2 | merchant | ✓ |
| Federal Reserve Bank | 1+ | stakeholder | ✓ |
| Margaret Hollowcreek | 1+ | flavor | ✓ |
| Pacific Northwest | 1+ | flavor | ✓ |
| Farmers Exchange Bank | 1+ | flavor | ✓ |

Investigation-persona strings (Cascadia, Juniper Ridge) and the
"Sasquatch Sips" / "Yeti Espresso" merchants used in
`Training_Story.md` are **NOT** in `SNB_PERSONA`. Decision in O.0.c:
extend `SNB_PERSONA` to cover them, OR define a separate persona for
the Investigation app.

## §4 — Code-side residual leaks

Allowed sites (intentional persona references):
- `src/quicksight_gen/common/persona.py` — canonical source
- `src/quicksight_gen/apps/l1_dashboard/_l2.py`, `_default_l2.yaml` — default L2 fixture
- `src/quicksight_gen/apps/*/demo_data.py` — per-app demo seed
- `tests/l2/` — all L2 fixtures + harness seeds

Residual hits outside allowed sites:

| File | Line | String | Judgment |
|---|---|---|---|
| `src/quicksight_gen/cli.py` | 845 | "Cascadia/Juniper persona-flavored Investigation walkthrough" | OK to keep — comment explaining the deferred persona-fixture lift |
| `src/quicksight_gen/cli.py` | 1067 | `r"Cascade Timber"`, `r"Pinecrest"`, `r"Harvest Moon"` | OK to keep — `_WHITELABEL_LEFTOVER_PATTERNS` regex list is intentionally exhaustive (catches unmapped strings during substitution) |
| `src/quicksight_gen/common/l2/seed.py` | 75 | "Bigfoot Brews — DDA" | OK to keep — `TemplateInstance.name` field docstring example |
| `src/quicksight_gen/common/l2/loader.py` | 365 | "Sasquatch National Bank Theme" | OK to keep — YAML schema docstring example |

**Verdict: zero real bleeds.** All residual hits are docstring examples
or intentional whitelabel-detection patterns.

## §5 — `HandbookVocabulary` schema sketch

Drives O.0.c. Built on top of the existing `SNB_PERSONA` dataclass; the
deltas vs. today are flagged.

```python
@dataclass(frozen=True, slots=True)
class HandbookVocabulary:
    """Substitution vocabulary for the Phase O unified mkdocs site.

    Built per-render from an L2Instance + an optional persona block on
    the institution YAML. The persona block is OPTIONAL; without it,
    the vocabulary falls back to a neutral "your institution" voice
    suitable for an integrator who doesn't want demo flavor.
    """
    institution: InstitutionVocabulary
    stakeholders: tuple[StakeholderVocabulary, ...]
    gl_accounts: tuple[GLAccountVocabulary, ...]      # already in SNB_PERSONA
    merchants: tuple[MerchantVocabulary, ...]         # already in SNB_PERSONA
    flavor: tuple[FlavorVocabulary, ...]              # already in SNB_PERSONA
    investigation_personas: tuple[InvestigationPersonaVocabulary, ...]  # NEW
```

Sub-shapes:

```python
@dataclass(frozen=True)
class InstitutionVocabulary:
    name: str            # "Sasquatch National Bank"
    acronym: str         # "SNB"
    description: str     # for prose intros (pulled from L2Instance.description)
    region: str          # "Pacific Northwest" (from current SNB_PERSONA.flavor)
    legacy_entity: str | None  # "Farmers Exchange Bank" (the absorbed institution)

@dataclass(frozen=True)
class StakeholderVocabulary:
    name: str            # "Federal Reserve Bank"
    short_name: str      # "Fed"
    role: str            # "settlement authority" / "card acquirer"

@dataclass(frozen=True)
class MerchantVocabulary:
    name: str            # "Big Meadow Dairy"
    account_id: str      # "cust-900-0001-big-meadow-dairy" (joins to seed)
    sector: str          # "agricultural" / "coffee retail"

@dataclass(frozen=True)
class FlavorVocabulary:
    name: str            # "Margaret Hollowcreek"
    role: str            # "character" / "region" / "legacy_entity"

@dataclass(frozen=True)
class InvestigationPersonaVocabulary:
    """Compliance/AML scenario actors. Investigation app uses these
    in the Cascadia/Juniper convergence demo; appear in 4 walkthrough
    pages today. Not in SNB_PERSONA today — promote in O.0.c."""
    name: str             # "Juniper Ridge LLC", "Cascadia Trust Bank"
    account_id: str       # "cust-900-0007-juniper-ridge-llc"
    role: str             # "convergence_anchor" / "operations_account" / "shell_entity"
```

GL accounts reuse the existing `GLAccount` dataclass from `persona.py`
unchanged; the existing `account_labels` tuple in `SNB_PERSONA` is
redundant (derivable from `gl_accounts`) and should be dropped during
the O.1.b implementation.

`vocabulary_for(l2_instance) -> HandbookVocabulary` reads:
- `institution.name` from `l2_instance.description` (first line) or a
  new `personas:` YAML block
- `gl_accounts`, `merchants`, `flavor`, `investigation_personas` from
  the new `personas:` YAML block (sidecar) OR fall back to a built-in
  neutral default if the instance carries no persona block

The neutral default is what an integrator gets out-of-the-box without
declaring personas — generic strings like "Your Bank" / "Acme Bank" /
"Customer 1" / "Customer 2" — so the handbook reads sensibly even
before the integrator personalizes.

## §6 — Merged IA validation

The proposed 5-section IA (O.0.e default) maps cleanly:

| Section | Source today | File count |
|---|---|---|
| `Concepts/` | `training/handbook/concepts/` | 7 (incl. README) |
| `Reference/` | `docs/handbook/` + `docs/Schema_v6.md` | 6 |
| `Walkthroughs/` | `docs/walkthroughs/` | 30 |
| `For Your Role/` | `training/handbook/for-*/` | 4 + role-orientation |
| `Scenarios/` | `training/handbook/scenarios/` + `docs/Training_Story.md` | 5 |

Top-level `index.md` derives from the existing `training/QUICKSTART.md`.

The **mixed-voice handbook index pages** (l2_flow_tracing.md,
investigation.md, etl.md, customization.md) split into:
- A per-section landing page under `Reference/<app>/index.md` (reference
  voice)
- A per-section "see also" block linking to the matching `Walkthroughs/<app>/`
  + `Concepts/` + `For Your Role/` pages (cross-section navigation)

## §7 — Diagram catalog inputs

Drives O.0.f. From the audit, the diagrams worth generating:

**L2-driven (auto from YAML, render via Graphviz):**
- Account-rail-account topology (per L2 instance) — embedded in
  `Reference/<app>/` and `Walkthroughs/<app>/getting-started.md`
- Chain DAG (per L2) — `Reference/l2_flow_tracing.md`
- Layered combination (accounts + rails + chains + transfer templates) —
  `Reference/index.md` as the "big picture"
- Per-app dataflow (which datasets feed which sheets) — one per app
  Reference page

**Hand-authored conceptual (`docs/_diagrams/conceptual/*.dot`):**
- Double-entry posting flow (debit + credit pair) — `Concepts/double-entry.md`
- Escrow-with-reversal cycle — `Concepts/escrow-with-reversal.md`
- Sweep-net-settle daily cycle — `Concepts/sweep-net-settle.md`
- Vouchering: voucher → settlement materialization — `Concepts/vouchering.md`
- Eventual consistency timeline (multi-day clear) — `Concepts/eventual-consistency.md`
- Open-vs-closed-loop network shapes — `Concepts/open-vs-closed-loop.md`

**Hybrid (skeleton hand-authored, L2 fills labels):**
- "Your role's view" — one per persona under `For Your Role/<role>/index.md`
  showing which sheets/datasets that role touches; the skeleton is
  hand-authored, the L2 fills account/sheet labels.

## §8 — Decisions snapshot

Captured here so O.1 can execute mechanically.

- **Template engine**: `mkdocs-macros-plugin` + Jinja2.
- **File-layout**: in-place (overwrite `docs/`) — the "split" alternative
  adds friction without buying clean diffs (mkdocs-macros runs at build
  time; source markdown stays template-shaped). Override if a problem
  surfaces during O.1.c pilot.
- **IA**: 5-section structure as proposed in O.0.e; validated in §6.
- **Vocabulary persona scope**: extend `SNB_PERSONA` (or a `HandbookVocabulary`
  built on top of it) to cover Investigation personas + the 2 missing
  Training_Story merchants; do NOT split into per-app personas.
- **Diagram engine**: Graphviz `dot` (default), `neato` for force-directed
  network views (the L2 topology cuts), embedded as SVG via the
  `{{ diagram(...) }}` macro.
- **`training/` directory removal**: confirmed safe per the inventory —
  18 files migrate cleanly into the new IA's `Concepts/` + `For Your Role/` +
  `Scenarios/` sections; no orphan content, no broken cross-references
  to anything outside the doc surface.
