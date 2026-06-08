# Phase 3 — v11.23.0 cold-sweep — UPSTREAM-ready findings

Three context-isolated cold-read judges (skeptic / reconciliation pragmatist / executive-oversight) against the v11.23.0 dashboard bundle generated against a sample L2 instance. Each judge worked from screenshots only — no SPEC, no PLAN, no prior reviews, no L2 YAML — and produced an independent ranked list of findings. This document is the consolidated, scrubbed-for-upstream view: dashboard / SPEC defects only, generic identifiers throughout, with no instance-specific seed data or downstream-integrator concerns.

## Key context: dashboard path is the production surface

In real-world deployments, the dashboard is the operator's only surface. The matviews and datasets backing it can be perfectly correct and the operator will still make wrong calls if the picker FK is wrong, the Sankey is blank, or the KPI is mislabeled. Every finding below is the kind a real operator hits *before* they ever look at the underlying data.

## Headline: Phase BM works where it landed, and surfaces a pre-existing picker bug as the new top blocker

**The positive:** the reconciliation-pragmatist judge independently confirmed Per-Account Daily Statement reconciles to the penny when populated — `Opening + Credits − Debits = Closing`, Drift `$0.00`, full detail-row table. Pre-BM (v11.22.0) the picker didn't narrow at all so the math couldn't be trusted; in v11.23.0 the unified `<<$pXxxDateStart/End>>` pushdown is doing its job and the headline reconciliation works. **v11.22.0 UPSTREAM finding #2 ("Per-Account Daily Statement Business-Day picker remains non-functional") closes.**

**The negative:** BM making the picker strict exposed a separate **account-picker FK mismatch** that was masked when the picker was a no-op. The Daily Statement's account dropdown offers IDs at one granularity (a customer/owner-rollup ID) while the role-filtered balance tables are keyed at a finer granularity (per-instance per-role IDs). A first-day user picking the first option in the dropdown gets five blank KPI cards and a zero-row table — and concludes the dashboard is dead. This is the new #1 blocker (Finding 1).

Several Sankey-rendering bugs are also new this round (charts blank while sibling tables have data), and the Drift / Drift Timelines metric disagreement from v11.22.0 sharpens into a quantified ~$415K gap on parent drift plus a sign flip on leaf drift between sister sheets.

## Findings

### Blockers (real dashboard defects)

#### 1. L1 Per-Account Daily Statement account-picker exposes rolled-up account IDs that don't intersect with `<prefix>_daily_balances` for the picked role (NEW, exposed by Phase BM)

Three independent captures with role selected, account selected from the dropdown, and a specific business day pinned all return five blank KPI cards (Opening / Debits / Credits / Closing / Drift) and `0–0 of 0` posted-money rows. Yet the same accounts are clearly active elsewhere: Account Coverage shows hundreds of thousands of postings, Overdraft pins specific instances at multi-million negative balances on the same dates, and Money Trail walks through transfers involving them.

Root cause hypothesis: the account-picker SQL feeding `param_pL1DsAccount` lists *owner-rollup* IDs (one per customer / counterparty) while `<prefix>_daily_balances` is keyed by *per-instance per-role* IDs (one per (role, instance) combination). Selecting a rollup ID returns zero rows because no `daily_balances` row has that ID as `account_id`. The Daily Statement panel's own subtitle claims the picker "lists accounts with stored daily balances only (L2 control-account stubs that lack their own balance row are filtered out)" — that filter is provably not running for rollup IDs.

Phase BM did not *introduce* this bug; it *exposed* it. Pre-BM the picker filter was a no-op so every selection showed all data, masking the FK mismatch. Post-BM the picker actually filters → cardholder/owner-rollup picks return correctly zero rows.

**Proposed fix:** scope the `param_pL1DsAccount` picker SQL to `SELECT DISTINCT account_id FROM <prefix>_daily_balances WHERE account_role = <picked_role>` (or the equivalent existing CTE), not from any rollup-level source. Reproducible in any deployment whose `account_templates` define instance IDs at finer granularity than the picker's source.

#### 2. Investigation → Account Network: both Inbound and Outbound Sankey panels render blank while the Touching Edges table below has rows for the same anchor (NEW)

With a leaf-account anchor selected and `min_hop_amount = 0`, both Sankey canvases ("Counterparties → anchor" and "anchor → counterparties") render as empty grey rectangles. The Touching Edges table immediately below populates with 18+ rows of (source → target, hop_amount, posted_at, rail_name) tuples for the same anchor. The Sankey is the dashboard's primary visual — rendering empty while the row evidence exists is a primary-use-case failure for the AML/investigation workflow this sheet is sold as.

Either the data shape feeding the chart differs from the data shape feeding the table (likely — Sankey may need an aggregation the table doesn't), or the Sankey component is silently failing on this dataset shape. Worth a load-trace.

#### 3. L2 Flow Tracing → Transfer Templates: Multi-Leg Flow Sankey renders blank while the Template Instances table shows 25+ Imbalanced rows (NEW)

The Multi-Leg Flow ("Account → Template → Account") panel is an empty white card on default load. The Template Instances table beneath it has plenty of data including templates flagged `Imbalanced` with `Expected_Net` vs `Computed_Net` gaps in the single-digit-dollars range. No "pick a template" affordance on the empty panel; no explicit empty-state copy.

Either render aggregated when no template is picked (showing all templates' flows in one diagram), or add an explicit "Pick a template instance to see its leg flow" empty-state. Same shape as Finding 2 (Sankey wired to a filter that doesn't fire on default load).

#### 4. L1 Drift sheet and Drift Timelines sheet disagree on the largest parent-drift dollar magnitude over the same window — ~$415K gap; leaf drift also sign-flips between the two (NEW quantification, same family as v11.22.0 #10)

Drift sheet: `Largest Parent Drift` shows the larger dollar value (signed leaf KPI). Drift Timelines (same window): `Largest Parent Drift Day` shows a smaller dollar value (unsigned leaf KPI). The two values differ by ~1.5% — plausibly "peak across the window" vs "peak single business day" — but the labels do not disambiguate. Operators clicking between the sheets will assume one is stale.

The sign-convention flip on the leaf KPI (signed on Drift, unsigned on Drift Timelines) compounds the confusion — same underlying datum, different presentation.

**Proposed fix:** label disambiguation (e.g., "Largest Parent Drift (anywhere in window)" vs "Largest Parent Drift Day (peak single business day)") AND signed/unsigned convention consistency across sister sheets.

### Blockers (training-UX defects)

#### 5. Per-Account Daily Statement landing experience has five quiet-empty KPI cards with no orientation copy (CARRYOVER, sharpens v11.22.0 #20)

Distinct from Finding 1 (the FK mismatch). Even when the picker works correctly, the *landing* state with no selection is five neatly-labeled blank tiles plus a `0–0 of 0` table. A trainee operator reads "Opening Balance: (blank)" as "the system is broken" or "this account has zero balance." Compounded by the FK mismatch in Finding 1, this is the single sheet most likely to generate "is the dashboard broken?" tickets on day one of a trainee rollout.

**Proposed fix:** in-card "Pick an account and a day to populate" empty-state copy on each KPI tile, or a single colored alert above the KPI row when no account is selected. Same shape on Investigation → Money Trail and Account Network (both also land bone-empty without a chain root / anchor picked).

#### 6. Executives → Money Moved leads with a large negative Net KPI in saturated red against the Gross KPI in neutral blue, with no sign-convention legend (CARRYOVER from v11.22.0 #16)

The Net KPI is `Gross_signed_sum_of_flows`; the subtitle explains "flows into the bank are positive; ACH out / cash out / POS out negative; expected near zero on a balanced book." But on a glance test, the red minus on a bank-reconciliation dashboard reads as alarm before the explainer is read. An executive quickly skimming will ask "are we hemorrhaging $X?" before they read the per-rail context (which currently isn't shown next to the KPI).

**Proposed fix:** neutralize the color treatment on Net KPI (no red/green on a signed sum where negative is normal); split the KPI into "Cash In / Cash Out / Net" with a directional legend; add per-rail attribution table or stacked bar below the KPI row so the operator can see *which* rail family is driving the net.

### Majors

#### 7. App Info "Matview Status" panel is byte-identical across L1, Executives, Investigation, and L2 Flow Tracing — all four show the same 2 base tables despite the panel's own subtitle claiming per-app scope with ~12 matviews on L1 (CARRYOVER from v11.22.0 #21)

The panel subtitle says the per-app scope is intentional ("Executives reads only 2 base tables; L1 reads ~12 matviews"), but the panel renders the Executives-only scope (2 base tables: `<prefix>_daily_balances`, `<prefix>_transactions`) on every dashboard. An operator trying to verify matview freshness before close gets a 2-row sample from a 26-matview deploy and cannot tell whether the matviews they care about have refreshed.

Note the *concept* of per-app diagnostic discipline is praised by every cold-read pass — the fix is "make it actually per-app," not "remove it." Likely a regression when the panel was extracted into shared components.

#### 8. The word "Drift" is used for two materially different invariants on the same dashboard and yields contradictory numbers for the same account/day (SPEC enhancement)

For the same account on the same business day: Daily Statement Drift = `$0.00`, Drift sheet drift = a non-zero multi-million value. Both mathematically defensible:
- Daily Statement Drift = `Closing − (Opening + signed-net flow)` — close-of-day **flow** drift
- Drift sheet drift = `parent_stored − Σ child_stored` — **hierarchy / aggregation** drift

But labeling both as "Drift" on the same dashboard guarantees operator confusion when they cross-check.

**Proposed fix:** rename one. Suggested: "Flow Drift" / "Posting Drift" on Daily Statement, "Aggregation Drift" / "Hierarchy Drift" on the Drift sheet. Worth a SPEC entry to lock the vocabulary.

#### 9. Investigation → Recipient Fanout: KPI `Distinct Senders` shows a global union value but every visible per-recipient row in the table shows a smaller per-recipient value with the same column name

KPI `Distinct Senders = 31` (across the recipient set); detail table column `Distinct Senders` = 22 on every visible row. Both columns labeled the same way. The KPI is plausibly a UNION (31 = |union of all per-recipient sender sets|) while the per-recipient column is per-recipient (22 = senders feeding *this* recipient). If so, the math could be self-consistent.

**Proposed fix:** label disambiguation (e.g., KPI = "Distinct Senders (Union)", column = "Senders Feeding This Recipient"); OR if the KPI is actually double-counting somehow, fix the SQL.

#### 10. Unbundled Posted Legs Aging chart is one screen-wide stacked bar with no temporal shape for thousands of stuck legs

Looks like a status pixel that happens to be bar-chart-shaped, not an age-bucket histogram. Operator coming to triage a four-digit count of stuck legs cannot tell whether the problem is one bad day or chronic. Compare to the sibling Pending Aging chart on the same dashboard, which has the *opposite* dwarfing problem — one dominant bucket dwarfs the rest, others invisible. Both suggest the bucket binning hasn't been tuned for realistic data distributions.

**Proposed fix:** true age-bucket histogram with binning tuned to data; log-scale option or "% of total" overlay for the Pending Aging companion. Consider exposing the bucket-edge config in the dashboard config so integrators can tune it for their plant shape.

#### 11. L1 Overdraft KPI and L1 Drift sheet leaf-drift table surface different invariants on the same dashboard with no scope explainer

An account holding a chronically negative stored balance over multiple consecutive days surfaces on Overdraft (as a violation row) but is absent from Drift sheet's leaf-drift table — because its cumulative net flow matches the negative stored balance (so flow-drift = 0 even though stored is wildly negative). Two related-but-orthogonal invariants surfaced on the same dashboard without prose explaining the relationship.

**Proposed fix:** SPEC enhancement — the L1 dashboard prose should clarify that Overdraft (`stored < 0`) and Drift (`stored vs computed`) are orthogonal SHOULD-constraints; a chronic-overdraft account that's "consistent with itself" still warrants top-line callout independent of drift status.

### Polish

#### 12. `Average Daily Volume` KPI renders 3 decimal places on a count metric (CARRYOVER from v11.22.0 #18)

KPI value displays as e.g. `36,955.696`. The KPI subtitle itself even self-narrates the prior cold-read finding: "v11.22.1 cold-read finding #18 noted QS's default 3-decimal display ('2.000') for AVERAGE aggregations was wrong for a count-of-things." Documented and not fixed. Same family: Volume Anomalies pair-window stats also display 3-decimal precision on dollar sums (wrong format for currency).

**Proposed fix:** QS number-format override on count aggregations (integer); on dollar sums (2-decimal currency).

#### 13. Operator-facing tooltip / subtitle text leaks sprint-archaeology language

Examples observed:
- Daily Statement Debits-signed KPI title contains a paragraph quoting "v11.22.1 cold-read finding #1 sibling rename"
- App Info Matview Status subtitle on every dashboard includes `(BH.18 follow-up 2026-05-26 after v11.22.1 cold-read: the panel was being read as 'all deploy matviews'…)`

Operator-facing copy shouldn't carry remediation-cycle archaeology. String cleanup pass on KPI subtitles and panel headers.

#### 14. Executives → Account Coverage: `Total Open Accounts` and `Active Accounts (this window)` rendered as same-size adjacent KPI tiles, identical values

When every open account has window activity (a frequent state on a fully-seeded demo), the two KPIs collapse to the same number. Side-by-side equal tiles invite the misread that they're *meant* to be different — operators may think the dashboard is broken.

**Proposed fix:** surface the diff explicitly — add a third tile "Inactive Accounts = `Open − Active`" so the operator can tell at a glance whether equality is meaningful (`Inactive = 0`) or whether a filter is degenerate.

#### 15. Date pickers default to `Latest day` placeholder text on sheets where they don't push down a date filter

L1 Transactions sheet shows a five-digit row count with Date From/To = `Latest day` — that's clearly not one day's worth at the displayed magnitude. Either the placeholder lies on this sheet or the picker doesn't push down here.

Phase BM unified pushdown across L1 — placeholder language hasn't been updated to match the cases where pushdown doesn't apply. Either the date pushdown works and the row count is from a different aggregation level (relabel the count), or the date filter is broken on this sheet (fix the filter).

#### 16. L1 Limit Breach sheet has no top-line KPI tile

When the detail tables are empty, an operator should not need to scroll/scan to determine "the answer is zero." Add a "Breaches in window" count KPI tile so the answer is anchored at the top.

#### 17. L2 Flow Tracing → Rails sheet is a wide ledger dump with no orientation subtitle or KPI row

Useful as an investigation/lookup tool, hostile as a cold-land target. A one-line subtitle ("Use this to look up an individual transfer leg by ID and inspect its journal row") and ideally a small KPI row ("Posted legs in window / Latest leg / Largest leg") would orient new operators.

#### 18. L1 Today's Exceptions chart hides small per-type bars under a dominant bar's y-axis scale

When one exception type dominates (e.g., a four-digit count) and others are in the single-digit range, the small bars round to ~0 visual pixels and disappear. Compare against the dedicated Pending Aging KPI which surfaces those small-count exception types correctly.

**Proposed fix:** y-axis-min handling or log-scale option on Today's Exceptions chart; or a companion counts-by-type table next to the chart so the long tail is visible.

## Methodology note

Three context-isolated cold-read judges, each given the same 40-PNG bundle and the same release-context briefing (Phase BM is the headline; intentionally-empty captures noted as expected post-BM behavior). No judge saw any prior-round review file, the L2 instance YAML, project PLAN.md, or each other's outputs. Convergent findings (triple consensus on Findings 1 and 6; double on Findings 2, 4, 7, 12) carry the highest confidence; single-judge findings are real but should be confirmed against the live dashboard before action.

The capture bundle is reproducible end-to-end from a Postgres/SQLite local stack via the four-step rebuild → start-server → capture-screenshots → run-personas cycle. Capture script (Playwright, full-page screenshots at 1600×1100 and 1700×1100) is available on request if useful for upstream regression scaffolding.

## What's working well (worth keeping)

For balance, signals that the round confirmed are working:

- **Daily Statement reconciles to the penny when populated** — Phase BM's date-picker pushdown does its job. Closes v11.22.0 #2.
- **App Info / diagnostic discipline (per-dashboard Liveness, deploy stamp, matview-freshness panel concept)** — exec-oversight judge called this best-in-class. The Matview Status per-app scope bug (Finding 7) doesn't change that the *discipline* is right.
- **Today's Exceptions sheet is operationally sane** — count KPI, histogram-by-type, sortable detail table, dollar magnitudes per row. A new operator can land and triage.
- **Drift Timelines shows real signal** — discrete leaf-drift spike for click-to-investigate, smoothly-rising parent-account series for accumulating residual. Trend-vs-event story.
- **Money Trail Sankey renders correctly when a chain is picked** — source → target ribbons sensibly weighted, hop-by-hop detail populates. The empty-state copy (Finding 5 family) is the only gap.
- **Overdraft sheet leads with one big number and shows the rows immediately underneath** — no ambiguity.
- **Supersession Audit** — 3 coherent KPIs, populated detail table, internally consistent counts.
