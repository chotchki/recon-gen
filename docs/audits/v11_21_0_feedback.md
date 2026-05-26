# recon-gen v11.21.0 — local-stack dashboard validation findings

Findings from a 4-judge cold-read against a non-trivial L2 instance running on the local `recon-gen dashboards` HTMX/d3 stack. Scrubbed of deployment-specifics; structured as actionable bug reports for upstream triage.

## Reproduction harness

- **Build**: `recon-gen v11.21.0` (git `158e8a6`)
- **Backend**: sqlite dialect, single-file DB
- **Stack**: `recon-gen dashboards --l2 <instance.yaml> -c <config.sqlite.yaml> --app all --no-docs`
- **L2 instance shape**: multi-pool, multi-cardholder-class, ~85 declared rails, several singleton-required chains, several multi-children XOR chains, several template-as-chain-parent chains, several fan-in chains (template-parent), inbound + outbound LimitSchedule entries on single-leg rails, escrow accounts with `expected_eod_balance: 0`
- **Seed**: 90-day baseline at default density (`recon-gen data apply --execute` then `data refresh --execute`); ~3.0M transactions, ~1.2K daily_balances
- **Method**: 4 context-isolated cold-judge passes against 40 full-page sheet captures (12 L1 + 6 L2FT + 6 Investigation + 5 Executives + ~10 round-1 parametrized shots), independently ranked, then cross-judge consolidated

## What's resolved vs. the previous release-validation cut

| Prior finding | Status in v11.21.0 |
|---|---|
| Daily Statement parameter pickers don't populate (CLI-serve path missed wiring) | RESOLVED |
| Demo-bank placeholder string leaked onto an Investigation landing sheet | RESOLVED (no placeholder strings spotted) |
| `stacked by rail_name` bar charts not actually stacked | RESOLVED (charts now render colored stacks; new presentation issues replace it — see §10) |

## Findings

### Blockers

#### 1. Per-Account Daily Statement Drift KPI is bound to a measure incompatible with the formula the sheet's own narrative defines

**Sheet**: `l1_dashboard / l1-sheet-daily-statement` with both a Role and an Account picked. **Reproducer params**: any populated account (a pool-class account; a leaf cardholder-class account — same defect on both).

**Symptom**: The sheet displays a five-number summary `Opening / Debits / Credits / Closing Stored / Drift` with a narrative defining `Drift = Closing Stored − (Opening + signed_net_flow)`. Reproduction on two account-classes:

| Account class | Opening | Debits | Credits | Closing Stored | Formula gap | Drift KPI shown | Mismatch |
|---|---|---|---|---|---|---|---|
| pool | 14,869.348 | -4,298.921 | 2,717.527 | 13,293.954 | +6.000 | **-8,091.841** | KPI ≠ formula gap |
| leaf cardholder | -13,648.671 | -1,745.823 | 1,279.524 | -24,120.18 (clipped) | -10,005.21 | **-3,491.836** | KPI ≠ formula gap |

Three different "how far off are we" numbers visible on the same panel: implied gap, printed Drift, and the difference between formula interpretations. Two of two populated reproductions fail the same arithmetic check.

**Hypothesis**: The Drift card is bound to a different measure than the sheet narrative claims — possibly a stored-drift column from a matview accumulating across a window that disagrees with the picker scope. Likely interacts with #2.

**Verification**: grep the L1 dataset SQL for the `Drift` KPI measure binding. Compare its WHERE/GROUP-BY against the binding for `Closing Stored` on the same panel. Surface the actual SQL the visual is sending so the formula↔visual discrepancy is unambiguous.

---

#### 2. Per-Account Daily Statement Business-Day picker is non-functional

**Sheet**: `l1_dashboard / l1-sheet-daily-statement`. **Reproducer**: pick a Role + Account, then change the Business-Day filter to any specific date.

**Symptom**: KPIs and the Posted Money Records detail table render **byte-identical** results regardless of which Business Day is picked. Confirmed by md5-comparing screenshots taken with `?date_from=2026-05-07&date_to=2026-05-07` vs no date param — identical files. The detail rows' `Business Day` column shows the latest-day date in both shots; the picker's value never reaches the dataset query.

**Hypothesis**: Regressed during the **Phase AM Tailwind utility migration** (release notes: AM.2 step 4 migrated data-page chrome to raw Tailwind utilities; AM.4 deleted `data.css` 554 LOC; trainer-strip + timeline-day buttons rebound to `data-role` / `data-state` attribute hooks). If a date-input's `name` attribute was reshuffled or its form-binding moved during the migration, the dataset would default to its all-data behavior. The 2,797-test unit suite went green because tests follow user-facing locators (`get_by_role`, `get_by_text`) that wouldn't trip on a missing form-input `name`.

**Verification**: diff `src/recon_gen/apps/l1_dashboard/` between the pre-AM-step-4 commit and v11.21.0 for any change to the daily-statement date control's emitted HTML form attributes. Add a unit test that submits the form with a specific date and asserts the dataset query receives that date.

**Impact**: This is the most foundational defect in the release. Findings #1, #3, #4, #6 below partially depend on it — the per-day arithmetic checks are unreliable until the picker is wired through. Once fixed, expect those findings to change shape or partially resolve.

---

#### 3. Per-Account Daily Statement shows NEGATIVE Opening Balance for a class-restricted (closed-loop / leaf-cardholder) role

**Sheet**: `l1_dashboard / l1-sheet-daily-statement` with Role = a single-direction-restricted cardholder class, Account = any specific cardholder account.

**Symptom**: Opening Balance KPI = `-13,648.671` on a leaf cardholder account class whose semantic is "prepaid stored value held on behalf of an individual." A negative stored-value position on a closed-loop cardholder is structurally impossible under the sheet's own narrative: "L2 control-account stubs that lack their own balance row are filtered out." Either: (a) the sign convention on the Opening measure is inverted for this account-class; (b) a control-account stub is leaking past the filter despite the narrative; (c) a wrong account class is bound through to the picker.

**Verification**: query the underlying `daily_balances` matview for this account-id on this business day; check the sign convention vs. the sheet's KPI binding. If the matview holds a positive value but the sheet inverts it, the bug is in the dataset SQL. If the matview itself is negative, the bug is upstream in the balance reconstruction.

---

#### 4. L1 Drift sheet "Latest Snapshot Drift" KPI hides per-account drift via SUM-cancellation

**Sheet**: `l1_dashboard / l1-sheet-drift` (KPI) vs `l1-sheet-drift-timelines` (chart) vs `l1-sheet-daily-statement` (per-account Drift).

**Symptom**: Three views of the same underlying state disagree:
- Drift sheet headline: `Latest Snapshot Drift = 0` (suggests healthy)
- Drift Timelines: `Largest Parent Drift Day = 58,221.979`; Parent Account Drift Over Time line oscillates between -$50K and +$60K every single business day across the 85-day window
- Per-Account Daily Statement on a pool account: Drift = -$8,091.841 on the latest snapshot

**Hypothesis**: "Latest Snapshot Drift" is `SUM(signed_drift)` across all accounts on the snapshot date, which cancels to ~0 because per-account positive and negative drifts net out. Per the sheet's tagline ("the single visual cue the underlying ledger doesn't reconcile"), this KPI should be `SUM(ABS(drift))` or `MAX(ABS(drift))`, not the signed sum.

**Verification**: read the KPI's underlying measure expression. Replace with `SUM(ABS(drift))` and confirm the KPI matches the per-account-largest figure shown elsewhere.

---

#### 5. Investigation → Volume Anomalies headline KPI = 0 flagged windows, while the companion distribution chart and detail table both clearly show populated tail mass

**Sheet**: `investigation / inv-sheet-anomalies`.

**Symptom**: The headline KPI claims zero flagged pair-windows, but the σ-distribution chart on the same sheet shows populated 2σ-3σ, 3σ-4σ, and 4σ+ bins, and the detail table is full of sender→recipient pair rows. Either the threshold for "flagged" is mis-tuned (effectively unreachable), or the KPI query and the chart query are computing different populations.

**Carryover**: This finding was present in the previous release-validation cut and remains unresolved.

**Verification**: surface the WHERE/HAVING clause behind "Flagged Pair-Windows" KPI and compare it against the σ-bucket aggregation in the distribution chart. If they're computing different things, document or unify; if the threshold is unreachable, retune or remove.

**Impact**: this is the AML triage surface — operators / reviewers reading this lose confidence in the entire investigation app from this contradiction alone.

---

#### 6. L1 Drift Timelines "Leaf Account Drift Over Time" is a perfectly flat constant ($15.00) line across the entire 30+ day window

**Sheet**: `l1_dashboard / l1-sheet-drift-timelines`.

**Symptom**: The Leaf series renders as a dead-flat horizontal line at $15.00 across the entire visible time range. KPI "Largest Leaf Drift Day = 15" is consistent with a constant line. A leaf series that's literally the same value every day on a non-trivial seed is statistically near-impossible — strongly suggests the series is bound to a constant, a single-account series, or a stale snapshot.

**Verification**: query the underlying matview for `(business_day, account_id, drift)` filtered to leaf accounts across the window. If the matview shows real per-day variation, the chart binding is wrong (likely a stuck WHERE clause or wrong join key).

---

### Majors

#### 7. Investigation → Recipient Fanout total amount is implausibly large; "Qualifying Recipients" count contradicts its likely threshold

**Sheet**: `investigation / inv-sheet-fanout`.

**Symptom**: Total inbound = $1.54B across 11 distinct senders feeding 4 qualifying recipients. The bank-wide gross handle reported on the Executives sheet for the same window is $2.65B — so the Recipient Fanout subset is 58% of the entire deploy's gross, concentrated in 4 recipients. Mathematically improbable: 11 distinct senders divided across 4 qualifying recipients = 2.75 senders/recipient average. If the "qualifying" threshold is the conventional ≥5 distinct senders, at most `floor(11/5)=2` recipients can simultaneously qualify; 4 qualifying implies a threshold of ≤2 (which makes the alert meaningless).

**Hypothesis**: SUM expression on the Fanout dataset is likely double-counting (summing both legs of each transfer, or summing across chain children). Threshold opacity is a separate but adjacent issue — the sheet doesn't tell operators what threshold defines "qualifying."

**Verification**: surface the SUM expression on `inv_fanout_*` dataset; check whether it's `SUM(amount_money)` across all matching legs or properly deduplicated to one side. Add the threshold to on-sheet narrative copy.

---

#### 8. Executives "Total Transactions" KPI = 2,403,163 vs App Info matview row_count = 3,032,345 on the same matview with both reading "All dates"

**Sheet**: `executives / exec-sheet-transaction-volume` vs `executives / exec-sheet-app-info` (or any app's app-info — they all agree on the matview row count).

**Symptom**: 629,182-row gap (≈21% of the matview). Both KPIs claim to read the same `<prefix>_transactions` matview the App Info panel literally names. No on-page reconciliation explainer.

**Carryover**: this finding shape was present in the previous release-validation cut; same gap, scaled to the new seed size.

**Verification**: surface the WHERE clause behind Total Transactions vs `SELECT COUNT(*) FROM <prefix>_transactions`. Likely candidates: failed-status filter, internal-leg filter, chain-child filter. Either match the count or annotate the sheet with the predicate ("Total Transactions excludes failed and chain-child rows").

---

#### 9. Today's Exceptions: KPI dominated by a single check-type bar; other categories visually flat

**Sheet**: `l1_dashboard / l1-sheet-todays-exceptions`.

**Symptom**: KPI shows ~5K-6K exceptions; the "Exceptions by Type" breakdown chart shows ONE bar at the KPI's height and all other check-type bars near-zero. Detail-table rightmost columns (Status / Origin) mostly empty.

**Disambiguation from plant-coverage testing**: this is a **real ETL signal** (the `stuck_unbundled` matview has ~5K rows on this seed at the post-Phase-2 firing densities — not a SQL double-emit). The dashboard rendering needs help to surface the other check-type bars when one dominates.

**Verification**: confirm via `SELECT check_type, COUNT(*) FROM <prefix>_todays_exceptions GROUP BY check_type`. If `stuck_unbundled` dominates legitimately, the chart needs either log-scale, a top-N + Other bucket, or a "click to drill into <check_type>" affordance.

---

#### 10. Executives daily-stacked-bar charts: ~60-80-entry rail_name legends dominate the canvas; Period-Total bar charts have one or two outlier bars dwarfing the rest

**Sheets**: `executives / exec-sheet-money-moved` + `exec-sheet-transaction-volume`.

**Symptom**: The previous-release "stacked-by-rail_name charts aren't actually stacked" issue is fixed — the charts DO render colored stacks. But the rail_name legend now spans the entire vertical height of the page next to a small bar chart, and the Period-Total bars have one or two dominant rails with dozens of near-invisible others. Fails any "5-second executive skim" criterion.

**Verification / fix candidates**: (a) top-N + "Other" bucket; (b) move legend to a scrollable side panel; (c) log-scale Period-Total bar charts; (d) operator-selectable rail-family grouping.

---

#### 11. L2 Exceptions KPI labeled "Open L2 Violations = 41" but detail-table Count column shows values in the thousands — two different units, same page, no signposting

**Sheet**: `l2_flow_tracing / l2ft-sheet-l2-exceptions`.

**Symptom**: KPI counts distinct check types (or open-only); table Count column counts violations-per-row (or includes resolved). Operators reading "41" will not expect a table with rows-each-numbering-in-the-thousands.

**Verification**: align units or add inline explainer ("KPI = distinct check types currently open; Count column = magnitude per row").

---

#### 12. L1 Drift sheet "Internal Accounts in Overdraft" KPI = 0, with the Overdraft Detail table directly below the KPI fully populated

**Sheet**: `l1_dashboard / l1-sheet-drift`.

**Symptom**: KPI reads 0 while a detail table on the same sheet is clearly populated with overdraft rows. Either the KPI is scope-mismatched against the table (counts a different account-class or a different time slice), or there's a measure binding bug.

**Verification**: KPI measure expression vs. detail table dataset query — confirm same scope.

---

#### 13. Pending Aging KPI=2 / detail-table=2 / chart bucket=~140 — chart bar height disagrees with both KPI and detail row count

**Sheet**: `l1_dashboard / l1-sheet-pending-aging`.

**Symptom**: KPI and footer-pagination tie cleanly (both = 2). But the 0-2h bucket bar rises to roughly the 140 y-tick. Either the chart is plotting a different (cumulative or unfiltered) population, or its y-axis is mislabeled.

---

### Polish

#### 14. 3-decimal currency formatting used inconsistently across the deploy

**Sheets**: Money Moved, Transaction Volume, Drift Timelines, Daily Statement.

**Symptom**: Values like `308,535.982`, `2,651,785.461`, `58,221.979`, `36,388.424` use three-decimal precision on currency totals. Mixed with `13,293,854` (no decimal) and `-24,120.18` (2 decimal) on the SAME Daily Statement. The 3-decimal format also creates real misread risk — two of four cold judges misread `-308,535.982` as `-$308M` (vs the correct -$308,535.98) because the period vs. comma was ambiguous at the rendering scale.

**Verification**: standardize on 2-decimal currency formatting across all `$`-prefixed KPIs.

---

#### 15. Empty-state discipline gap on picker-required sheets

**Sheets**: Daily Statement default; Money Trail default; Account Network default; Recipient Fanout default.

**Symptom**: When a required picker isn't yet set, sheets render five blank KPI cards or a blank Sankey canvas or a "0–0 of 0" table, with no inline "pick a parameter to begin" hint. Reads as "broken" to first-time users.

**Pattern to copy**: the `l2_flow_tracing / l2ft-sheet-transfer-templates` sheet has explicit in-place banners ("no chains selected" / "no template matched") — exec judge noted this as the right pattern for the other picker-required sheets to copy.

---

#### 16. L1 getting-started copy: "CELIBERATE" typo + raw seed-config leaks into user-facing intro + unattributed "Part I / Part II" references

**Sheet**: `l1_dashboard / l1-sheet-getting-started`.

**Symptom**: Typo "CELIBERATE cut down" (intended: "DELIBERATELY"). Same paragraph leaks raw seed-config tokens like "90-day window, 1.5 ledgers/cardholder, 4 per location" into prose. References "Source: Part I, Part II" with no link target or expansion.

---

#### 17. Internal version-note string leaks into Executives help text

**Sheets**: `executives / exec-sheet-money-moved` + `exec-sheet-transaction-volume`.

**Symptom**: Help-text content contains a string of the shape `"Note (vL1.5.21*) apparent multi-week stretches + weekend gaps reflect the bundled demo's short seed window (90 days)..."`. The `(vL1.5.21*)` token is developer-internal build-note language showing up in user-facing copy on two flagship exec sheets.

---

#### 18. App Info Matview Status panel only lists 2 matviews (`daily_balances`, `transactions`) while the deploy clearly relies on many more

**Sheet**: `*-app-info` on all 4 apps (consistent).

**Symptom**: The panel reads "1–2 of 2" with only the two foundational matviews. Other matviews — drift, exceptions, fanout, supersession, etc. — are referenced elsewhere in the dashboards but absent from this status panel. Operators reading "is everything fresh?" would assume there are only 2 matviews to monitor.

**Verification**: confirm whether the panel is filtering to a specific matview-family, paginated past visible window, or genuinely only registering two matviews for freshness. Surface all matviews or label the panel narrower scope.

---

#### 19. Daily Statement KPI tile text-clipping

**Sheet**: `l1_dashboard / l1-sheet-daily-statement`.

**Symptom**: A KPI tile reads `-24,120,18` with the final digit clipped by the tile width. Should size-to-content or apply abbreviation (`-$24.1M`) before clipping.

---

#### 20. Unbundled Aging KPI label rendering: "Stuck Unbundled = $ Exposure 490,826"

**Sheet**: `l1_dashboard / l1-sheet-unbundled-aging`.

**Symptom**: The `$` glyph appears to be concatenated into the label string rather than applied as a number-format prefix to the value. Reads "Stuck Unbundled = $ Exposure 490,826" rather than "Stuck Unbundled Exposure: $490,826" or similar.

---

#### 21. App Info Deploy Stamp shows `dialect: sqlite` — fine for dev capture, surface a "dev build" affordance for prod

**Carryover**: this was a minor in the previous cut and stands; worth a "dev build" disclaimer or hiding the dialect token in production-style serves.

---

## Plant-coverage finding (separate signal worth tracing)

Run of `phase2_coverage_tests.py` against the same seeded DB surfaced 3 unmatched `rail_name` values appearing in transactions that don't match any declared Rail:

- `MerchantSettlementCycleVoucher` — known: a Template name (template-as-chain-parent F1/F18 pattern).
- `VoucherBatch` — known: a Template name (same pattern).
- **`ach`** — **NOT a known template or rail name**; 3 characters; doesn't match any chain-parent pattern. Smells like a string-slicing bug or hard-coded default in some seed-emitter path leaking a partial name into transaction `rail_name`. Worth grepping `src/recon_gen/common/l2/*.py` for any literal `"ach"` or string-slicing on rail name fields.

## Capture requests (warm-pass support)

If upstream wants to ground-truth findings before patching, the most useful raw queries:

1. The exact dataset SQL behind the Daily Statement Drift KPI (one query per panel).
2. The exact dataset SQL behind Executives Total Transactions vs `COUNT(*) FROM <prefix>_transactions`.
3. The exact dataset SQL behind Recipient Fanout Total Amount (looking for double-counted legs).
4. The exact dataset SQL behind L1 Drift "Latest Snapshot Drift" (confirm `SUM(signed)` vs `SUM(ABS)`).
5. The exact dataset SQL behind L1 Drift Timelines Leaf series (looking for stuck WHERE / wrong join).
6. Three Daily Statement re-shoots on three different business days for the same populated account, AFTER the date-filter regression (#2) is diagnosed — needed to re-judge #1 + #4 against per-day data.

## Methodology note on cold-judge protocol (for the curious)

Each of the 4 judge agents ran with strict no-other-docs guardrails — no project memory, no SPEC/PLAN/test files, no upstream code. Their only input was the 40 screenshot PNGs. The 4 lenses (baseline / reconciliation-pragmatist / skeptical-QA / executive-oversight) were prompt-framed to read the same captures with different priorities. Cross-judge consensus (3+ of 4 independently flagging a finding) was the bar for promotion to a top-ranked blocker; single-judge findings are listed as polish or as separate-judgment items.
