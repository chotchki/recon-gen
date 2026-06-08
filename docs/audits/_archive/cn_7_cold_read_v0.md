# CN.7 cold-read v0 (source-side)

Date: 2026-06-08
Method: source-side comparison only — handbook prose vs matview SQL
(`src/recon_gen/common/l2/schema.py`), dataset SQL
(`src/recon_gen/apps/<app>/datasets.py`), Sheet `description=` /
populator visuals + Drill actions in `src/recon_gen/apps/<app>/app.py`.
Screenshot-driven complement (where-is-the-button friction) deferred
to operator session.

## Per-page findings

### l1/getting-started.md

- "ten invariant checks (drift, overdraft, limit breach, pending/unbundled
  aging, chain cardinality, XOR groups)" (line 24) under-counts the actual
  L1 Exceptions matview UNION, which carries **12 branches**
  (`drift`, `ledger_drift`, `overdraft`, `limit_breach`,
  `expected_eod_balance_breach`, `balance_cadence_gap`, `stuck_pending`,
  `stuck_unbundled`, `chain_parent_disagreement`, `xor_group_violation`,
  `fan_in_disagreement`, `multi_xor_violation`).
  **Evidence:** `src/recon_gen/common/l2/schema.py:3197-3326` (the
  `{matview_create_kw} {p}_l1_exceptions` UNION ALL stack).
  The L1 Exceptions sheet's own description + KPI subtitle hit the
  same 10-vs-12 gap (see `l1/exceptions.md` below); both copies need
  reconciling.
- "scoped to the most recent business day in the data" (line 24) is
  stale. **Evidence:** `src/recon_gen/common/l2/schema.py:3188-3201`
  (matview comment: "No matview-level day filter. The sheet's date
  picker pushes `pL1DateStart` / `pL1DateEnd` over `business_day`")
  + `apps/l1_dashboard/app.py:1056-1058` ("the matview is no longer
  pre-filtered to latest_day; date narrowing happens at the dataset
  SQL"). The handbook copy describes pre-BV.3.3.c behavior.
- Broken sibling link `[L1 Exceptions](l1-exceptions.md)` (line 48)
  — actual file is `docs/handbook/l1/exceptions.md`. The hyphen-
  underscore convention got crossed.
- Broken sibling link `[Info](app-info.md)` (line 51) — file is
  `docs/handbook/_shared/app-info.md`; this page is in `l1/`, so
  the link needs `../_shared/app-info.md`.
- Vocabulary discipline drift: `matview` first appears on line 40
  un-expanded; not linked to glossary until the Vocabulary footer.
  `L2 instance` first appears on line 7 un-expanded.

### l1/drift.md

- (no findings) — handbook matches `_populate_drift_sheet`
  (`src/recon_gen/apps/l1_dashboard/app.py:605-837`). KPI titles
  (`Leaf Account-Days in Drift`, `Largest Leaf Drift (anywhere in
  window)`, `Parent Account-Days in Drift`, `Largest Parent Drift
  (anywhere in window)`), table titles (`Leaf Account Drift`,
  `Parent Account Drift`), and the right-click → *View Daily
  Statement for this account-day* drill all line up. Quirks-log
  anchor caveat: see Cross-cutting below.

### l1/drift-timelines.md

- (no findings) — handbook matches `_populate_drift_timelines_sheet`
  (`apps/l1_dashboard/app.py:840-941`). KPI titles
  (`Largest Leaf Drift Day (peak business day)`,
  `Largest Parent Drift Day (peak business day)`) and line-chart
  titles (`Leaf Account Drift Over Time`,
  `Parent Account Drift Over Time`) match. Categorial axis +
  per-`account_role` color split match the source. Correctly notes
  no row-level drills.

### l1/overdraft.md

- "displayed at SECOND granularity to preserve the per-account
  boundary timestamp" (line 33) — correct per
  `apps/l1_dashboard/app.py:982-984` (`date_granularity="SECOND"`).
- Handbook explicitly says the matview's `business_day_end` /
  `source` columns are "not displayed in the base table" (line 37)
  — correct per source columns at `app.py:993-1000`.
- (no major findings) — KPI title `Account-Days in Overdraft`,
  table title `Overdraft Violations`, single right-click drill to
  Daily Statement all line up with source.

### l1/limit-breach.md

- "Outbound transfer limit violations" (line 3 / *What this sheet
  teaches*) is half the picture — post-AB.1 the matview unions
  outbound AND inbound branches; the sheet's own description
  (`apps/l1_dashboard/app.py:328-334`) names both, and the rest
  of the handbook prose (lines 27-37) describes both. The lead
  sentence is the inconsistency.
- Broken sibling link `[L1 Exceptions](l1-exceptions.md)` (line 132)
  — file is `exceptions.md`, same defect as in `getting-started.md`.
- `[LimitSchedules](../_glossary.md#account-role)` (line 24)
  points the LimitSchedules term at the `account-role` glossary
  anchor; the glossary has no LimitSchedules entry. Misleading
  link target.
- KPI title `Breaches in Window`, detail table title `Limit Breach
  Detail`, and the right-click → *View Daily Statement* drill all
  match source (`apps/l1_dashboard/app.py:1210-1283`).

### l1/pending-aging.md

- Column list (lines 27-41) advertises `account_role` as a
  displayed column. **Evidence:** detail-table column list at
  `apps/l1_dashboard/app.py:1366-1377` projects
  `account_id`, `account_name`, `transfer_id`, `rail_name`,
  `amount_money`, `amount_direction`, `posting`, aging-bucket,
  `max_pending_age_seconds`, `age_seconds`. `account_role` is in
  the matview but NOT in the displayed table.
- KPI title (`Stuck Pending`), bar-chart title (`Stuck Pending by
  Age Bucket`), table title (`Stuck Pending Detail`), and the
  right-click → *View Transactions for this transfer* drill all
  match source. Five aging bands (0-6h, 6-24h, 1-3d, 3-7d, >7d)
  match the sheet description.

### l1/unbundled-aging.md

- Column list (lines 15-24) advertises both `account_role` AND
  `account_parent_role` as displayed columns. **Evidence:** the
  detail-table column list at
  `apps/l1_dashboard/app.py:1492-1502` shows neither — only
  account_id / account_name / transfer_id / rail_name / amount_money
  / amount_direction / posting / aging-bucket /
  `max_unbundled_age_seconds` / `age_seconds`. Same shape as the
  Pending-Aging finding above.
- "amount_money — leg amount in dollars (signed: positive in,
  negative out)" (line 19) is correct in sign convention but the
  table label is just `Amount Money`; no sign-glyph differentiator
  in the rendered visual. Minor wording.
- Bar-chart title `Stuck Unbundled by Age Bucket` and the dual KPI
  pair (`Stuck Unbundled`, `Stuck Unbundled Exposure`) match
  source (`apps/l1_dashboard/app.py:1424-1480`).

### l1/supersession-audit.md

- "Below the strip sit two side-by-side tables" (line 7) — the
  populator stacks the *Transactions Audit* and *Daily Balances
  Audit* tables in separate full-width rows
  (`apps/l1_dashboard/app.py:1610-1664`: Row 2 + Row 3 each
  `width=_FULL`). Not side-by-side.
- KPI titles (`Logical Keys (Transactions) with Supersession`,
  `Supersession $ Exposure`, `Supersession Rows with No Reason`),
  table titles (`Transactions Audit`, `Daily Balances Audit`)
  match source.

### l1/exceptions.md

- "ten L1 invariant matviews" (line 13) under-counts. The
  `<prefix>_l1_exceptions` UNION ALL has **12 branches** — handbook
  lists only 10 and omits `balance_cadence_gap` (the CL.6
  invariant) and one of the chain checks. **Evidence:**
  `common/l2/schema.py:3197-3326`. The sheet `_L1_EXCEPTIONS_
  DESCRIPTION` (`apps/l1_dashboard/app.py:380-400`) AND the KPI
  subtitle / bar-chart subtitle (`app.py:1052-1095`) also call it
  "10". This is a coordinated drift: handbook AND sheet copy AND
  KPI text all say 10 when the SQL says 12. Per
  `[[feedback_quirks_log_ever_growing]]` and the project's
  "encode invariants in types" stance, the 10/12 mismatch is the
  load-bearing fix; handbook is one of three places to update.
- Within the 10 the handbook does list (lines 16-29), it names
  `xor_group_violation` but omits `multi_xor_violation`; the sheet
  copy does the opposite (lists `multi-XOR violation` and omits
  `xor_group_violation`). Both shapes exist in the matview.
- Handbook says transfer-keyed rows have `account_id` as NULL
  (line 43). Confirmed — the UNION branches use
  `{null_text} AS account_id` (`schema.py:3271-3324`).
- KPI title (`Open Exceptions`), bar-chart title
  (`Exceptions by Check Type`, log-scale Y), table title
  (`Exception Detail`) all match source.
- Cross-sheet drill spec is correct: left-click → *Narrow Drift
  to this account* (Drift sheet); right-click → *View Daily
  Statement for this account-day*. Confirmed
  (`apps/l1_dashboard/app.py:1135-1151`).

### l1/daily-statement.md

- Broken sibling link `[Transactions](../transactions.md)` (line 60)
  — the file is in the same `l1/` dir, so the relative path needs
  to be `transactions.md` (not `../transactions.md`).
- Handbook describes 5 top KPIs + 2 bottom "context" KPIs — matches
  source (`apps/l1_dashboard/app.py:1737-1876`: Opening Balance,
  Debits (signed), Credits (signed), Closing Stored, Posting Drift
  at top; Accounts available, Roles available at bottom).
- Table title `Posted Money Records` + columns (transaction_id,
  transfer_id, rail_name, amount_money, amount_direction, status,
  origin, posting) match.
- Right-click → *View Transactions for this transfer* drill
  confirmed.

### l1/transactions.md

- Column list (lines 15-24) over-promises **what the visible table
  shows**. **Evidence:** displayed columns at
  `apps/l1_dashboard/app.py:1697-1708` are account_id, account_name,
  account_role, transfer_id, rail_name, amount_money,
  amount_direction, status, origin, posting. Handbook adds
  `transaction_id` (id renamed), `account_parent_role`, and
  `transfer_parent_id` — none of which appear in the Posting Ledger
  table even if the dataset SQL projects them. The handbook should
  either drop these or qualify them as "dataset columns, not
  displayed".
- Five filter dropdowns (Account, Transfer, Status, Origin,
  Transfer Type) confirmed
  (`apps/l1_dashboard/app.py:2249-2278`).
- Drill spec: handbook claims "*Posting Ledger* table row → ...
  Available from Pending Aging, Unbundled Aging, and Daily
  Statement's detail table" (lines 56-58). The Transactions sheet
  itself defines no outbound drills — this is correct — but the
  phrasing reads as if the sheet defines one. Minor wording.

### l2ft/getting-started.md

- "five explorer tabs" (line 7) over-counts. The L2FT analysis has
  Getting Started + 4 explorer sheets (Rails, Chains, Transfer
  Templates, L2 Exceptions) + App Info. "Four explorer tabs" is
  correct; "five" includes Info, which is the diagnostic canary,
  not an explorer.
- Same line is internally inconsistent — "four sheets" vs the list
  of five names (Rails, Chains, Transfer Templates, L2 Exceptions,
  Info).
- Otherwise just a landing-page summary; matches the source's
  Getting Started sheet shape (welcome text + per-sheet
  descriptions; no visuals).

### l2ft/rails.md

- "amount_money — the absolute amount in dollars" (line 19) is
  internally contradictory: the same bullet then says "Negative
  amounts still appear in dollars with a negative sign." The
  dataset converts BIGINT cents to dollars but does NOT take ABS
  (`apps/l2_flow_tracing/datasets.py:766` —
  `{amount} AS amount_money` where `amount = cents_to_dollars_sql
  ("amount_money", …)`). The column is signed dollars; the first
  half of the bullet is wrong.
- Status mapping note (line 21) says "Other (any terminal state
  like Failed, Rejected, Cancelled)" — the base-table constraint
  only allows `Pending` / `Posted` / `Failed`
  (`apps/l2_flow_tracing/datasets.py:618`), so in practice
  "Other" only ever resolves to `Failed`. Handbook's example list
  over-states the universe.
- Column list (lines 14-24) matches source visual exactly
  (`apps/l2_flow_tracing/app.py:720-729`: posting, rail_name,
  transfer_id, account_name, amount_money, amount_direction,
  status, bundle_status, transfer_parent_id).
- KPI titles `Legs in Window` + `Largest Leg`, table title
  `Transactions` all match. Six filter controls (Date From, Date
  To, Rail, Status, Bundle, Metadata Key, Metadata Value) match.
  (Counting "Date From + Date To" as one date control gives six;
  individually it's seven.)

### l2ft/chains.md

- "L2 ([flow-tracing])" glossary anchor target
  `#l2-flow-tracing--per-chain-transfer-integrity` is plausible
  (the H3 in `_glossary.md` is "L2 Flow Tracing — per-chain
  transfer integrity"). But the **chain anchor**
  `#chain--a-declared-sequence-of-leg-patterns` (lines 11, 19) does
  NOT match — the glossary heading is simply "### Chain"
  (anchor `#chain`). Same broken pattern for
  `[template](../_glossary.md#template--a-blueprint-for-transfer-structure)`
  (line 16) — the heading is "### Template" (anchor `#template`).
  Multiple invented-anchor errors.
- Visual title (`Chain Instances`) and column list (parent_posting,
  parent_chain_name, parent_transfer_id, completion_status,
  required_fired, required_total, parent_amount_money,
  parent_status) match source
  (`apps/l2_flow_tracing/app.py:856-889`).
- Drill spec: handbook says L2 Exceptions row → *View in Chains
  (filter parent_chain_name to entity_a)* — confirmed
  (`apps/l2_flow_tracing/app.py:1254-1259`).

### l2ft/transfer-templates.md

- (no findings) — sheet title `Transfer Templates — Multi-Leg
  Flow`, Sankey title `Multi-Leg Flow — Account → Template →
  Account`, table title `Template Instances`, and column set all
  match source (`apps/l2_flow_tracing/app.py:1068-1107`). Sankey
  `items_limit=30` (handbook line 45) confirmed at
  `app.py:1083`.

### l2ft/l2-exceptions.md

- (no findings) — KPI title `Distinct Exception Types Open`, bar
  chart `L2 Violations by Check Type`, table `L2 Violation
  Detail`, and the two drill names (`View in Rails (filter
  rail_name to entity_a)` + `View in Chains (filter
  parent_chain_name to entity_a)`) all match
  (`apps/l2_flow_tracing/app.py:1185-1260`). Six checks
  enumerated correctly.

### investigation/getting-started.md

- Broken sibling links `[Recipient Fanout](recipient-fanout.md)`
  (line 52) and `[Volume Anomalies](volume-anomalies.md)` (line
  53) — actual files are `fanout.md` and `anomalies.md`. Multiple
  Investigation handbook pages share this defect (see
  `fanout.md`, `anomalies.md`, `money-trail.md` below).

### investigation/fanout.md

- Broken sibling link `[Volume Anomalies](volume-anomalies.md)`
  (line 52) — actual file is `anomalies.md`.
- KPI titles (`Qualifying Recipients`, `Distinct Senders (Union)`,
  `Total Inbound`), table title (`Recipient Fanout — Ranked`),
  slider title (`Min distinct senders`) all match
  (`apps/investigation/app.py:326-385`).
- Column descriptions (recipient_account_id /
  recipient_account_name / recipient_account_type, distinct
  sender count, transfer count, amount) match dataset contract
  (`apps/investigation/datasets.py:115-118` + `150-153`).

### investigation/anomalies.md

- Broken sibling link `[Recipient Fanout](recipient-fanout.md)`
  (line 59) — actual file is `fanout.md`.
- Sheet name (`Volume Anomalies`), KPI title (`Flagged at current
  σ`), bar-chart title (`Pair-Window σ Distribution`), table
  title (`Flagged Pair-Windows — Ranked`), slider title
  (`Min sigma`) all match
  (`apps/investigation/app.py:480-595`).
- Distribution chart binds to `ds_anomalies_distribution` (no
  σ-pushdown) while KPI + Table bind to `ds_anomalies` (with
  σ-pushdown) — handbook narrative (line 22) correctly conveys
  the unfiltered distribution + filtered KPI/table split.

### investigation/money-trail.md

- Broken sibling links `[Recipient Fanout](recipient-fanout.md)`
  (line 63) and `[Volume Anomalies](volume-anomalies.md)` (line
  64) — both files are `fanout.md` / `anomalies.md`.
- Sankey title (`Money Trail — Chain Sankey`, 2/3 width), table
  title (`Money Trail — Hop-by-Hop`, 1/3 width), four controls
  (Chain root transfer, Max hops, Min hop amount ($), Date Range)
  all match (`apps/investigation/app.py:685-795`).
- Column list (root_transfer_id, transfer_id, depth,
  source/target account_id/name/type, hop_amount, posted_at,
  rail_name) matches the matview shape; correctly notes that
  single-leg transfers don't render Sankey ribbons.

### investigation/account-network.md

- (no findings) — Sankey titles (`Inbound — counterparties →
  anchor`, `Outbound — anchor → counterparties`), table title
  (`Account Network — Touching Edges`), drill names (`Walk to
  this counterparty` from each Sankey via DATA_POINT_CLICK,
  `Walk to other account on this edge` from the table via
  DATA_POINT_MENU) all match source
  (`apps/investigation/app.py:906-1008`). Three-dataset split
  (bidirectional + inbound + outbound) correctly noted.

### executives/getting-started.md

- (no findings) — describes the 4 operational sheets (Program
  Health, Account Coverage, Transaction Volume Over Time, Money
  Moved). Sheet names match
  (`apps/executives/app.py:858-872`); note the
  *Transaction Volume Over Time* full name in the source vs the
  shortened `Transaction Volume` the handbook uses
  inconsistently — handbook uses both forms. Minor.

### executives/program-health.md

- "matview UNION of all L1 invariant violation types … and the
  L2FT chain-coherence checks" (line 13) is a defensible
  rephrasing — the chain/cardinality checks (chain_parent_
  disagreement, xor_group_violation, fan_in_disagreement,
  multi_xor_violation) DO live in the `<prefix>_l1_exceptions`
  matview but are conceptually L2 invariants. Calling them
  "L2FT" is a slight category-leak from the operator's
  perspective; the matview is L1's. Not load-bearing.
- KPI title (`Open L1 Invariant Violations`), threshold bands
  (amber=1, red=20), date-range window (30 days) all match
  (`apps/executives/app.py:465-518`).

### executives/account-coverage.md

- (no findings) — 2 KPIs (`Total Open Accounts`,
  `Active Accounts (this window)`), 2 horizontal bar charts
  (`Open Accounts by Type`, `Active Accounts by Type`), 1 detail
  table (`Account Detail`) all match source
  (`apps/executives/app.py:340-447`). Column descriptions for
  the detail table (account_id, account_name, account_type,
  last_activity_date, activity_count) match.

### executives/transaction-volume.md

- (no findings) — 3 KPIs (`Total Transactions (Posted,
  per-transfer)`, `Transfer Legs (all statuses)`,
  `Average Daily Volume`), 2 bar charts (`Daily Transaction
  Count by Type` stacked + `Period Total by Type` clustered
  log-scale) all match
  (`apps/executives/app.py:521-642`). The per-transfer-vs-per-leg
  KPI design note is correctly explained.

### executives/money-moved.md

- (no findings) — 2 KPIs (`Net Money Moved`, `Gross Money
  Moved`), daily stacked bar `Daily Gross Dollars Moved by Type`,
  period clustered bar `Period Total Gross Dollars by Type`
  (log-scale) all match
  (`apps/executives/app.py:650-751`). KPI sign indicator (▲/▼)
  noted correctly.

### l2-editor/bundles-activity.md

- Invents three matview names that don't exist in the schema
  (lines 31, 35, 37):
  - `<prefix>_unbundled_aging` — actual name is
    `<prefix>_stuck_unbundled`.
  - `<prefix>_bundle_drift` — not in the schema. The aggregating-
    rail balance check, if it exists, doesn't ship under this name.
  - `<prefix>_unbundled_orphan` — not in the schema.
  **Evidence:** `grep -n "_unbundled\|_bundle" common/l2/schema.py`
  surfaces only `_stuck_unbundled`. Either these matviews never
  shipped and the page is aspirational, or they were renamed and
  the page wasn't synced.

### l2-editor/chain-children.md

- "Chains-Required-Total breaches" (line 21) — the L1 exceptions
  matview uses `check_type='chain_parent_disagreement'` for the
  required-chain-child violation
  (`common/l2/schema.py:3269-3279`). No `Chains-Required-Total`
  check_type exists. Wording invention; should match the actual
  taxonomy.
- Validator references `C8a` / `C8b` etc. — not verified;
  plausible internal IDs.

### l2-editor/completion-expression.md

- "The L1 Timeliness matview" (line 36) — no matview by this name.
  Pending Aging is fed by `<prefix>_stuck_pending`
  (`common/l2/schema.py:2782`); there is no `<prefix>_timeliness`
  matview. Invented name.
- "Daily Statement KPI strip rolls up the count" (line 38) —
  Daily Statement's 5 KPIs are Opening Balance, Debits (signed),
  Credits (signed), Closing Stored, Posting Drift
  (`apps/l1_dashboard/app.py:1742-1791`). No stuck-count rollup.
  Inaccurate.

### l2-editor/leg-rail-xor-groups.md

- (no findings) — UI form documentation; validator references
  (`C1a-d`) plausible.

### l2-editor/max-unbundled-age.md

- "The `<prefix>_unbundled_aging` matview" (line 16) — same
  invented name as in `bundles-activity.md`. Actual matview is
  `<prefix>_stuck_unbundled`.
- "drill into Today's Exceptions" (line 24) — no sheet named
  "Today's Exceptions" exists in the L1 dashboard; the sheet is
  named `L1 Exceptions` (`apps/l1_dashboard/app.py:379-400`).
  Reference is stale (a prior phase-K name?).
- "roll up into the Daily Statement KPI strip" (line 24) — same
  inaccuracy as in `completion-expression.md`. Daily Statement
  doesn't roll up stuck-aging counts.

### l2-editor/metadata-keys.md

- (no findings) — UI form documentation; `PostedRequirements`
  references plausible.

### l2-editor/transfer-key.md

- (no findings) — UI form documentation; validator R12
  reference plausible.

### _shared/app-info.md

- KPI title (`Liveness`), Matview Status table title
  (`Matview Status — sources this app reads from`), Deploy Stamp
  text box all match source
  (`common/sheets/app_info.py:118-119` + populator). Per-dialect
  liveness SQL claim (line 11: Postgres `information_schema.tables`,
  Oracle `USER_TABLES`, DuckDB `information_schema.tables`)
  matches `_liveness_sql` (`app_info.py:131`).
- (no findings) — matches source.

## Cross-cutting

### Vocabulary discipline drift

- `l1/getting-started.md` — `matview` used on line 40 before
  the Vocabulary footer link (line 55). `L2 instance` first
  appears on line 7 without expansion. Per the template's
  Vocabulary discipline rule, first-use of project-specific
  terms must link to the glossary inline.
- `l1/exceptions.md` — `chain` and `rail` first appear in the
  context section without `[chain](../_glossary.md#chain)` inline
  link.
- Most pages do follow the rule; these are exceptions to flag.

### Link rot

Broken intra-handbook links found:

- `l1/getting-started.md` line 48: `[L1 Exceptions](l1-exceptions.md)`
  → should be `exceptions.md`.
- `l1/getting-started.md` line 51: `[Info](app-info.md)`
  → should be `../_shared/app-info.md`.
- `l1/limit-breach.md` line 132: `[L1 Exceptions](l1-exceptions.md)`
  → should be `exceptions.md`.
- `l1/daily-statement.md` line 60: `[Transactions](../transactions.md)`
  → should be `transactions.md`.
- `investigation/getting-started.md` lines 52, 53:
  `recipient-fanout.md` → `fanout.md`; `volume-anomalies.md`
  → `anomalies.md`.
- `investigation/fanout.md` line 52:
  `volume-anomalies.md` → `anomalies.md`.
- `investigation/anomalies.md` line 59:
  `recipient-fanout.md` → `fanout.md`.
- `investigation/money-trail.md` lines 63, 64: both broken
  (same shape as above).

Suspicious glossary-anchor links (anchor not present in
`_glossary.md`):

- `l2ft/chains.md` lines 11, 19: `#chain--a-declared-sequence-of-leg-patterns`
  → glossary heading is just "### Chain", anchor `#chain`.
- `l2ft/chains.md` line 16: `#template--a-blueprint-for-transfer-structure`
  → glossary heading is "### Template", anchor `#template`.
- `l1/limit-breach.md` line 24: `[LimitSchedules](../_glossary.md#account-role)`
  links to wrong glossary section (account-role).

Suspicious quirks-log anchors (the `quicksight-quirks.md` file uses
H2 headings that don't match these short anchor slugs):

- `l1/drift.md` line 168: `§count-distinct-quirk-bl1`
- `l1/drift.md` line 172: `§dependent-dropdown-no-refresh`
- `l1/daily-statement.md` line 64: `§cascade-source-dataset-must-be-unparameterized`
- `l1/transactions.md` line 69: `§dependent-dropdown-no-refresh`
- `account-network.md` line 86: `§dependent-dropdown-no-refresh`
  Actual H2 headings in `docs/reference/quicksight-quirks.md`
  start with the full symptom (e.g. `## CategoricalMeasureField
  (COUNT) silently renders DISTINCT…`), generating much longer
  slugs. The handbook anchors are aspirational labels, not real.
  The quirks-log file may need anchors added (HTML `<a id=…>`)
  to match these handbook labels.

### Banned terms

No occurrences of "Navy Cash" or other real-system names found in
the handbook tree (`grep -i -r 'navy cash\|navycash'` clean).
Vocabulary stays in the generic / Sasquatch frame as required by
`[[feedback_no_navy_cash_in_codebase]]`.

### Matview-naming pattern drift

The `l2-editor/*.md` pages repeatedly cite matview names that do
NOT exist in `common/l2/schema.py`:

- `<prefix>_unbundled_aging` (bundles-activity, max-unbundled-age)
  → actual: `<prefix>_stuck_unbundled`.
- `<prefix>_bundle_drift` (bundles-activity) → no such matview.
- `<prefix>_unbundled_orphan` (bundles-activity) → no such matview.
- `<prefix>_timeliness` (completion-expression) → no such matview.

These pages appear written ahead of the schema's actual shape, or
predate a Phase-X rename that the handbook didn't follow.
Reconciling them is the highest-cost fix in this audit because
some of the "named" matviews may not exist as separate views at
all (their checks are sub-branches of `_stuck_unbundled` /
`_chain_parent_disagreement` / etc.).

### L1 Exceptions 10-vs-12 drift

The triplet
  - sheet description (`_L1_EXCEPTIONS_DESCRIPTION`,
    `apps/l1_dashboard/app.py:381-400`)
  - sheet KPI subtitle (`Open Exceptions`,
    `apps/l1_dashboard/app.py:1054`)
  - sheet bar-chart subtitle
    (`apps/l1_dashboard/app.py:1083-1087`)
all say "10 invariant checks". The matview UNION
(`common/l2/schema.py:3197-3326`) has 12 branches. The handbook
echoes the "10" count. Two of the 12 (`balance_cadence_gap` and
one of the chain checks) are missing from every operator-facing
copy. Fix this in the SQL source first (or update the copy
everywhere) — handbook updates without source updates leave the
operator scanning two different numbers.

## Sign-off

- Total pages checked: 30 (5 L1 detail sheets + L1 Drift Timelines
  + L1 Daily Statement + L1 Transactions + L1 Supersession + L1
  Exceptions + L1 Getting Started + 4 L2FT sheets + L2FT Getting
  Started + 4 Investigation sheets + Investigation Getting
  Started + 4 Executives sheets + Executives Getting Started +
  7 L2 Editor field pages + shared App Info)
- Pages with findings: ~17.
- Severity distribution (operator's call, not graded here):
  - **Factual mismatch (load-bearing)**: 10/12 L1-exceptions drift
    (3 places + handbook); invented matview names in 3 L2-editor
    pages; over-promised displayed columns in 2 L1 detail pages
    + 1 L2FT page; "scoped to the most recent business day" stale
    claim in L1 Getting Started; outbound-only framing in
    Limit Breach lead sentence.
  - **Link rot (mechanical)**: 8+ broken intra-handbook links
    (mostly hyphen-conflated names like `l1-exceptions.md` →
    `exceptions.md`, `recipient-fanout.md` → `fanout.md`,
    `volume-anomalies.md` → `anomalies.md`); 3+ broken glossary
    anchors (compound slugs that don't match plain glossary
    headings); 5+ aspirational quirks-log anchors that don't
    correspond to real H2 anchors in `quicksight-quirks.md`.
  - **Wording / minor**: Supersession "side-by-side" claim,
    "Stuck Pending" `account_role` column over-promise,
    "5/seven explorer tabs" in L2FT Getting Started, "Today's
    Exceptions" stale reference, `recipient_account_type` claim
    in fanout column descriptions (valid, not a defect — flagging
    here as a confirmation that column-names-are-real).
- Open: iterative-screenshot cold-read (CN.7 v1) needs operator
  to drive the click-by-click pass.

## Operator-eyes-needed gaps

Things this source-side audit cannot judge:

- Does the `?` button render on each sheet's H1? Does it have a
  visible affordance (icon / tooltip / cursor change)?
- Does clicking `?` open the side panel on the same sheet, or
  does it navigate? Does the side panel position cover the
  visuals, or sit on the right edge?
- Does Escape close the side panel? Does clicking outside it close?
- Does the side panel render the markdown readably (heading sizes,
  bullet indentation, monospace for code) — or is it a wall of
  unstyled text?
- Does the side panel resolve `../_glossary.md` links correctly
  in the App2 router? (The `?` route's markdown renderer needs
  to be able to follow relative paths to the glossary and to
  sibling handbook pages. With ~8 broken sibling links flagged
  above, even when those are fixed, the operator should verify
  the renderer doesn't have its own link-resolution bug.)
- Does the side panel scroll independently of the main sheet?
- Does the side panel size correctly on the small 1366-wide
  laptop screen most ops folks use? (Sheets are ~36 grid columns;
  if the side panel claims 12 of them, the visuals get cramped.)
- On the L2 Editor pages (`l2-editor/*`), does the `?` button
  appear on the studio editor form fields themselves (not on
  dashboard sheets)? CN.7 v1 needs to confirm the studio's
  field-level `?` wiring against the same set of pages.
- Cold-read operator should also confirm the broken sibling
  links surface as actually-broken in the side panel (404 / red
  warning) — not silently failing.
