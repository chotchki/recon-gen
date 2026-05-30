# BT design mockups

> **Status:** LOCKED 2026-05-30. First-cut agent draft + operator
> review pass 1 → all open questions resolved on first read (the
> probe date-range got flipped; rest held the agent's lean). Drives
> BT.1-BT.4 implementation. Authored against SPEC.md::Phase BT +
> `docs/audits/bt_0_replan.md` + `common/l2/primitives.py` + BS.3
> top-nav.

## Headline

Four routes under `/studio/etl/`: landing index (BT.1),
`/etl/probe` (BT.2 — L2-slice probe), `/etl/run` (BT.3 — execution +
coverage), `/etl/triage` (BT.4 — exception triage + handoff). All
sit behind the shared BS.3 top-nav; all reuse the Studio's existing
card / pill / details vocabulary — no new component primitives.
Persona: the **ETL Engineer** on a midsize credit union. Loop:
`truncate → run hook → triage gaps → jump to L2 editor → fix →
repeat`, sub-1-minute per cycle.

## 1. Sequence diagram — the BT.3 run flow

`/etl/run` button POSTs (no payload — L2 + cfg already resident).
Server invokes `run_deploy_pipeline` with `test_generator.enabled
=False` (BT.0 lock 1). Each step emits a dev-log event the browser
tails over `/dev_log/stream` (BS.2 SSE). Page polls
`/data_generation_id` for the auto-reload signal.

```
Browser                  Server                    DB
  │                        │                        │
  │  POST /etl/run         │                        │
  ├───────────────────────▶│                        │
  │                        │  cfg.patch(test_       │
  │                        │    generator.enabled   │
  │                        │    =False)             │
  │                        │                        │
  │                        │  dev_log: deploy:start │
  │                        │                        │
  │                        │── step_2_wipe ────────▶│ TRUNCATE
  │  ◀── SSE: step2:start  │                        │   <prefix>_transactions
  │                        │  dev_log: step2:wipe   │   <prefix>_daily_balances
  │  ◀── SSE: step2:done   │                        │
  │                        │                        │
  │                        │── step_1_etl_hook ────▶│ INSERT
  │  ◀── SSE: step1:start  │   (subprocess /        │   <prefix>_transactions
  │                        │    plugin call)        │   <prefix>_daily_balances
  │                        │                        │
  │                        │   ┌── exit=0 ────────┐ │
  │                        │   │                  │ │
  │                        │   ▼                  │ │
  │                        │  dev_log: step1:done │ │
  │  ◀── SSE: step1:done   │                      │ │
  │                        │                      │ │
  │                        │  (step_3 SKIPPED —   │ │
  │                        │   test_generator     │ │
  │                        │   .enabled=False)    │ │
  │                        │                      │ │
  │                        │── step_4_matviews ──▶│ │ REFRESH MATVIEW
  │  ◀── SSE: step4:start  │                      │ │   <prefix>_inv_*
  │                        │                      │ │   <prefix>_l1_*
  │  ◀── SSE: step4:done   │                      │ │
  │                        │                      │ │
  │                        │── step_5_reload ────▶│ │ bump data_generation_id
  │  ◀── SSE: step5:done   │                      │ │
  │                        │                      │ │
  │  ◀── 200 {ok:true,    │                      │ │
  │      data_gen_id:N+1} │                      │ │
  │                        │                      │ │
  │  GET /data_generation_id (poll, was N now N+1) │
  ├───────────────────────▶│                      │ │
  │  ◀── {id: N+1} ────────│                      │ │
  │                        │                      │ │
  │  auto-reload coverage  │                      │ │
  │  card grid via hx-get  │                      │ │
  │  /etl/coverage         │                      │ │
  ├───────────────────────▶│                      │ │
  │                        │  coverage_for(L2)   ─┼─▶ SELECT
  │  ◀── card grid HTML ───│                      │ │   per-kind tallies
  │                        │                      │ │
  │                        │                      │ │
  │                  HALT BRANCH (etl_hook exit ≠ 0):
  │                        │   │  exit ≠ 0       │ │
  │                        │   ▼                 │ │
  │                        │  dev_log:           │ │
  │                        │    step1:halted     │ │
  │  ◀── SSE: halted ──────│                     │ │
  │                        │                     │ │
  │  ◀── 200 {halted:true,│                     │ │
  │      reason, stderr,  │                     │ │
  │      exit_code}       │                     │ │
  │                        │  (step_4 + step_5  │ │
  │                        │   SKIPPED — wipe   │ │
  │                        │   already ran;     │ │
  │                        │   DB is empty)     │ │
  │                        │                     │ │
  │  banner: "Halted —     │                     │ │
  │  DB is empty until     │                     │ │
  │  next successful run"  │                     │ │
  │  + [→ Triage] CTA      │                     │ │
```

**Notes:** Wipe runs *before* the hook — a halted hook leaves the DB
empty, surfaced loudly on the run page. Step 3 is skipped at the cfg
layer (not by branching inside the pipeline), keeping the BT.3 entry
point a thin wrapper. SSE + data_generation_id polling are the same
shape the home page's Deploy-changes button already uses (X.4.g.14).

## 2. BT.2 — L2-slice probe wireframe

```
┌──────────────────────────────────────────────────────────┐
│ Recon-Gen │ L2 Editor │ ETL Support [●] │ Training │ ... │
├──────────────────────────────────────────────────────────┤
│ Studio · ETL · L2-slice probe              sasquatch_pr  │
├──────────────────────────────────────────────────────────┤
│                                                          │
│ Pick a slice of the L2 to probe:                         │
│                                                          │
│ ┌──────────┬───────────────────┬──────────┐              │
│ │ ◉ Rail   │ ○ Transfer Tmpl   │ ○ Chain  │              │
│ └──────────┴───────────────────┴──────────┘              │
│                                                          │
│ Rail name:  [ ach_credit                          ▼ ]    │
│             (typeahead — filters as you type)            │
│                                                          │
│ Observation window:                                      │
│   From [ 2026-04-01 ]  To [ 2026-05-30 ]   [Apply]       │
│   Defaults to last 7 days; widen for backfill / mass-    │
│   load scenarios where the data lives outside the window │
│                                                          │
├──────────────────────────────────────────────────────────┤
│ EXPECTED (from L2)        │ OBSERVED (window)            │
├───────────────────────────┼──────────────────────────────┤
│ Column          Expected  │ Showing 10 of 1,247 rows     │
│                           │   (in window 2026-04-01 →    │
│                           │    2026-05-30)               │
│ ─────────────  ─────────  │ ────────────────────────     │
│ rail_name      = ach_     │ tx-001  ach_credit ✓         │
│                  credit   │   role=CustomerLedger ✓      │
│                           │   dir=Credit ✓               │
│ account_role   ∈ {        │   metadata: trace_id ✓       │
│                  Customer │                              │
│                  Ledger,  │ tx-002  ach_credit ✓         │
│                  ExtCorr  │   role=ExtCorrespondent ✓    │
│                  esponde- │   dir=Debit ✓                │
│                  nt }     │   metadata: trace_id ✓       │
│                           │                              │
│ leg_direction  variable   │ tx-003  ach_credit ✓         │
│   (template-   (one per   │   role=GLSuspense ✗          │
│    closed)     leg)       │   dir=Credit ✓               │
│                           │   metadata: trace_id ✓       │
│ metadata.      required:  │                              │
│   trace_id     non-null   │ tx-004  ach_credit ✓         │
│                           │   role=CustomerLedger ✓      │
│ metadata.      optional   │   dir=Credit ✓               │
│   memo                    │   metadata: trace_id —       │
│                           │                              │
│                           │ ...                          │
│                           │                              │
│                           │ Legend: ✓ matches L2         │
│                           │         ✗ contradicts L2     │
│                           │         — column has no L2   │
│                           │           expectation        │
└───────────────────────────┴──────────────────────────────┘
```

Empty-state copy when the slice yields zero rows:

```
┌──────────────────────────────────────────────────────────┐
│ OBSERVED (window)                                        │
├──────────────────────────────────────────────────────────┤
│                                                          │
│           No rows match this slice.                      │
│                                                          │
│   The L2 declares this rail / template / chain but       │
│   the ETL hook hasn't produced any matching rows in      │
│   the selected window.                                   │
│                                                          │
│   → Widen the window — backfill / historical loads may   │
│     live outside today's default.                        │
│                                                          │
│   → Check the Run + coverage page to see when the        │
│     last ETL run completed.                              │
│                                                          │
│   → If the last run was recent, this slice may be a      │
│     real ETL gap. Open Triage.                           │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

**Serves the ETL Engineer:** smallest possible debugging unit (pick
a thing the L2 declares, see what the ETL produced for it). No app
nav, no SQL. Side-by-side layout puts contract next to evidence so
red cells are scannable. Operator-controlled window (defaults to
last 7 days; widens for backfill / mass-load scenarios where the
data lives outside the live window).

**Data sources:**

- Expected (left): BT.5's `derive_column_contracts(L2Instance)`
  filtered to the picked entity (Rail: `rail_name`, `account_role`
  union, `leg_direction`, per-key metadata; Template: leg_rails
  union + `template_name`; Chain: parent + per-child
  `transfer_parent_id`).
- Observed (right): SELECT against `<prefix>_transactions` filtered
  by the slice's `rail_name` / `template_name` / parent AND the
  operator's selected date window. Direct query; no SPICE.
- Dropdown: typeahead picker — filters L2-declared names of the
  picked kind as the operator types. Same widget vocabulary as the
  existing L2 editor's relationship pickers; works for both small
  (~5 rails) and large (>50 rails) corpora.

## 3. BT.3 — ETL execution + coverage report wireframe

```
┌──────────────────────────────────────────────────────────┐
│ Recon-Gen │ L2 Editor │ ETL Support [●] │ Training │ ... │
├──────────────────────────────────────────────────────────┤
│ Studio · ETL · Run + coverage              sasquatch_pr  │
├──────────────────────────────────────────────────────────┤
│                                                          │
│           ┌────────────────────────┐                     │
│           │    ▶  Run ETL          │  last run:          │
│           └────────────────────────┘  2026-05-30 14:23   │
│                                       duration: 12.4s    │
│                                       status: ● success  │
│                                                          │
├──────────────────────────────────────────────────────────┤
│ LIVE LOG                                                 │
├──────────────────────────────────────────────────────────┤
│ 14:23:01 [step2:wipe] start                              │
│ 14:23:01 [step2:wipe] truncated 2 tables                 │
│ 14:23:01 [step2:wipe] done (0.3s)                        │
│ 14:23:01 [step1:etl_hook] start (cmd: ./feed.py)         │
│ 14:23:09 [step1:etl_hook] wrote 14,221 transactions      │
│ 14:23:09 [step1:etl_hook] wrote 2,114 daily_balances     │
│ 14:23:09 [step1:etl_hook] done (8.1s)                    │
│ 14:23:09 [step3:generator] skipped (disabled)            │
│ 14:23:09 [step4:matviews] start                          │
│ 14:23:13 [step4:matviews] refreshed 7 matviews           │
│ 14:23:13 [step4:matviews] done (3.7s)                    │
│ 14:23:13 [step5:reload] data_generation_id 42 → 43       │
│ 14:23:13 deploy:done (12.4s)                             │
│                                                          │
│ [ ↻ Re-run ]  [ Clear log ]                              │
├──────────────────────────────────────────────────────────┤
│ COVERAGE                                                 │
├──────────────────────────────────────────────────────────┤
│ ┌────────────────┐ ┌────────────────┐ ┌────────────────┐ │
│ │ Rails          │ │ Templates      │ │ Chains         │ │
│ │                │ │                │ │                │ │
│ │   ●●●●●○○      │ │   ●●●●○○○      │ │   ●●○○○○○      │ │
│ │   5/7 = 71%    │ │   4/7 = 57%    │ │   2/6 parents  │ │
│ │                │ │                │ │   firing       │ │
│ │ ach_credit  ✓  │ │ MerchantSet ✓  │ │   1/6 closed   │ │
│ │ ach_debit   ✓  │ │ Payroll      ✓ │ │   loop         │ │
│ │ wire        ✓  │ │ ACHReturn   ✓  │ │                │ │
│ │ check       ✓  │ │ WireOut     ✓  │ │ MerchantSet ✓  │ │
│ │ sweep       ✓  │ │ CheckClear  ✗  │ │   (firing)     │ │
│ │ card        ✗  │ │ CardSettle  ✗  │ │ Payroll      ✗ │ │
│ │ atm         ✗  │ │ FeeAssess   ✗  │ │   (no parents) │ │
│ └────────────────┘ └────────────────┘ └────────────────┘ │
│                                                          │
│ ┌──────────────────────────────────────────────────────┐ │
│ │ Metadata                                             │ │
│ │                                                      │ │
│ │   18/24 required metadata keys landed (75%)          │ │
│ │                                                      │ │
│ │ Per template:                                        │ │
│ │   MerchantSettlement  3/3 keys ✓                     │ │
│ │   Payroll             4/4 keys ✓                     │ │
│ │   ACHReturn           2/3 keys ✗  missing: reason    │ │
│ │   WireOut             3/3 keys ✓                     │ │
│ │   CheckClear          0/4 keys ✗  no rows            │ │
│ │   CardSettlement      0/5 keys ✗  no rows            │ │
│ │   FeeAssessment       6/6 keys ✓                     │ │
│ │                                                      │ │
│ └──────────────────────────────────────────────────────┘ │
│                                                          │
│ Coverage report green = ETL contract satisfied.          │
│ Not green? → Open Triage to see specific gaps.           │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

Empty-state when no ETL has been run yet (first-time visit):

```
┌──────────────────────────────────────────────────────────┐
│           ┌────────────────────────┐                     │
│           │    ▶  Run ETL          │  no runs yet        │
│           └────────────────────────┘                     │
├──────────────────────────────────────────────────────────┤
│ COVERAGE                                                 │
├──────────────────────────────────────────────────────────┤
│                                                          │
│   No ETL has been run yet on this L2.                    │
│                                                          │
│   Click "Run ETL" above to invoke the configured ETL     │
│   hook against the demo DB. Coverage shows up here       │
│   once the run completes.                                │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

Halt-state banner above coverage when the last run halted:

```
┌──────────────────────────────────────────────────────────┐
│ ⚠ Last run HALTED at step1:etl_hook (exit code 17)       │
│                                                          │
│ stderr: feed.py: KeyError: 'trace_id' on row 1422        │
│                                                          │
│ The DB was wiped before the hook ran — it is currently   │
│ empty. Dashboards will be blank until the next           │
│ successful run.                                          │
│                                                          │
│ [ → Open Triage ]  [ ↻ Re-run ]                          │
└──────────────────────────────────────────────────────────┘
```

**Serves the ETL Engineer's iteration loop:** one button = one full
cycle (no terminal). Live log = fast feedback that a slow hook is
doing something. Coverage cards score the run at a glance — green
move on, red drill into Probe / Triage. Metadata card carries the
SPEC-mandated per-template required-key tally inline.

**Data sources:**

- Run button: POSTs `/etl/run` → wraps `run_deploy_pipeline` with
  `test_generator.enabled=False` (BT.0 lock 1).
- Live log: SSE on `/dev_log/stream`, filtered to `event` prefix
  `deploy:` (BS.2 channel).
- Coverage cards: existing `common/l2/coverage.py::coverage_for`.
  Per-template metadata table is a new
  `metadata_coverage_per_template(L2, db)` helper — extension to
  `coverage_for`, not a separate module.

## 4. BT.4 — Exception triage + handoff wireframe

```
┌──────────────────────────────────────────────────────────┐
│ Recon-Gen │ L2 Editor │ ETL Support [●] │ Training │ ... │
├──────────────────────────────────────────────────────────┤
│ Studio · ETL · Triage                      sasquatch_pr  │
├──────────────────────────────────────────────────────────┤
│                                                          │
│ 4 gaps detected · last triage 2026-05-30 14:23           │
│                                                          │
│ [ Filter: All ▼ ] [ Sort: By kind ▼ ] [ ↻ Re-check ]     │
│                                                          │
├──────────────────────────────────────────────────────────┤
│                                                          │
│ ┌──────────────────────────────────────────────────────┐ │
│ │ ⚠ Unmatched rail_name                                │ │
│ │                                                      │ │
│ │ 47 rows arrived with rail_name="ach" but the L2      │ │
│ │ declares no Rail of that name.                       │ │
│ │                                                      │ │
│ │ The L2 declares these Rails: ach_credit, ach_debit,  │ │
│ │ wire, check, sweep, card, atm.                       │ │
│ │                                                      │ │
│ │ Sample row: tx-13422  account_id=cust-001            │ │
│ │             account_role=CustomerLedger ✓            │ │
│ │             rail_name=ach ✗                          │ │
│ │             posted_at=2026-05-30 09:14               │ │
│ │                                                      │ │
│ │ [ → Open Rails editor ]   [ Hide this gap kind ]     │ │
│ └──────────────────────────────────────────────────────┘ │
│                                                          │
│ ┌──────────────────────────────────────────────────────┐ │
│ │ ⚠ Unmatched template_name                            │ │
│ │                                                      │ │
│ │ 12 rows arrived tagged with                          │ │
│ │ template_name="ReturnReversal" — no such template    │ │
│ │ in the L2.                                           │ │
│ │                                                      │ │
│ │ Closest declared templates: ACHReturn, CheckReturn.  │ │
│ │ Operator decides: add the new template OR rename     │ │
│ │ the ETL's tag to match an existing one.              │ │
│ │                                                      │ │
│ │ Sample row: tx-15001  template_name=ReturnReversal   │ │
│ │             rail_name=ach_debit                      │ │
│ │                                                      │ │
│ │ [ → Open Templates editor ]   [ Hide this gap kind ] │ │
│ └──────────────────────────────────────────────────────┘ │
│                                                          │
│ ┌──────────────────────────────────────────────────────┐ │
│ │ ⚠ No LimitSchedule for (CustomerLedger, wire,        │ │
│ │   Outbound)                                          │ │
│ │                                                      │ │
│ │ 142 wire-out rows landed against CustomerLedger      │ │
│ │ but no LimitSchedule covers this triple. L1 Limit    │ │
│ │ Breach shows these as "no cap" in dashboards.        │ │
│ │                                                      │ │
│ │ Existing LimitSchedules for CustomerLedger:          │ │
│ │   (CustomerLedger, ach_debit, Outbound) — cap $5k    │ │
│ │   (CustomerLedger, ach_debit, Inbound)  — cap $10k   │ │
│ │                                                      │ │
│ │ [ → Open Limits editor ]   [ Hide this gap kind ]    │ │
│ └──────────────────────────────────────────────────────┘ │
│                                                          │
│ ┌──────────────────────────────────────────────────────┐ │
│ │ ⚠ Missing required metadata key: reason              │ │
│ │                                                      │ │
│ │ Template ACHReturn declares "reason" as required.    │ │
│ │ 23 of 31 ACHReturn rows landed without it — L1       │ │
│ │ Conservation can't bucket them. Operator decides:    │ │
│ │ fix the ETL to emit "reason", or drop the key from   │ │
│ │ the template if upstream genuinely doesn't carry it. │ │
│ │                                                      │ │
│ │ Sample row: tx-14008  metadata.reason = (null)       │ │
│ │                                                      │ │
│ │ [ → Open ACHReturn template ]   [ Hide this gap ]    │ │
│ └──────────────────────────────────────────────────────┘ │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

Empty-state when no gaps detected:

```
┌──────────────────────────────────────────────────────────┐
│ Studio · ETL · Triage                                    │
├──────────────────────────────────────────────────────────┤
│                                                          │
│           ● No gaps detected.                            │
│                                                          │
│   Every row produced by the last ETL run matches the     │
│   L2's declared contracts. Coverage tally is green —     │
│   the ETL contract is satisfied.                         │
│                                                          │
│   Last checked: 2026-05-30 14:23                         │
│                                                          │
│   → Re-check on next ETL run, or after editing the L2.   │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

**Serves the ETL Engineer's find-and-fix loop:** one card = one
decision; diagnosis prose self-contained (what the ETL produced,
what the L2 declares, the gap in English — no SQL, no "look up
matview X"). Deep link puts the engineer one click from the fix.
"Hide this gap kind" scopes a triage session (in-memory, cleared on
next run).

**Link-only-v1 (BT.0 lock 5):** CTA goes to the kind's editor list
page (e.g., `/l2_shape/rail/`), not a pre-filled "create new" form.
Diagnosis text names the gap precisely ("no Rail named `ach` in the
L2") so the engineer knows whether to click "+ Add" or edit an
existing entity. Pre-fill is a follow-on if cold-read flags friction.

**Data sources:**

- `derive_column_contracts(L2)` (BT.5) for the declared side.
- New `detect_gaps(contracts, db) → list[Gap]` helper diffing
  contracts against `<prefix>_transactions`. `Gap` is typed: `kind`
  (rail / template / limit / metadata), `evidence` (row count +
  sample row), `link_target` (URL).

## 5. Cross-page navigation flow

Probe = investigate one slice; Run = execute + score; Triage =
find + fix gaps.

```
                    ┌───────────────────┐
                    │ /etl/             │
                    │ (landing index)   │
                    └─────────┬─────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
       ┌────────────┐  ┌────────────┐  ┌────────────┐
       │  Probe     │  │  Run +     │  │  Triage    │
       │            │  │  coverage  │  │            │
       │ (BT.2)     │  │  (BT.3)    │  │  (BT.4)    │
       └─────┬──────┘  └─────┬──────┘  └─────┬──────┘
             │               │               │
             │               │               │
             │               └──── halt ─────▶ "Open Triage"
             │                                 (banner CTA)
             │                                 │
             ▼                                 ▼
       Empty slice                       Gap card
       → "Open                           → "Open <kind>
          Triage"                          editor"
                                           (deep link to
                                            /studio/l2/<kind>/)
```

**Transitions:**

- Probe → Triage: empty-state button ("this slice has no rows —
  check Triage").
- Run → Triage: halt-banner button OR coverage-not-green CTA.
- Triage → `/studio/l2/<kind>/`: link in each gap card (link-only
  per BT.0 lock 5).
- Run → Probe: implicit (operator picks Probe from nav); no inline
  CTA — a coverage card has no obvious default slice to deep-link.
- Landing (BT.1): 3-card index, one card per child page + one-line
  description.

## 6. Resolved decisions (operator review 2026-05-30)

The first-cut open questions all resolved on first read; agent leans
held except where flipped explicitly.

1. **Probe — date-range axis: FLIPPED. Day-range picker in v1.**
   Operator note: "there's a difference between implementation and
   ongoing maintenance. In implementation you may be mass loading
   data VERY far back so last 7 days will suck." Backfill /
   historical-load scenarios live outside any default window;
   forcing the engineer to one is a real friction. Wireframe
   updated above with `From` / `To` controls + `Apply` button;
   defaults to last 7 days; widens on operator action.

2. **Triage — "green" affirmation: LOCKED.** Operator confirms:
   "better to say everything looks good so someone knows it worked
   than to say nothing and let people wonder." Empty-state keeps
   the explicit `● No gaps detected · all column expectations
   matched` affirmation copy.

3. **Run — explicit button only: LOCKED.** Operator confirms: "yes
   on explicit button press." `/etl/run` GET renders the button +
   last-run state; POST is the click. No auto-fire on page visit.

4. **Run — cap + see-all on coverage cards: LOCKED.** Operator
   confirms: "cap + see all is fine, any real system will always
   have more than 30." Per-kind cards cap inline lists at 10 with
   a `[ see all N ]` link to the kind's full coverage page.

5. **BT.5 — LimitSchedule out of Probe v1: LOCKED.** Operator
   confirms: "I think limit schedule won't be nearly as heavily
   used as the other 3, defer for a v1." BT.2 Probe surfaces
   `Rail / Template / Chain`. LimitSchedule contracts appear in
   BT.4 Triage (declared-vs-observed gap detection) but not as a
   probe-slice option in v1.

## 7. Out-of-scope follow-up captured

Operator flagged a separate cleanup during review:

- **Remove the Deploy button from the `/l2` editor page.** The
  per-entity edit form has no deploy semantics — Deploy belongs on
  the diagram / data / ETL pages where the operator has the
  full-cfg context. Tracked as a Studio-cleanup follow-up entry in
  PLAN.md (not part of BT scope; BT delivers new surfaces, not
  cleanup on existing ones).

---

## Appendix — visual vocabulary reused from existing Studio

- Top nav: shared BS.3 `build_top_nav_entries`; `ETL Support` entry
  already declared (dead link until BT.1 lands the routes).
- Page header: `<h1>Studio</h1>` + mono prefix + breadcrumb prose,
  matching `_render_home_page`.
- Cards / sections: `bg-white border border-surface-border rounded-md
  mb-3` (matches `_HOME_SECTIONS`). Coverage + triage cards reuse.
- Accent button: `bg-accent text-accent-fg border border-accent
  px-3 py-1 rounded-sm` (Deploy-changes pattern).
- Status colors: `text-success` / `text-warning` / `text-danger`
  from the studio theme head.
- Live log: monospace block over SSE; mirrors `/dev_log/stream`
  console (BS.2).
