# Vocabulary

This handbook talks about **L1**, **L2**, and a handful of project-
specific terms that aren't universal banking jargon. Define them here
once so every other page can lean on the names without re-explaining.

## The four dashboards

Recon Generator ships four dashboards on top of one shared data model.
Each one answers a different CLASS of integrity question.

### L1 Dashboard — account-integrity invariants

**"Is each account's stored balance honest?"**

L1 (read as *layer one* — the institution's foundational integrity
layer) is the set of SHOULD-constraints that hold *at every account,
every end-of-day*, regardless of what kind of money flow happens to
be passing through. It covers:

- **Drift** — stored balance equals the cumulative net of postings
- **Overdraft** — internal accounts don't go negative
- **Limit breach** — outbound transfers respect per-account caps
- **Pending / unbundled aging** — transactions don't get stuck past
  their rail's age cap
- **Supersession** — every rewrite of an append-only entry is
  accounted for

L1 is the layer the regulator audits. If L1 has open violations, the
institution's books disagree with themselves; nothing else matters
until L1 is clean.

### L2 Flow Tracing — per-chain transfer integrity

**"Does each money-movement flow through its chain correctly?"**

L2 (read as *layer two* — built on top of L1) traces individual
transfers through the institution's declared *chains* — the sequences
of legs that a money movement is supposed to fire as it crosses
internal and external systems. It answers questions like:

- Did every required leg of this transfer actually post?
- Did the chain children agree on which parent they belong to?
- Are XOR groups (mutually-exclusive leg sets) satisfied?
- Did the aggregator rail pick this leg up into a bundle on time?

L2 violations are about PATHS, not POSITIONS. An L1-clean ledger
can still have L2 violations — every account agrees with itself but
the transfer chain dropped a leg somewhere.

### Investigation — AML question shapes

**"Who's moving money in patterns we want a closer look at?"**

The investigation app answers compliance / anti-money-laundering
questions about BEHAVIOR, not integrity:

- **Recipient fanout** — which accounts send to unusually many
  distinct counterparties?
- **Anomaly windows** — which account-pair flows spike outside
  their rolling z-score baseline?
- **Money trail** — recursively walk a transfer's parent chain to
  see where it originated and where it's heading.
- **Account network** — visualize the directed graph of who pays
  whom across the institution.

### Executives — program-health scope

**"Is the reconciliation program working overall?"**

The executives app is the top-line readout: are L1 violations trending
down, is the account-coverage % adequate, is transaction volume
healthy, what's the net money moved this period. Built for monthly
review meetings, not minute-by-minute ops.

### App Info — per-dashboard health canary

Every dashboard has an *App Info* sheet at the end of its tab strip.
It shows per-matview row counts + the last refresh timestamp + a
deploy stamp (git SHA + ISO time). If a dashboard sheet renders blank
and you don't know why, **App Info is the diagnostic ladder's first
rung**: if the matview row count for the relevant invariant is zero,
the SQL is dry; if it's positive but the sheet still shows nothing,
the issue is in the visual binding. If `last_refresh_at` is older
than the most recent ETL load, the matviews are stale.

## Core data-model terms

### Account

A single ledger position. Has an `account_id`, an `account_name`, an
`account_role` (what kind of position — e.g. CustomerSubledger,
CustomerLedger, MerchantDDA) and a `scope` of either `internal` (the
institution's own books) or `external` (banks, payment networks, the
fed). The L1 invariants apply to internal accounts only — external
accounts are EXPECTED to behave however they behave; the institution
is responsible for keeping its own house in order.

### Account role

A semantic label that groups accounts by what they do, not by who
owns them. `CustomerSubledger` is a role; an institution may have
millions of accounts in that role. The dashboards aggregate + filter
by role because that's how the operator thinks: "show me drift on
the CustomerSubledger accounts" is a useful question; "show me drift
on account `cust-019` specifically" is the drill.

### Account scope: internal vs external

`internal` = the institution's own books. The L1 SHOULD-constraints
apply here.
`external` = counterparty books (banks, payment networks, the fed).
Excluded from L1 invariants because the institution can't audit
what someone else's ledger says.

### Parent / leaf accounts

A parent account is a control account whose stored balance is
DEFINED as the sum of its children's stored balances. A leaf
account has no children — its stored balance is just a number that
gets emitted directly. L1 drift on a leaf account means "my postings
disagree with my stored balance"; L1 drift on a parent means "my
children disagree with my rollup". The Drift sheet has separate
tables for each.

### Transaction

A single money-movement leg with an `account_id`, an `amount` (in
integer cents — never floats), a `signed_amount` (+ in, − out), a
`status` (Pending / Posted / Failed), a `posted_at` timestamp, a
`balance_date` (the business day the leg counts against) and JSON
metadata that varies by rail.

### Transfer

A logical event that may comprise multiple transactions. All
transactions sharing a `transfer_id` are legs of one transfer; a
non-failed multi-leg transfer's legs net to zero by construction.

### Rail

A named family of transfers — ACH, wire, check, merchant card, on-us
internal transfer. Each rail carries its own configuration: a
`max_pending_age` cap (how long a Pending leg may sit before it's
considered stuck), a `max_unbundled_age` cap (how long a Posted leg
may wait for the aggregator), metadata-key requirements and so on.

### Chain

A declared sequence of leg patterns that a particular kind of
transfer is supposed to fire. "Customer pays merchant via ACH" might
be a 4-leg chain: debit customer DDA → credit ACH origination
clearing → debit ACH settlement → credit merchant DDA. The L2 Flow
Tracing dashboard checks that real transfers actually fire all the
required legs.

### Template

A blueprint for what a Transfer's metadata + leg structure should
look like. Each Transfer in the system claims a `template` (in the
metadata); L2 Flow Tracing verifies the claim against the template's
declared shape.

### Matview / materialized view

A SQL view whose results are stored in a regular table and refreshed
on demand. Every L1 invariant is a matview — the SQL computes the
violation set, the dashboard reads from the table. Matviews refresh
on every ETL load; ad-hoc dashboard hits do NOT trigger a refresh,
so if a matview's `last_refresh_at` (visible on the App Info sheet)
predates the most recent posting, the dashboard is showing yesterday's
state.

## Cross-cutting concepts

### Carry-forward / sparse cadence

Some institutions report a daily balance for every account every day
("dense cadence"); others report only when something changed
("sparse cadence"). On sparse-cadence accounts, the dashboard's
*effective balance* on a non-emit day is **carried forward** from the
prior emit — that's what the institution would say if asked. Drift
on a `source='carried'` row means a posting happened on a non-emit
day and the carried balance doesn't account for it.

### Empty-state = teaching moment

Every dashboard sheet has an honest empty-state. Zero rows on the
Drift sheet means *every account agrees with its postings today*. It
does not mean "the dashboard is broken". The empty-state copy on each
sheet, and the **What "no rows" means** section of each handbook page,
distinguish "all clean" from "filter too narrow" from "matview stale".

### Cross-app drill

A click that takes you from one dashboard's sheet to another
dashboard's sheet with a filter pre-applied. Left-clicks generally go
*back toward the source* (e.g. clicking a Drift row drills to the
Daily Statement narrative for that account-day); right-clicks
generally go *deeper into the data* (e.g. right-click → *View
Transactions* opens the raw leg list).

## A note on the *other* L1 / L2 / L3

Inside the codebase, "L1 / L2 / L3" also names a three-layer
**architectural** split (persona-blind primitives / per-app domain
assembly / persona-flavored SQL + theme). That's a developer concept,
not an operator concept. If you hear someone say "L1 invariant" they
mean the account-integrity dashboard; if you hear them say "the L1
tree primitive" they mean a Python dataclass. The handbook only uses
the operator meaning.
