# BH.0 — v11.21.0 cold-read finding state snapshot

**Method**: ran every BG.X assertion (+ adjacent direct-SQL probes) against
the sasquatch_pr-backed sqlite (`run/sasquatch.sqlite`, built per
`feedback_spec_example_seed_thin_for_validation`) on 2026-05-25. Per
finding: current red/green state + fix surface + which BH.X cell owns
the work.

## Snapshot

| # | Finding | Status | Notes |
|---|---|---|---|
| **#1** | Daily Statement Drift KPI ≠ narrative formula | **GREEN** | All 3,928 matview rows satisfy `drift = closing_stored − (opening + net_flow)`. The matview computation agrees with the sheet narrative on the current code path. Cold-read may have been version-specific (older deploy) or against a different L2 instance. **BG.2's narrative-formula assertion holds on this seed.** |
| **#2** | Daily Statement Business-Day picker non-functional | **BROWSER-LAYER** | SQL pushdown returns distinct rows per day (raw SQL probe confirms). Bug — if real on current deploy — lives in the flatpickr → hidden-input → HTMX-refresh chain. Real-browser test required to localize. |
| **#3** | Negative Opening Balance on class-restricted role | **RED** | **316 `CustomerDDA` rows + 85 `ACHOrigSettlement` rows have negative Opening Balance (min = −$1,373,780)**. CustomerDDA is the cardholder class — the bug shape #3 names. Either (a) seed legitimately produces negative balances on CustomerDDA (seed defect: cardholder Opening ≥ 0 invariant violated) OR (b) matview's LAG-from-prior-day picks up unrelated rows. |
| **#4** | "Latest Snapshot Drift" SUM-cancellation | **N/A** | No KPI by this name in current code. Likely cold-read was against an older deploy. Cell can close as "no current bug to fix." |
| **#5** | Volume Anomalies KPI=0 vs populated distribution | **GREEN** | filtered=122 rows == distribution_above_σ=122 (default σ=2 on this seed). KPI matches dataset; chart matches matview. Cold-read's "KPI=0 / chart populated" doesn't reproduce — sasquatch's seed plants enough anomalies above default σ. |
| **#6** | Leaf Drift Timelines flat constant | **GREEN** | 10 days / 3 distinct per-day sums on this seed. Variance gate would pass. Cold-read's "$15 flat across 30+ days" doesn't reproduce. |
| **#7** | Recipient Fanout cartesian inflation | **RED** | Inflated SUM = **$22,078,499.69** vs deduped truth = **$21,797,950.68** = $280,549 over-count (1.0129× ratio). BG.4's `test_bg4_recipient_fanout_kpis_match_inflows_only_truth` trips RED on real browser run. Fix in `apps/investigation/datasets.py:276 build_recipient_fanout_dataset` (inflows-side aggregate before outflows join). |
| **#8** | Total Transactions vs App Info gap | **BY-DESIGN** | KPI sum=38,758 (per-Posted-transfer collapsed); per-leg-all-status=76,488; gap 49.3%. Predicate mismatch by design per triage doc. Fix is subtitle clarification, not SQL. BG.5's contract assertion holds. |
| **#9** | Today's Exceptions one-bar dominance | **RED-PRESENTATION** | `stuck_unbundled` = 129/155 (83.2%) on this seed. Bar chart legitimately dominated. Not BG scope (presentation); BH.9 owns. |
| **#10** | Executives stacked-bar legend overwhelm | **RED-PRESENTATION** | 32 distinct rail_name values → 32-entry legend. Not BG scope. BH.10 owns (top-N + Other bucket). |
| **#11** | L2 Exceptions KPI / table units mismatch | **RED-NARRATIVE** | KPI=9 rows vs table count_sum=113 (12.6× ratio). Two correct measures; narrative fix needed. BG.6 enforces each binding matches; BH.11 owns the rename. |
| **#12** | Internal Overdraft KPI=0 vs populated table | **NEEDS-BROWSER** | Overdraft dataset returns 406 rows on this seed. Raw SQL probe confirms `len(rows)=406`; the bug (if real) is in `.count()` resolution on the renderer side. BG.3's test would trip if rendered KPI != 406. |
| **#13** | Pending Aging KPI/table/chart triple disagreement | **NEEDS-BROWSER** | Stuck pending dataset = 2 rows. Triple-identity gate (KPI == table == chart-bar-sum) catchable only against rendered chart bars; data probe can't tell. |
| **#14** | 3-decimal currency formatting | **PARSER-GATE** | `_kpi_parse.parse_currency_kpi` raises on 3+ decimals; would trip per-KPI-read site in real browser run. Per-KPI surface inspection needed to confirm rendered precision matches the gate. |
| **#15** | Empty-state discipline gap | **NEEDS-VISUAL** | copy/format/presentation — data probe can't tell |
| **#16** | L1 getting-started typo + leaked seed-config | **NEEDS-VISUAL** | copy edit |
| **#17** | Executives `(vL1.5.21*)` build-note leak | **NEEDS-VISUAL** | copy edit |
| **#18** | App Info Matview Status only 2 matviews | **NEEDS-VISUAL** | needs rendered App Info inspection |
| **#19** | Daily Statement KPI tile text-clipping | **NEEDS-VISUAL** | CSS / layout |
| **#20** | Unbundled Aging KPI label rendering | **NEEDS-VISUAL** | format-prefix vs literal-string |
| **#21** | App Info Deploy Stamp dialect token leak | **NEEDS-VISUAL** | config-flag decision |
| **plant** | Hardcoded `ach` rail in spine emitters | **RED** | **7 transactions with `rail_name='ach'` that's NOT declared as a Rail on the sasquatch_pr L2**. Confirms the plant-coverage finding. 6 spine modules carry `rail_name="ach"` hardcoded defaults — they emit into transactions, fail the rail-name ⊆ L2-declared-rails invariant for L2 instances that don't declare "ach". BH.22 owns the fix. |

## Tally

- **RED (confirmed bug on current code + data)**: #3 (cardholder
  negative Opening), #7 (fanout cartesian inflation), `plant`
  (hardcoded ach) — **3 findings**.
- **BY-DESIGN / narrative**: #8 (predicate mismatch), #11 (units
  mismatch) — **2 findings**, subtitle/rename fixes.
- **RED-PRESENTATION**: #9 (one-bar dominance), #10 (legend
  overwhelm) — **2 findings**, not BG scope.
- **NEEDS-BROWSER** (catchable only against rendered visuals):
  #2, #12, #13, #14 — **4 findings**.
- **NEEDS-VISUAL** (copy/format/CSS — visual inspection):
  #15-#21 — **7 findings**.
- **GREEN** on current seed: #1, #5, #6 — **3 findings**.
  Either cold-read was on older deploy, or sasquatch_pr's seed
  shape differs from the cold-read's L2. BG gates would still
  catch regressions.
- **N/A**: #4 — no current KPI by that name.

## Priority for BH.1+

Order BH.X work by:
1. **RED (real bugs)** — fix first, satisfies BG's already-red gates:
   - BH.7 (fanout cartesian) — BG.4 is already red, would turn green
   - BH.3 (cardholder negative Opening) — root-cause: seed defect or matview LAG
   - BH.22 (ach plant) — thread real rail name from L2
2. **BY-DESIGN narrative** — small subtitle/rename work:
   - BH.8 (Total Transactions predicate subtitle)
   - BH.11 (L2 Exceptions KPI rename)
3. **NEEDS-BROWSER** — run BG.2-6 against a real deploy + read the results:
   - BH.2 (date picker real-browser repro)
   - BH.12 (overdraft .count() resolution)
   - BH.13 (pending aging triple-identity)
   - BH.14 (3-decimal currency — confirm parser trips at expected sites)
4. **Presentation** — low-priority polish:
   - BH.9, BH.10 (one-bar + legend overwhelm)
5. **Copy / format / CSS** — single-line edits:
   - BH.15-BH.21
6. **Close as N/A**: BH.4

## Coverage-by-construction notes

- **BG.4 fanout test reds on real data** — confirmed; the test serves
  as both the gate AND the visible bug signal.
- **BG.2 narrative-formula assertion holds on real data** — finding
  #1 isn't a current bug. Cell could be reframed as "confirm
  narrative-formula contract holds in production deploys" rather
  than "fix the gap."
- **BH.0 conclusion**: cold-read surface that's actually red on
  current code is **3 bugs + 2 narratives + 2 presentation + 7
  copy/format**. The rest are GREEN, by-design, or need real-browser
  inspection.
