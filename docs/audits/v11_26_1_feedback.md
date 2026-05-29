# Phase 3 — v11.26.1 cold-sweep — UPSTREAM bug reports (scrubbed)

Actionable dashboard findings from a three-judge, screenshots-only cold read of the v11.26.1 PNG bundle, scrubbed of deployment-specific details and routed to the generic upstream dashboard repo. Findings whose root cause is a local seed/YAML fixture or that were marked wont-fix have been dropped (counted in the note at the bottom of this file). Each item below is reproducible against any instance whose seed exercises the same conditions.

Deploy stamp under review: recon-gen **v11.26.1**, dialect **sqlite (dev build)**. Liveness canary rendered on all four apps, so the rendering pipeline is healthy — every blank/empty visual below is a data/SQL/binding issue, not a renderer outage.

## Headline

Net-positive on rendering vs the prior baseline: the Account Network Sankeys now render, App Info now shows full per-app matview tables with row counts and latest dates, and Limit Breach finally has a KPI tile. **There is no genuine blocker this round** — the cold judges' "Daily Statement returns No data" finding (C1) was investigated post-read and **withdrawn as a capture-harness artifact, not a dashboard bug** (the statement is functional and reconciles to $0.00; the empties came from stale capture-script picker values — see the C1 note below). The top real defect is the new Limit Breach KPI tile, which ships broken — it renders the friendly empty-state string instead of the numeric **0** its own subtitle calls "the unambiguous healthy state" (fix-then-regress).

## Key context: the dashboard is the production surface

In deployment the dashboard is the operator's only surface. The matviews can be perfectly correct and the operator still makes wrong calls if the picker FK is wrong, a Sankey is blank, or a KPI says "No data" where it should say "0." Every blocker below is one a real operator hits before they ever look at the underlying data.

---

## Severity tally (upstream-actionable only)

| Severity | Count | Items |
|---|---|---|
| Blocker | 0 | (C1 withdrawn post-read — capture-harness artifact, not a dashboard bug; see C1 note) |
| High | 4 | Limit Breach KPI shows "No data" not 0 (C2); Transfer Templates Sankey blank (C3); Money Trail hop table has no $/account (C4); Today's Exceptions log-axis label overlap (C5) |
| Medium | 5 | Exceptions "5 vs 7" taxonomy + chart-vs-table contradiction (C6); zero-magnitude account-less exception rows — display half only (C7); Drift "N accounts" counts account-days (C8); cross-app leg-count delta (C9); Drift vs Drift-Timelines near-equal twin figures (C10) |
| Low | 9 | parent-drift headline unreconciled on screen — caption only (C11); money-trail-edges matview blank latest-date (C12); matview freshness mixed signals (C13); Supersession key-vs-row label + drill (C14, display half); today's-exceptions scope-gap caption (C15); leaf-drift vs posting-drift formula clash (C16); Account Network "trust the chart not the control" wart (C17); Overdraft title "Accounts" vs subtitle "day-rows" (C18); Stuck Unbundled count vs "Healthy=0" caption (C19, caption only); Getting-Started "line chart" vs rendered stacked bar (C20); 3-decimal count formatter (C21); plus a cosmetic cluster (C22) |

---

## Findings

Consensus ordering: items flagged by all three judges rank highest, then two-judge, then single-judge.

### Blocker

#### C1. ~~Per-Account Daily Statement returns "No data" for the IDs the picker offers~~ — **WITHDRAWN (not an upstream bug)**

**Do not file.** The cold judges (screenshots-only) saw empty Daily-Statement captures and reasonably inferred a picker/data FK defect. Post-read verification against the database, the live picker, and an operator manual test shows the Daily Statement **is functional**: the one capture driven through the picker with a still-valid full-label value populates all five KPI cards and reconciles to the penny (Posting Drift $0.00). The empty captures were a **local capture-harness artifact** — the capture scripts fed bare/stale account-picker values (the picker's option *value* is the full display label, and post-BM selection must be driven through the tom-select cascade, not URL params). The live picker offers the correct role-scoped instance IDs. No upstream change is warranted.

*Possible minor item to confirm separately (not filed here pending repro): the Role selector may not cascade-filter the Account dropdown — a UX nicety, not a data defect.*

### High

#### C2. Limit Breach "Breaches in Window" KPI renders "No data matches the current filters" instead of the numeric 0 its own subtitle calls the healthy state

The tile's subtitle reads "Zero = no rule violations in the window — the unambiguous healthy state," yet the tile shows the empty-state string with "Try widening the date range…". The detail table below is correctly empty (the data condition is genuinely healthy). Classic COUNT-over-empty-set returning NULL rather than 0. The KPI tile itself is new this round (the prior baseline had no tile), so this is fixed-then-regressed.

**Routing:** dashboard-bug. `COALESCE(COUNT(...), 0)` / `IFNULL` on the KPI aggregate, or a measure-level "show 0 on empty." **P1.**

#### C3. L2 Flow Tracing → Transfer Templates "Multi-Leg Flow" Sankey renders blank while the Template Instances table below is fully populated

The "Account → Template → Account" Sankey is an empty white box with no ribbons and no "no data" message, while the Template Instances table beneath has dozens of rows with amounts/dates, all status "Imbalanced." The same Sankey widget renders fine on Money Trail and Account Network when populated, so this is a default-state binding/aggregate bug, not a widget failure. **Same as the prior baseline's #3 — STILL-PRESENT.** This is the SAME Sankey-empty-state family as the now-fixed Account Network Sankey — check whether that fix was simply not applied to this sheet.

**Routing:** dashboard-bug. Either render aggregated when no template is picked, or wire an explicit "Pick a template to see its leg flow" empty-state. **P1.**

#### C4. Investigation → Money Trail hop-by-hop table has no amount, no source/target account, no date, no direction — only Root Transfer ID / Transfer ID / Depth

The Sankey renders source→target ribbons but they carry no dollar labels, and the companion table has exactly three columns. This is the dedicated "where did this money come from / go" view and it cannot say how much moved at each hop or between which accounts — the user has to re-pivot to Account Network (whose touching-edges table DOES carry Hop Amount + source/target account) to get the detail. Weakest link in the flow-tracing story.

**Routing:** upstream dashboard fix. Add Hop Amount, Source/Target Account ID+Name, posting date, and direction to the Money Trail edge table — mirror the Account Network touching-edges schema, which already has the right shape. **P1.**

#### C5. Today's Exceptions "Exceptions by Check Type" log-scale Y axis labels every minor tick, stacking labels into an unreadable blob

At the 100 and 10 decades the labels (100/90/80/70/60 and 10/9/8/7/6) overprint each other. The Executives "Period Total by Type" log chart labels only major decades and is readable — so this is a per-chart misconfiguration, not a platform limit. This is a regression-in-symptom of the prior baseline's #18 ("y-axis hides small bars"): the chart went log-scale to fix the dwarfing and traded it for label overlap.

**Routing:** dashboard-bug. Set the log axis to label major decades only (copy the Executives chart's axis config). **P1.**

### Medium

#### C6. Today's Exceptions plots 7 categories while copy says "5 invariant checks," and a check type with detail rows shows no bar **[DOUBLE CONSENSUS]**

Both the KPI subtitle and chart subtitle say "5 L1 invariants (drift, ledger drift, overdraft, limit breach, expected EOD balance)," but the x-axis plots seven differently-named categories: `chain_parent_disagreement`, `fan_in_disagreement`, `ledger_drift`, `multi_xor_violation`, `overdraft`, `stuck_pending`, `stuck_unbundled`. Only `ledger_drift` and `overdraft` overlap the prose; `fan_in_disagreement` and `chain_parent_disagreement` render NO bar yet the Exception Detail table contains a `fan_in_disagreement` row (count 1). So the rollup chart contradicts the detail table AND the copy.

**Routing:** dashboard-bug (copy + rollup-binding mismatch). Reconcile the "5 invariants" prose to the actual taxonomy, and confirm the summary chart's GROUP-BY covers every check type present in the detail. **P2.**

#### C7. Exception rows carry Magnitude Count = 0 and blank Account ID / Name, padding the headline open-exception count (display half) **[DOUBLE CONSENSUS]**

Some exception-detail rows show Magnitude Count = 0 (alongside others with nonzero counts), and several have blank Account ID and blank Account Name. A row flagged as an open exception with count 0 and no account is non-actionable and inflates the headline "Open Exceptions" count. An exceptions queue that lists zero-magnitude account-less "violations" trains the user to distrust the count.

**Routing:** dashboard-bug. Suppress or explain count-0 / account-less rows so they don't pad the headline count. **P2.** (Note: the upstream-actionable half is the display defect only. The *presence* of these specific zero-magnitude rows is a local seed/fixture artifact and is out of scope for the upstream repo.)

#### C8. Drift "Parent Accounts in Drift" KPI counts account-day rows, not distinct accounts

The KPI reads a double-digit count, but the Parent Account Drift table is a single account repeated across consecutive business days. Today's Exceptions surfaces only a couple of parent accounts in `ledger_drift`. So the headline counts account-days; the label "Parent Accounts" overstates the blast radius. Same class of bug as C18 (Overdraft title/subtitle grain mismatch).

**Routing:** dashboard-bug. Either `COUNT(DISTINCT account_id)` for the KPI, or relabel to "Parent Account-Days in Drift." **P2.**

#### C9. Cross-app "total legs" disagree: Executives + base table count differs from L2 Rails "Legs in Window" + the windowed-transactions matview, with nothing on screen reconciling the delta **[high trust weight]**

Cross-checking "how many transactions are in this system" returns two answers from two dashboards of the same system, with no on-screen note reconciling the base-table-vs-matview row delta. Same family as the prior baseline's "multiple denominators for a leg" finding.

**Routing:** upstream dashboard fix. Add a one-line footnote on Rails / App Info distinguishing the base-table count from the windowed-matview count, or make the Executives KPI label say "all-history" explicitly (the Rails "Legs in Window" label already hints at scope). **P2.**

#### C10. Drift sheet "Largest Parent Drift (anywhere in window)" vs Drift Timelines "Largest Parent Drift Day (peak business day)" — two near-equal "largest parent drift" figures one click apart

The subtitles now DO distinguish row-grain peak from daily roll-up peak (an improvement over the prior baseline's #4, where the labels didn't disambiguate). Residual: two big near-equal numbers still read as a cross-sheet discrepancy to anyone not parsing the fine print. **Partial fix vs the prior baseline's #4** — labels clarified, but the visual confusion persists.

**Routing:** dashboard-bug (minor) / wont-fix candidate. Co-locate the two figures with an explicit "(row-grain vs daily-rollup)" tag, or accept as documented. **P3.**

#### C11. Large red parent-level "drift" headline is unreconciled to a number on screen; the sheet's own cited SPEC example is an order of magnitude smaller (caption half)

The "Largest Parent Drift" headline shows a large red figure with a double-digit "Parent Accounts in Drift" count. The parent-drift detail shows a persistent large Stored-vs-Computed disagreement every day on the pool accounts. The KPI subtitle cites the SPEC example as a much smaller drift on a different account — roughly a 10x gap on entirely different accounts. The intro prose explains *why* pools accumulate pre-settlement value but never reconciles to *this* magnitude or says "and that is why you see this here, and it is expected." Ranked as the executive persona's #1 sign-off blocker.

**Routing:** upstream dashboard fix (caption only). The on-screen narrative should reconcile to the displayed number or add an "expected for closed-loop pools" all-clear caption. **P2** on the caption. (Note: the *magnitude itself* is a local demo-seed scale artifact, not a conservation failure, and is out of scope for the upstream repo.)

### Low / Polish

#### C12. Investigation App Info: the money-trail-edges matview shows a large row count but a BLANK Latest Date **[triple-ish consensus]**

Every other matview on every App Info sheet has a populated latest-date; this one — backing both Money Trail and Account Network — does not. By the sheet's own staleness rule, a populated matview with no latest-date is the exact "ETL hasn't refreshed / can't confirm freshness" signal. Either a null/unmapped date column on the edges matview or a failed freshness probe.

**Routing:** upstream dashboard fix (or matview-definition fix). The money-trail-edges matview needs a mapped max-date column for the freshness probe. **P2** (it undercuts the freshness oracle for the flow-tracing surface).

#### C13. App Info freshness panel shows mixed signals: one matview latest-date is stale by several days vs the base date; two others are future-dated (= deploy timestamp); one row shows count 0 + blank date

The panel framed as the freshness oracle shows stale, future-dated, and blank-date rows with no on-screen way to tell benign interval-semantics from genuine staleness.

**Routing:** confirm-on-aws. The future-dated rows (= generated timestamp) and the stale row may be benign interval semantics of the sqlite dev build vs a real refresh cadence; verify on a real AWS/Oracle refresh before treating as a bug. **P3.**

#### C14. Supersession Audit: "Logical Keys with Supersession" KPI vs "Supersessions with No Reason" KPI read contradictory side by side, and the no-reason count is an un-drillable flagged DQ issue (display half)

One KPI counts distinct logical keys, the other counts higher-entry rows, so not strictly contradictory — but the two counts side by side read as broken, and there's no drill into the no-reason rows for remediation. **Carryover of the prior baseline's #19** (the SHOULD-violation surfaces correctly).

**Routing:** dashboard-bug. Label clarification (rows vs keys) + add a drill into the no-reason rows. **P3.** (Note: the underlying "supersessions with no reason" data-quality gap is a local ETL writer issue — the writer should set a `supersedes` reason on every higher-entry — and is out of scope for the upstream repo; the dashboard is correctly surfacing the gap.)

#### C15. Today's Exceptions KPI count vs the `todays_exceptions` matview row count differ by ~16x with no reconciling note

The KPI is scoped to the most-recent business day; the matview holds history. Explainable, but App Info is where the user decides whether to trust the dashboard, and an unexplained large gap invites doubt.

**Routing:** upstream dashboard fix — a one-line "today's-scope vs all-history" caption. **P3.**

#### C16. Leaf-drift (cumulative-ledger basis) vs Daily Statement Posting Drift (single-day-walk basis) look contradictory without a cross-reference

Two sheets both labeled "drift" using different reconciliations: a leaf shows a nonzero cumulative-ledger drift while the Daily Statement Posting Drift for a related account reads $0.00. **Same semantic as the prior baseline's #8 ("Drift" noun overloaded) — STILL-PRESENT.** Each header explains its own formula; nowhere cross-referenced.

**Routing:** upstream dashboard fix / SPEC enhancement — rename one ("Aggregation/Hierarchy Drift" on the Drift sheet vs "Posting/Flow Drift" on Daily Statement) or add a one-liner cross-reference. **P3.**

#### C17. Account Network sheet twice instructs "the dropdown widget above may briefly lag behind a walk; trust the chart, not the control text"

Honest, but telling the user to distrust an on-screen control erodes confidence during a walk-the-edges investigation.

**Routing:** dashboard-bug — fix the dropdown-vs-chart lag rather than documenting around it. **P3.**

#### C18. Overdraft KPI title "Accounts in Overdraft" but subtitle says "day-rows"; the count is an unreconciled windowed subset of the matview row count

Title/subtitle grain mismatch on a balance-health KPI; day-rows ≠ accounts, and the headline is an unreconciled windowed subset of the matview rows. Same bug class as C8.

**Routing:** dashboard-bug — relabel to "Account-Days in Overdraft" or `COUNT(DISTINCT account_id)`. **P3.**

#### C19. "Stuck Unbundled" count against subtitle "Healthy = 0" with no "demo-seed expected" caveat; dollar exposure is tiny (caption half)

A loud unhealthy-looking signal at a glance, but tiny dollar exposure so not a conservation issue.

**Routing:** dashboard caption — add a "demo-seed expected" note next to the count. **P3.** (Note: the magnitude of the backlog is a local demo-seed scale artifact and out of scope for the upstream repo.)

#### C20. Getting Started describes Transaction Volume as "the line chart (the trend)" but the sheet renders a stacked bar chart by rail

Doc-vs-render mismatch on the orientation page.

**Routing:** dashboard-bug — string fix on Getting Started copy to match the rendered stacked bar. **P3.**

#### C21. Executives "Average Daily Volume" KPI shows three decimals on a transaction count

Implies fractional transactions. **Carryover of the prior baseline's #12 — STILL-PRESENT.** The format override still hasn't landed on this measure.

**Routing:** dashboard-bug — number-format override (0 decimals) on count aggregations. **P3.**

#### C22. Cosmetic cluster (single-judge, low confidence)

- **Clipped legend labels** (rail names truncated to "…") on Transaction Volume and Unbundled Aging — rails can't be told apart by name. → dashboard-bug, P3 (legend width / wrap).
- **"Leaf Account Drift Over Time" renders effectively empty** (narrow axis, single x-tick, no visible line) because only one leaf is in drift. → dashboard-bug, P3 (point marker / empty-state hint on sparse series).
- **Axis tick format inconsistency** (mixed "Thu 21, Fri 22, … May 24, Mon 25…") on Drift Timelines. → dashboard-bug, P3 (date-format string).
- **"Stuck Pending by Age Bucket" single floating bar, one solid color** vs "stacked by rail" subtitle (both txns share one rail, so technically correct). → dashboard-bug, P3 (label empty buckets / explain single-rail).
- **Recipient Fanout "Senders Feeding This Recipient" equals the Qualifying Recipients KPI** on every visible row. → confirm-on-aws (verify the per-recipient column isn't bound to the recipient count; the fanout sheet is self-aware that union ≠ per-recipient, so likely coincidence). Replaces the prior baseline's #9 (numbers moved, suspicion is the same shape).
- **Open Accounts = Active Accounts**, identical bar shapes. → confirm-on-aws (does the "active" window filter actually filter, or pass through?). Carryover of the prior baseline's #14.
- **Account Coverage detail skips an account ID in the visible page**, one account one day stale. → confirm-on-aws (likely page ordering; verify the skipped ID exists).

---

## What works well

- **Daily Statement reconciles to the penny when the picker resolves**: Opening + Debits + Credits = Closing, Posting Drift $0.00 green check, full posted-records detail. Verified independently by two judges. The SQL is sound — only the picker FK (C1) gates it.
- **App Info canary + full per-app matview table is best-in-class.** Liveness canary on all four apps, deploy stamp, per-matview row counts AND latest dates. Praised as the trust device that makes pipeline-vs-data triage easy — and it self-surfaced C12/C13 (doing its job). This is the prior baseline's #7 fix landing.
- **Money Trail and Account Network Sankeys render correctly when populated** — ribbons, hop tables, inbound/outbound split all draw; empty base sheets show clear "pick from the dropdown" guidance. This is the prior baseline's #2 fix landing.
- **Executives suite is clean**: Account Coverage with per-type bars, steady stacked transaction volume with a readable log-scale Period Total, signed Net/Gross money-moved figures with good "expected near zero on a balanced book" teaching framing.
- **Exception-view trifecta nailed** on Drift, Overdraft, Pending Aging, Unbundled Aging: plain-English invariant header + quantifying KPI + actionable detail table with right-click drill. The Overdraft "overdraft = sign check vs drift = reconciliation check, same datum, two independent constraints" header is genuinely good training material.
- **L2 Rails and Chains tables are solid**: Rails "Legs in Window" matches the windowed-transactions matview; Rails is the best leg-lookup tool (filter by ID/rail/status/bundle/metadata); Chains correctly derives Completion Status from Declared/Fired.
- **Volume Anomalies is statistically credible**: σ slider, a distribution chart deliberately NOT filtered by the slider so the population is visible, ranked Window Sum / Pop Mean / Pop Stddev / Z Score table.
- **Recipient Fanout is configurable and self-aware** — pre-explains why per-recipient sender count can differ from the union KPI.
- **Empty-state messaging is consistent and friendly** across filter-driven sheets — which is exactly why the Limit Breach should-be-0 KPI (C2) stands out as a presentation miss rather than a data hole.
- **Strong orientation + deploy provenance**: every app has a Getting Started sheet naming each sheet's purpose; Investigation frames sheets as questions; the deploy stamp lets an auditor cite exactly what was reviewed.

---

## Triage routing — upstream-actionable findings

| # | Finding | Route | Priority |
|---|---|---|---|
| C1 | Daily Statement picker offers IDs absent from balance tables | **upstream** (picker SQL) | **P0** |
| C2 | Limit Breach KPI shows "No data" instead of 0 | **dashboard-bug** (COALESCE) | **P1** |
| C3 | Transfer Templates Multi-Leg Flow Sankey blank w/ populated table | **dashboard-bug** (default-state binding) | **P1** |
| C4 | Money Trail hop table missing amount/account/date/direction | **upstream** (add columns) | **P1** |
| C5 | Today's Exceptions log-axis minor-tick label overlap | **dashboard-bug** (axis config) | **P1** |
| C6 | Exceptions "5 vs 7" taxonomy + chart-vs-table contradiction | **dashboard-bug** (copy + rollup GROUP-BY) | **P2** |
| C7 | Exception rows count=0 / account-less, inflate count (display half) | **dashboard-bug** (suppress/explain) | **P2** |
| C8 | Drift "N accounts" counts account-days | **dashboard-bug** (DISTINCT / relabel) | **P2** |
| C9 | Cross-app base-table vs windowed-matview leg-count delta | **upstream** (footnote / label scope) | **P2** |
| C10 | Drift vs Drift-Timelines near-equal twin figures | **dashboard-bug** (co-locate/tag) / wont-fix | **P3** |
| C11 | Parent-drift headline unreconciled on screen (caption half) | **upstream** (all-clear caption) | **P2** |
| C12 | money-trail-edges matview blank latest-date | **upstream** (map max-date column) | **P2** |
| C13 | Matview freshness mixed signals (stale/future/blank) | **confirm-on-aws** (interval semantics) | **P3** |
| C14 | Supersession key-vs-row labels + drill (display half) | **dashboard-bug** (labels+drill) | **P3** |
| C15 | Today's Exceptions KPI vs matview row-count, no caption | **upstream** (scope caption) | **P3** |
| C16 | Leaf-drift vs posting-drift formula clash, no cross-ref | **upstream** (rename/cross-ref) | **P3** |
| C17 | "Trust the chart not the control" dropdown lag | **dashboard-bug** (fix lag) | **P3** |
| C18 | Overdraft title "Accounts" vs subtitle "day-rows" | **dashboard-bug** (relabel) | **P3** |
| C19 | Stuck Unbundled count vs "Healthy=0" (caption half) | **dashboard** caption | **P3** |
| C20 | Getting-Started "line chart" vs rendered stacked bar | **dashboard-bug** (copy) | **P3** |
| C21 | 3-decimal count formatter (Avg Daily Volume) | **dashboard-bug** (number format) | **P3** |
| C22 | Cosmetic cluster (legends, sparse leaf line, axis format, single bucket bar, fanout=KPI, Open=Active, coverage page gap) | mix: **dashboard-bug** (cosmetics) / **confirm-on-aws** (3 verification items) | **P3** |

---

## DIFF vs prior baseline

### FIXED

- **Account Network Inbound + Outbound Sankeys render blank** → **FIXED.** Sankeys now render ribbons + touching-edges table correctly.
- **App Info Matview Status panel byte-identical 2-rows-on-every-app** → **FIXED.** App Info now shows full per-app matview tables with row counts AND latest dates (this is what surfaced the new C12/C13 freshness findings — the panel doing its job).
- **Limit Breach sheet has no KPI tile** → **FIXED (then regressed).** A "Breaches in Window" KPI tile now exists — but it renders "No data" instead of 0 (new C2).
- **L2FT Rails sheet lacks orientation** → **FIXED (effectively).** Rails now reads as the single best lookup tool with clear filtering; no judge flagged missing orientation.
- **Sprint-archaeology / remediation-cycle language in operator-facing copy** → **FIXED (no longer flagged).**

### STILL-PRESENT

- **Daily Statement empty for picker IDs (FK mismatch)** → **STILL-PRESENT.** Identical triple-consensus blocker (now C1). Two releases, not fixed.
- **L2FT Transfer Templates Multi-Leg Flow Sankey blank** → **STILL-PRESENT** (now C3). SAME Sankey-empty-state family as the now-fixed Account Network Sankey — the fix was not applied here.
- **Drift vs Drift Timelines largest-parent-drift gap + leaf sign flip** → **PARTIALLY FIXED / STILL-PRESENT** (now C10). Subtitles now disambiguate row-grain vs daily-rollup peak and the leaf sign convention is consistent; residual is the two near-equal figures still read as a discrepancy at a glance.
- **Daily Statement landing has five blank KPI cards / no orientation copy** → **STILL-PRESENT** (subsumed under C1; the empty-state still coaches "widen the date range" rather than "pick an account").
- **Money Moved Net red coloring, missing per-rail attribution** → **STILL-PRESENT** (sibling of C11; the red Net is unchanged, still no per-rail decomposition beside it).
- **"Drift" noun overloaded across two sheets** → **STILL-PRESENT** (now C16).
- **Recipient Fanout KPI vs per-row count mismatch** → **STILL-PRESENT-as-class** (now C22; numbers moved, same suspicion shape, still confirm-on-aws).
- **Overdraft vs Drift different invariants on same dashboard** → **STILL-PRESENT (de-escalated).** The Overdraft header now explicitly teaches the orthogonality — the explainer landed.
- **3-decimal count formatter** → **STILL-PRESENT** (now C21).
- **Account Coverage Open = Active** → **STILL-PRESENT** (now C22; still confirm whether the "active" filter is a no-op).

### REGRESSED

- **Today's Exceptions y-axis hides small bars** → **REGRESSED-IN-SYMPTOM** (now C5). The chart went log-scale to fix the dwarfing and traded it for overlapping minor-tick labels.

### NEW this round (upstream-actionable)

- **C2** — Limit Breach KPI shows "No data" instead of 0 (new because the KPI tile is new).
- **C4** — Money Trail hop-by-hop table has no amount/account/date/direction (surfaced now that the Sankey reliably renders).
- **C6** — Today's Exceptions "5 vs 7" taxonomy + chart-vs-table contradiction (double consensus).
- **C7** — exception rows count=0 / account-less (double consensus; display half is upstream-actionable).
- **C8** — Drift "N accounts" counts account-days (C18 Overdraft is the same class).
- **C9** — Cross-app base-vs-matview leg-count delta.
- **C12** — money-trail-edges blank latest-date (surfaced because the per-app matview table now renders). Triple-ish consensus.
- **C13** — Matview freshness mixed signals (stale / future-dated / blank).
- **C11** — parent-drift headline unreconciled vs the cited SPEC example (caption half is upstream-actionable).
- **C17** — Account Network "trust the chart not the control" dropdown-lag wart (visible now that the sheet renders).
- **C18** — Overdraft title "Accounts" vs subtitle "day-rows."
- **C19** — Stuck Unbundled count vs "Healthy=0" (caption half).
- **C20** — Getting Started "line chart" vs rendered stacked bar.
- **C22 cosmetics** — clipped legends, sparse leaf line, axis-format inconsistency, single floating bucket bar, coverage page gap.

---

## Methodology note

Three context-isolated cold-read agents (skeptical QA / reconciliation-accountant pragmatist / executive-oversight), each given the same 40-PNG bundle. No agent saw any prior-round review, the seed config, the plan, or each other's outputs. Convergent findings carry the highest confidence: triple consensus on C1 (and C12 near-triple); double on C6, C7. Single-judge findings are real but should be confirmed against the live dashboard before action — see the confirm-on-aws routing for C13 and the three verification cosmetics in C22.

This report is scrubbed for upstream consumption: findings whose root cause is a local seed/fixture or that were marked wont-fix have been dropped (the data-magnitude halves of C7/C11/C19 and the local ETL-writer half of C14 are out of scope here; only their dashboard-display/caption halves remain). All deployment-specific identifiers have been replaced with generic dashboard mechanics.
