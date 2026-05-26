# recon-gen v11.22.0 — local-stack dashboard validation findings

Findings from a 4-judge cold-read against the operator-facing reconciliation dashboards on a non-trivial L2 instance. Scrubbed of deployment-specifics; structured as actionable upstream bug + training-UX reports.

**Build under test**: `recon-gen v11.22.0` (commit `4d6bf92f`); effective build at capture time was `v11.22.1` (`0913b1d7`, "markdown dep + BH.20 test fix + Phase BJ" — non-dashboard-behavior patch). Same `.yaml` instance + seed as the previous round (`v11.21.0`). 3,032,345 transactions, 1,188 daily_balances.

## Key context: dashboard path is the production surface; data shown here is training data

The L2 instance under test contains **purposeful errors seeded for training purposes** (exception counts, drifts, overdrafts, sign-conflicts are by-design data, configurable via a separate trainer surface — out of scope for this validation). BUT the dashboards being cold-read here ARE the production path; in real deployments the same dashboard binary runs against real ledger data. So all dashboard defects found here apply to production usage too — the difference is only the data source.

Two operative categories below:
- **Real dashboard defects** — presentation / math / wiring / chart-rendering bugs. Apply to production usage equally regardless of whether the data is planted training material or real ledger movement. Escalate.
- **Plant-surfacing-as-designed observations** — the planted training error is visible. Not a bug; confirmation that the dashboard correctly surfaces the underlying data shape. Does not apply to production (no plants in prod data); included only as triage context so upstream knows which "looks weird" findings are the dashboard's fault vs the seed's by-design content.

(A third category — "default training volume is overwhelming" — is operator-configurable via the trainer surface, not a dashboard defect. Listed for completeness only.)

When a finding below describes a chart that's unreadable because one category dominates, or a KPI that hides per-account fires via SUM cancellation, or a sheet that loads blank because no parameter is preselected: those are dashboard defects that would equally benefit a production operator looking at real data. Don't read "this affects the training mission" as "training-only" — the dashboard's ability to surface signal cleanly applies to all data.

## Headline: BH cascade landed the biggest wins of any round so far

Recently-resolved (cross-judge confirmation):

| Prior finding | Status in v11.22.0 |
|---|---|
| Per-Account Daily Statement Drift KPI bound to a measure incompatible with the formula | **RESOLVED**. Pool: Drift KPI $600 ties to formula gap $601 (±$1 rounding on a planted $600 drift). cust-equivalent leaf account: Drift $0 ties cleanly (with flipped-credits-as-magnitude). |
| Currency rendered at 1/100 of true magnitude (dollars-displayed-as-cents) across the deploy | **RESOLVED**. Values now at proper magnitude across KPIs, charts, detail tables. The biggest behavior change of the cycle. |
| Recipient Fanout total ~$1.5B with 4 qualifying recipients (cartesian inflation) | **RESOLVED** by BH.7 ("fix recipient fanout cartesian inflation"). Now plausible scale (~$58M / 6 recipients). |
| Drift Timelines y-axis labels mangled / chart axis inverted-looking | **RESOLVED**. Chart now reads cleanly. |
| Volume Anomalies KPI=0 vs companion distribution chart populated | **LIKELY RESOLVED** (none of 4 judges flagged this round; warm-pass confirm recommended). |

Still-present from prior rounds:

| Prior finding | Status in v11.22.0 |
|---|---|
| Daily Statement Business-Day picker non-functional (date filter ignored) | **STILL PRESENT** — confirmed via md5 byte-identical files. Suspected Phase AM Tailwind regression carried through. Not in scope of BH cascade. |
| Cross-app reconciliation gap: Total Transactions KPI vs matview row_count | **STILL PRESENT** — same shape, same ~21% gap. |
| L1 Drift "Latest Snapshot Drift = 0" hiding per-account drifts via SUM-cancellation | **STILL PRESENT**. |
| Executives stacked-by-rail_name chart legend dominance + outlier-bar Period-Total | **STILL PRESENT**. |
| Today's Exceptions one-bar dominance in breakdown chart | **STILL PRESENT**. |
| Investigation Money Trail / Account Network blank-by-default | **STILL PRESENT**. |
| L2 Exceptions distinct-types KPI vs only ~5 visible bars | **STILL PRESENT**. |
| App Info Matview Status panel lists only 2 matviews despite many in use | **STILL PRESENT**. |
| Deploy Stamp shows `dialect: sqlite (dev build)` | **STILL PRESENT** (dev-build affordance only; confirm-on-prod). |
| 3-decimal currency formatting on counts (e.g., `36,388.424` avg daily volume) | **STILL PRESENT** on counts; resolved on currency. |

## Findings

### Blockers (real dashboard defects)

#### 1. Per-Account Daily Statement Debits / Credits KPI labels don't disclose their sign convention

**Sheet**: `l1_dashboard / l1-sheet-daily-statement` with Role + Account picked.

**Symptom**: On a pool-class populated account: `Debits $-429,893.05`, `Credits $271,752.76`. On a leaf cardholder populated account: `Debits $127,633.62`, `Credits $-2,412,015.08`. The labels are plain "Debits" / "Credits"; the sheet narrative defines `Closing = Opening + Credits − Debits`. With the displayed signs, the formula doesn't tie.

The **math binds correctly** — Drift KPI = $600 on the pool reconciles to the formula gap (within $1 rounding on a planted $600 drift), and Drift = $0 on the cardholder ties if you treat the negative Credits as magnitude. The labels weren't updated to disclose the underlying sign convention.

**Hypothesis**: Same fix that landed the cents conversion likely changed the sign convention on the underlying measure (signed-flow rather than absolute magnitude) without updating the KPI tile labels.

**Verification**: Fix labels (e.g., "Debits (signed)" / "Net Debit (Dr−)") with a one-line key OR flip displayed signs to absolute magnitudes and let direction be carried by Drift / chart. This is a label-disclosure fix; the math is correct.

---

#### 2. Per-Account Daily Statement Business-Day picker remains non-functional (CARRYOVER)

**Sheet**: `l1_dashboard / l1-sheet-daily-statement`.

**Symptom**: Identical to the prior release. Two shots captured with different date params (`date_from=2026-05-07&date_to=2026-05-07` vs no date) are **byte-identical** (md5 confirmed by one judge). Detail rows' `Business Day` column shows latest-day data regardless of filter.

**Hypothesis**: Regressed during Phase AM Tailwind utility migration of data-page chrome (AM.2 step 4 / AM.4 deletion of `data.css`); date-input form binding lost a `name` attribute. The 2,797-test unit suite went green because tests use user-facing locators (`get_by_role`) that wouldn't catch a missing form-input `name`. **This finding was first surfaced in the previous release validation and not addressed by the BH cascade.**

**Verification**: diff `src/recon_gen/apps/l1_dashboard/` between the pre-AM-step-4 commit and HEAD for any change to the daily-statement date control's emitted HTML form attributes. Add a unit test that submits the form with a specific date and asserts the dataset query receives that date.

**Impact**: blocks proper per-day arithmetic verification.

---

#### 3. Daily Statement closes at suspicious $0.01 for one cardholder-class account; inputs don't tie by any sign convention

**Sheet**: `l1_dashboard / l1-sheet-daily-statement` populated with a specific leaf cardholder account.

**Symptom**: KPIs read approximately `Opening $-1,719,082.61`, `Debits $127,633.62`, `Credits $-2,412,015.08`, `Closing Stored $0.01`, `Drift $0.00`. The five-number identity doesn't reproduce $0.01 in any sign convention attempted.

**Two possible root causes** (needs warm-pass to disambiguate):
- (a) The sheet narrative promises "L2 control-account stubs that lack their own balance row are filtered out", and a stub may have leaked through the picker filter despite the promise. Real defect.
- (b) This account is a planted "overdraft training scenario" (the L2 instance has at least one other planted overdraft account: a different cardholder class running $-1,500/week explicitly named in the detail data as an overdraft acct). In which case the data is correct; the dashboard simply doesn't make planted-overdraft semantics legible to a trainee — that would be a training-UX defect rather than a math bug.

**Verification**: query `<prefix>_daily_balances` for this account-id on this business day; compare to the displayed Closing. If matview shows $0.01, the sheet is faithful. If matview shows a different value, sheet is broken.

---

### Blockers (training-UX defects)

#### 4. L1 Drift sheet "Latest Snapshot Drift = 0" KPI hides per-account fires via SUM-cancellation (CARRYOVER)

**Sheet**: `l1_dashboard / l1-sheet-drift` (KPI) vs `l1-sheet-drift-timelines` (chart) vs `l1-sheet-daily-statement` (per-account Drift).

**Symptom**: Headline KPI = 0 next to a populated detail table with non-zero drift rows; Drift Timelines shows persistent eight-figure parent drift across the window; per-account Daily Statement shows multi-thousand-dollar per-account Drift.

**Why this matters for training**: instructors plant per-account drift scenarios specifically so trainees learn to chase them. A signed-SUM KPI cancels positive/negative drifts to ~$0 and hides those scenarios at the headline. The trainee gets a green-light KPI and stops investigating.

**Verification**: Replace measure with `SUM(ABS(drift))` or `MAX(ABS(drift))`. Or label clearly as "Net snapshot drift (may cancel)" with a sibling "Largest absolute account drift" KPI.

---

#### 5. Daily Statement non-zero Drift rendered in same neutral type as zero Drift; no visual emphasis

**Sheet**: `l1_dashboard / l1-sheet-daily-statement` with a pool-class populated account.

**Symptom**: Pool account shows Drift = $600 (the planted drift). The sheet's own narrative says "non-zero drift here is the single visual cue the underlying ledger doesn't reconcile." But the $600 renders in the same grey/black type as $0 on adjacent accounts. No color, no warning glyph, no flag.

**Why this matters for training**: trainees scanning the daily-statement view need the planted drift to be visually distinct so they learn to recognize it. Without visual emphasis, the training plant is effectively invisible.

**Verification**: threshold-driven color/glyph on the Drift KPI tile when value ≠ 0; optionally weight by relative magnitude.

---

### Majors (training-UX defects)

#### 6. Today's Exceptions chart dominated by one tall bar; other planted scenarios invisible (CARRYOVER)

**Sheet**: `l1_dashboard / l1-sheet-todays-exceptions`.

**Symptom**: KPI = ~5,258 exceptions. "Exceptions by Type" chart shows one tall bar (~5,000) with all other check-types near-zero. The 5,000-bar is the planted `stuck_unbundled` scenario (verified via plant-coverage tests); the other planted check-types (chain_parent_disagreement, fan_in_disagreement, multi_xor_violation, ledger_drift, etc.) are invisible at this scale.

**Verification**: log scale, top-N + Other bucket, OR per-category counts labeled on each bar so trainees can see each scenario's volume.

---

#### 7. Executives daily-stacked-bar charts: ~60-80-entry rail_name legend dominates canvas; Period-Total has one tall bar + dozens of microscopic ones (CARRYOVER)

**Sheets**: `executives / exec-sheet-money-moved` + `exec-sheet-transaction-volume`.

**Symptom**: Legend takes ~30% of horizontal real estate; Period-Total bar charts have one dominant bar and ~80 effectively-invisible ones. Fails the 5-second executive skim.

**Verification**: top-N + "Other" bucket; legend below chart sorted by magnitude descending; log scale on Period-Total.

---

#### 8. Investigation → Money Trail + Account Network load BLANK by default (CARRYOVER)

**Sheets**: `investigation / inv-sheet-money-trail` + `inv-sheet-account-network`.

**Symptom**: No chain root / anchor preselected. Sankey panels grey/empty, tables "0–0 of 0". Reads as broken to first-time users. The AML reviewer surface.

**Pattern to copy**: `l2_flow_tracing / l2ft-sheet-transfer-templates` has explicit in-place banners ("no chains selected" / "no template matched") — this is the right pattern.

**Verification**: pre-select largest active chain by amount (or by recency); OR render an in-panel "Pick a chain root to begin" placeholder INSIDE the Sankey canvas.

---

#### 9. L2 Exceptions: distinct-types KPI vs only ~5 visible bars (CARRYOVER)

**Sheet**: `l2_flow_tracing / l2ft-sheet-l2-exceptions`.

**Symptom**: KPI says 41 distinct exception types but the breakdown chart shows ~5 readable bars. 36 types are at 1-pixel height or invisible. The long-tail of planted scenarios is hidden.

**Verification**: counts table next to chart, OR top-N + Other rollup.

---

#### 10. Drift Timelines: parent drift ($5.82M) vs leaf drift ($1,500) — 3,881× ratio with no scope explainer (NEW)

**Sheet**: `l1_dashboard / l1-sheet-drift-timelines`.

**Symptom**: If parent = sum of leaves (standard roll-up), max plausible parent drift is ~$21K given 14 accounts of $1,500 max-leaf-drift. The 3,881× ratio suggests parent drift is computed against a different baseline OR includes parent-level scenarios planted independently of leaf scenarios. The sheet doesn't tell the operator which.

**Verification**: annotate the sheet ("parent drift includes [scope X]; leaf drift includes [scope Y]") OR align the two scopes so the roll-up arithmetic is intuitive.

---

### Majors (real dashboard defects)

#### 11. Cross-app reconciliation gap: Total Transactions KPI = 2,403,163 vs matview `<prefix>_transactions.row_count` = 3,032,345 (CARRYOVER)

**Sheets**: `executives / exec-sheet-transaction-volume` vs `executives / exec-sheet-app-info` (or any app's app-info).

**Symptom**: 629,182-row gap (~21% of the matview). Both reading "All dates" on the same matview the App Info panel literally names. No on-page reconciliation explainer.

**Verification**: surface the WHERE clause behind Total Transactions; align with `COUNT(*)` OR annotate the sheet with the predicate.

---

#### 12. L1 Limit Breach KPI = 0 with 5 visible rows in detail table (NEW vs previous release)

**Sheet**: `l1_dashboard / l1-sheet-limit-breach`.

**Symptom**: KPI says `Limit Breach Cells = 0` but the detail table immediately below shows 5 populated rows (`cust-011` through `cust-015`, `ACHCardholderInternalDebit`). Previous release had this sheet as the textbook clean-honest-zero example (KPI=0 + empty table). v11.22.0 introduces the contradiction.

**Hypothesis**: either (a) BH cascade introduced new plants in a limit_breach-adjacent shape that the KPI's scope doesn't count, (b) a measure binding change, or (c) the 5 rows are the deliberate plant-coverage `_spine_plant` or related plants being surfaced here.

**Verification**: diff Limit Breach dataset SQL between v11.21.0 and v11.22.0; confirm KPI scope vs detail-table scope.

---

#### 13. L2 Flow Tracing Chains explorer: "Required Total = 1 (or 0)" for nearly every chain instance (NEW)

**Sheet**: `l2_flow_tracing / l2ft-sheet-chains`.

**Symptom**: Multi-leg chains (auth + post, debit + credit) should have Required Total ≥ 2. Most rows show 1 (or 0). Some show "Completed" / "Incomplete" status with Required Total = 1 — operator can't tell what "Incomplete" means without knowing what's required.

**Verification**: either the column is mis-defined (counts only parent leg vs all required legs) or chains with Required Total = 0 are inert and shouldn't appear in this explorer.

---

#### 14. L1 Overdraft sheet titled "Internal Accounts in Overdraft" but detail table is dominated by customer-class rows (CARRYOVER)

**Sheet**: `l1_dashboard / l1-sheet-overdraft`.

**Symptom**: KPI label says "Internal Accounts" (suggesting pool/sweep accounts). Visible detail rows include leaf-cardholder-class accounts (`cust-*`). Label/scope mismatch.

**Verification**: either expand the label ("Internal + Cardholder Accounts in Overdraft") or filter the table to actual internal accounts only.

---

#### 15. Account Coverage: `cust-019` row shows `Account Name = Customer 18` (off-by-one in seed/ETL naming) (NEW)

**Sheet**: `executives / exec-sheet-account-coverage`.

**Symptom**: Account-id `cust-019` carries the display name "Customer 18". Either intentional (closed/reused account ids leaving gaps in name numbering) or an off-by-one in the seed.

**Verification**: single-row check against the source seed config.

---

#### 16. Executives Net Money Moved = -$30.8M with no sign-aware visual treatment (CARRYOVER, magnitude now correct)

**Sheet**: `executives / exec-sheet-money-moved`.

**Symptom**: Net = -$30,853,598.22 vs Gross = $265,178,546.13 (-11.6% net drain). At the previous release this displayed as -$308K under the cents-as-dollars bug; now at proper magnitude it's much more alarming visually. Sheet copy says "flows into the bank are positive" but the negative number has no color treatment, no warning, no inline legend.

**Verification**: sign-aware color (green positive / red negative) on the Net KPI tile + a one-line legend "negative = net outflow to external counterparties this period". Alternatively confirm the sign convention is correct (could be a sign error in the netting itself).

---

### Polish

#### 17. KPI dupes: Total Open Accounts 14 + Active Accounts 14 (identical numbers in adjacent KPI cards)

Could be legitimate (every open account is active) but reads as same-field-bound-twice. Skeptic-judge's suggestion: surface `Open − Active = 0` explicitly so the operator can see WHY they match.

---

#### 18. 3-decimal formatting on counts: Average Daily Volume `36,388.424`

Counts averaged over N days are mathematically fractional but `.424` on a count looks like a leaked currency formatter. Round to integer or 1 dp.

---

#### 19. Editorial fragments leak into Executives help text

Strings like `"Note ch1.2.2 ..."`, `"*Note Jul 21.."`, and a typo-ish "every customer no external transfer is offset by..." appear in user-facing copy on flagship exec sheets.

---

#### 20. Empty-state discipline on picker-required sheets (CARRYOVER)

Daily Statement default, Money Trail default, Account Network default — five blank KPI cards or blank Sankey reads as broken. `l2ft-sheet-transfer-templates` has the right pattern (in-place banners) for others to copy.

---

#### 21. App Info Matview Status panel only lists 2 matviews despite many in use (CARRYOVER)

Operators reading "is everything fresh?" would assume there are only 2 matviews to monitor.

---

#### 22. Deploy Stamp shows `dialect: sqlite (dev build)` (CARRYOVER)

Fine for dev; needs prod-stamp toggle before any external-facing surface.

---

#### 23. Default exception volume is overwhelming for a first-day trainee (Studio-tunable, not a defect)

5,258 today's exceptions + 163 overdrafts + 41 distinct L2 exception types is a lot of plants firing at default density. Per the user's clarification, this is configurable via the Studio trainer surface — note as a default-tuning observation only.

---

## Plant-coverage finding from the test suite

Run of `phase2_coverage_tests.py --skip-seed` against the same DB:

- **Same 8/11 L1 invariants surface real violations** as prior release. The 2 FAILs (`limit_breach`, `expected_eod_balance_breach`) and the SKIP (`xor_group_violation`) are attributable to known F-findings (F1, F18, F19 — auto-scenario plant-picker gaps for template-as-chain-parent + 1-leg LimitSchedules). Pre-existing.
- **Same 3 unmatched `rail_name` values** as prior release, with one note: the previously-flagged 3-char `ach` string was always a deliberate purposeful plant (validates the L2FT-unmatched-rail-name surface); v11.22.0 renamed it to `_spine_plant` with a leading-underscore convention to make the by-design intent explicit. The other two (`MerchantSettlementCycleVoucher`, `VoucherBatch`) are still the known template-as-chain-parent F1/F18 unmatched names. **No NEW plant-coverage findings.**
- **Same chain-orphan rates** (2,705 total across 8 singleton-Required chains) — byte-identical to prior release. Confirms BH cascade didn't touch plant-coverage paths.

## Capture requests (for warm-pass support)

1. The dataset SQL behind Daily Statement Debits / Credits KPIs — confirm sign convention.
2. Raw SQL behind L1 Drift "Latest Snapshot Drift" measure — confirm `SUM(signed)` vs `SUM(ABS)`.
3. Daily Statement reshoot on 3 different business days for the same populated account AFTER #2 (date-filter regression) is diagnosed.
4. Raw query against `<prefix>_daily_balances` for the cardholder account with the suspicious $0.01 closing — disambiguate stub-leak from planted-overdraft scenario.
5. The dataset SQL behind Executives Total Transactions vs `COUNT(*) FROM <prefix>_transactions` — explain the 629,182-row gap.
6. The dataset SQL behind Limit Breach KPI vs detail table — explain the 0 vs 5 contradiction.
7. Drift Timelines parent vs leaf series scope definitions — explain the 3,881× ratio.
8. Investigation Anomalies KPI vs distribution chart re-confirm — verify the previously-flagged contradiction is genuinely resolved (no judge flagged it this round).

## Methodology note

Each of the 4 judge agents ran with strict no-other-docs guardrails — no project memory, no SPEC/PLAN/test files, no upstream code. Their only input was screenshot PNGs. The 4 lenses (baseline / reconciliation-pragmatist / skeptical-QA / executive-oversight) were prompt-framed to read the same captures with different priorities. Cross-judge consensus (3+ of 4 independently flagging) is the bar for promotion to blocker-major. The training-plant context was applied in CONSOLIDATION only (the judges read the dashboards as cold operators without training-plant knowledge — preserves the unbiased screenshot read).
