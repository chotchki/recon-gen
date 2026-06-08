# BT.0 — Phase BT REPLAN (post-BS exit state)

> **Status:** REPLAN LOCKED 2026-05-30. Read SPEC.md::Phase BT against
> the BS exit state; verified D4.arch lock matches BT.2-BT.5
> assumptions; inventoried BS.1 audit's BT carryover; output: lock
> list + BT.1-N task refinement.

## Headline

Phase BT can fire as-spec'd. **No SPEC changes needed**; BS exit state
satisfies every prerequisite BT.2-BT.5 assume. The one decision to
take down-stream of BS.1's audit is which projection-view conversions
to bundle with BT vs leave for later. Recommendation: **bundle
`_v_config_transfer_templates` with BT.2** (the L2-slice probe needs
it anyway) + defer the others until a BT surface actually requires
them.

## BS exit-state verification

### BS.4 deploy-model shift (consumed by BT.3)

BT.3 spec: "operator clicks 'run'; the binary truncates demo_db,
invokes the ETL hook, refreshes matviews."

BS.4 shipped: `run_deploy_pipeline` now `wipe → etl_hook → generator
→ matviews → reload`. The 4-step shape BT.3 needs is in place. The
extra step_3 (generator) overlay is benign — BT.3 calls a different
entry point that skips the generator (or sets `test_generator.enabled
= False` for the duration of an ETL-only run).

**Sub-decision (locked):** BT.3 invokes `run_deploy_pipeline` with a
cfg patched to disable the test_generator overlay. Operators who
want generator overlays use Training mode (Phase BU); ETL Support's
flow is pure ETL.

### BS.2/BS.3 nav scaffolding (consumed by BT.1)

BT.1 spec: "`/studio/etl` flat-nav entry + landing page."

BS exit: `build_top_nav_entries` already includes the `ETL Support`
entry pointing at `/etl/`. Route doesn't exist yet (intentional dead
link per the operator's "leave as-is" decision earlier in the BS
walkthrough). BT.1 lands the route + landing page; the nav entry
goes live automatically.

**Sub-decision (locked):** the BT.1 landing page renders the same
shared top-nav + Studio header; sits behind a `studio_routes_factory`
that adds the `/etl/`, `/etl/probe`, `/etl/run`, `/etl/triage` routes
alongside the existing Studio routes. No new factory plumbing —
extends the existing `make_studio_routes`.

### BS.1 audit — BT carryover

The static-collapse audit (`docs/audits/bs_6_kv_static_collapse_audit.md`)
deferred 5 projection-view items to BT. Re-classified against BT.2-BT.5
needs:

| View | BS.1 reason | BT need? | BT phase to land |
| ---- | ----------- | -------- | ---------------- |
| `_v_config_transfer_templates` | unlocks 2 L2FT builders (tt_instances + tt_legs) | **YES** — BT.2's L2-slice probe slices BY template + needs per-template column expectations | BT.2 (bundle the view authoring with the probe page) |
| `_v_config_bundles_activity` | unlocks 1 L2FT builder | NO direct BT need | Defer to BV or follow-on |
| `_v_config_rail_metadata_keys` | partial value (Oracle ORA-40597 caps it) | NO direct BT need (BT.5 derives metadata expectations from `instance.rails[].metadata_keys` directly — same Python source) | Defer indefinitely |
| `_v_config_accounts` | not load-bearing for any walked path | NO direct BT need | Defer indefinitely |
| `_v_config_account_templates` | not load-bearing for any walked path | NO direct BT need | Defer indefinitely |
| `_v_config_rails.leg_shape` extension | 1 L2FT builder | NO direct BT need | Defer to follow-on |

**Sub-decision (locked):** BT.2 authors `_v_config_transfer_templates`
as part of the L2-slice probe implementation. The remaining BS.1
deferrals stay deferred until a real BT/BV need surfaces — no
speculative views (per `[[feedback_no_compat_shims]]` posture).

## BT.5 — per-column-pair contract derivation

SPEC: "the match-vs-not-matched view (#3) implies a per-column-pair
contract derivable from the L2. Audit `common/l2/` to see whether the
typed primitives already carry it, or whether a new derivation step
is needed."

**Audit result:** the typed primitives carry enough to derive the
contract — no new model needed. Derivation source:

- **Rail** (`TwoLegRail` / `SingleLegRail`) → per-rail expectations on
  `<prefix>_transactions`:
  - `rail_name = <Rail.name>`
  - `account_role IN (<Rail.source_role>, <Rail.destination_role>)` for two-leg
  - `account_role = <Rail.leg_role> AND amount_direction = <Rail.leg_direction>` for single-leg
  - `<metadata JSON>` carries each `key IN Rail.metadata_keys` (when declared as required)
- **TransferTemplate** → per-template expectations:
  - `template_name = <TransferTemplate.name>`
  - Inherits the underlying rail's column expectations per `leg_rails`
- **Chain** → per-chain-parent expectations:
  - `template_name = <Chain.parent>` OR `rail_name = <Chain.parent>` (parent can be either)
  - For each child: `transfer_parent_id` chain to a matching child Transfer
- **LimitSchedule** → per-(parent_role, rail, direction) expectations:
  - Bounded narrowing over `daily_balances`-side aggregates

**Sub-decision (locked):** BT.5 lands a new module
`common/l2/contract.py` with a single function
`derive_column_contracts(L2Instance) → ColumnContracts`. Pure
function; reads only the typed primitives + the existing helpers in
`common/l2/`. BT.4's exception-triage view consumes the result; no
DB-side work in BT.5.

## BT.4 pre-fill scope (SPEC's open question)

SPEC: "Each partial match links into `/studio/l2` *pre-filled to the
edit that would close the gap*. ... Comment: pre-filled may be a
stretch, we may have to evaluate when we get here what is possible."

**Sub-decision (locked):** BT.4 ships **link-only** in v1 — the
partial-match panel shows the gap diagnosis + a deep link to the
relevant `/studio/l2/<kind>/<entity>` editor page (which already
exists for every entity kind per BF). Pre-fill of NEW entity creation
forms (e.g., "add this missing rail") stays a follow-on; the link
takes the operator to the kind's list page where they can click "+
Add" themselves. Risk: an operator might pick the wrong existing rail
to edit when the gap is actually "missing rail." Mitigation: BT.4's
diagnosis text says "no Rail named `<rail_name>` in the L2" — operator
goes to "+ Add" rather than picking an existing one. The diff between
"deep-link to edit existing" and "deep-link to create new with
pre-fill" is small enough that we can land both later if cold-read
flags the friction.

## BT.6 cold-read agent persona

SPEC: "give an agent the SPEC section + any wireframes / page sketches
/ prose / partial implementations. Ask it to read as a named persona
('you are the ETL Engineer arriving at `/studio/etl` for the first
time')."

**Sub-decision (locked):** BT.6 cold-read persona = **"first-time ETL
Engineer for a midsize credit union, hands-on Python + SQL background,
no Recon-Gen exposure"**. Output: `docs/audits/bt_cold_read.md` keyed
on the same "what's confusing / missing / skippable / non-landing
concept name" rubric the existing dashboard cold-reads use. Operator
critique + sign-off after.

## PLAN.md BT.1-N refinement (output)

Updated PLAN.md entries (no new tasks; just sharpened acceptance criteria):

- **BT.0** — DONE (this doc).
- **BT.0.5** — Agent-driven design mockups. Hand off SPEC.md::Phase BT + this lock list + the L2 primitives module to the agent. Output: `docs/audits/bt_design_mockups.md` (ASCII wireframes for 3 pages + 1 sequence diagram for the truncate→hook→refresh→triage flow). Cycle 2× with operator critique. Estimated 60-90 min.
- **BT.1** — `/studio/etl` flat-nav entry + landing page. Extends `make_studio_routes`. Landing page is a 3-card index (Probe / Run / Triage) with one-line per-page descriptions. Estimated 60 min.
- **BT.2** — D4.surface #1: L2-slice probe. Author `_v_config_transfer_templates` projection view (BS.1 carryover). Probe page: operator picks one rail / template / chain from dropdowns; page renders the L2-side column expectations table + the actual runtime rows matching that slice. Reuses existing `common/l2/coverage.py` helpers. Estimated 3-4h.
- **BT.3** — D4.surface #2: ETL execution + coverage report. "Run" button POSTs to `/etl/run` which invokes `run_deploy_pipeline` with `test_generator.enabled=False`. Page polls + renders per-kind tally (rail count / template count / chain count / metadata-key landed yes-no per template). Reuses `coverage_for` + `data_generation_id` polling. Estimated 3-4h.
- **BT.4** — D4.surface #3: Exception triage + handoff. Author `derive_column_contracts` (BT.5 prerequisite). Triage page diffs (declared contracts) vs (observed runtime rows); for each gap, render a card with the diagnosis + a deep link to the relevant editor page. Link-only (no pre-fill) in v1. Estimated 4-6h.
- **BT.5** — Per-column-pair contract derivation in `common/l2/contract.py`. Pure function over `L2Instance`; output consumed by BT.4. Unit tests only (no DB). Estimated 2-3h.
- **BT.6** — Agent cold-read. Output: `docs/audits/bt_cold_read.md`. Operator iteration + sign-off. Estimated 60-90 min.

**Sequencing:** BT.5 can run in parallel with BT.2/BT.3 (it's pure-Python module work with no UI coupling). BT.4 depends on BT.5 + BT.2's L2-slice picker logic. Suggested order: BT.0.5 → BT.1 + BT.5 (parallel) → BT.2 → BT.3 → BT.4 → BT.6.

**Phase BT total estimate:** ~14-18 hours focused work.

## Out of scope (BU / BV territory)

- Plant generation surfaces (BU).
- Generator-as-etl-hook dogfood (BV.2).
- The HUGE round-trip test (BV.4).
- mkdocs/docs templating from `_kv` (BW, may not fire).
