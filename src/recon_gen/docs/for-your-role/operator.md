# For the operator

*Audience — reconciliation operator at **{{ vocab.institution.name }}**.
Primary user of the L1 Reconciliation Dashboard.*

## What you do today

You run an aggregated text report (or a spreadsheet, or a stack of
emails) every morning and cross-check the numbers against the
previous business day's bank statement. When something doesn't tie,
the answers you actually need — WHICH account drifted, whether
each transfer settled, where a posting's counterpart went —
aren't on the report you're holding.

So you escalate to whoever owns the data pipeline. They run a
query, send back a CSV, sometimes within the hour, sometimes by
end-of-day. By the time you hear back, the issue has often aged
another day. If the answer leads to another question ("OK but where
did THAT entry come from?"), it's another round-trip.

## What this tool does differently

Same underlying data, laid out so you can answer those follow-up
questions yourself. Every KPI on the **L1 Exceptions** sheet is one
class of violation and every detail row is a specific break — click
any cell and it drills straight to the underlying transactions.

The first trace you finish in under two minutes is one you used to
wait a day for. See it live on the
[spec_example dashboards](https://recon-gen-spec.hotchkiss.io/).

## What we are NOT asking you to learn

- **Not SQL.** You won't write queries. The L1 invariant matviews
  do the querying; you're reading the results.
- **Not a new accounting framework.** The dashboard uses the
  industry-standard vocabulary you already know — debits, credits,
  balances, transfers, drift, aging.
- **Not the Investigation or Executives dashboards.** Those belong
  to compliance / leadership and answer different questions. Skim
  them, don't study them.

## What we ARE asking you to learn

Two dashboards, not one. The L1 Reconciliation Dashboard is your
day-to-day surface — but L1 violations are downstream symptoms.
When the symptom is a real one and you need to know WHY, the
**L2 Flow Tracing dashboard** is the next stop.

- **L1 answers "did the invariants hold?"** — drift, overdraft,
  limit breach, stuck pending / unbundled, supersession, today's
  exceptions. These are SHOULD-constraints on the runtime data.
  L1 is where you spend 90% of your time.
- **L2 Flow Tracing answers "is the institution's L2 declaration
  alive?"** — every Rail, every Chain, every Transfer Template,
  every Limit Schedule the L2 instance declares should produce
  activity in the runtime data. The **L2 Exceptions** sheet
  surfaces declarations that are dead, mismatched or orphaned —
  and these failures often manifest one layer down as the L1
  exceptions you saw on L1 Exceptions.

When an L1 trace ends with "but why is this happening EVERY
day?", flip to L2 Flow Tracing. A spike in *Dead Rails* or *Chain
Orphans* under L2 Exceptions tells you the integrator's L2
declaration has drifted from runtime — not a transient runtime
hiccup, but a structural problem that needs an integrator handoff.

## How to start

1. Read the [L1 Reconciliation Dashboard handbook](../handbook/l1.md).
   It covers the 11 sheets in display order, the analyst journey,
   and the L2-instance contract that drives every prose block on
   each sheet.
2. Walk through the
   [L1 Exceptions walkthrough](../walkthroughs/l1/exceptions.md).
   It's the morning landing page — start there every day.
3. Walk through
   [Drift](../walkthroughs/l1/drift.md) and
   [Drift Timelines](../walkthroughs/l1/drift-timelines.md). Drift
   is the most common L1 violation; understanding it cold makes
   every other check easier.
4. Walk through the
   [Daily Statement](../walkthroughs/l1/daily-statement.md) +
   [Transactions](../walkthroughs/l1/transactions.md) walkthroughs.
   These are the canonical drill destinations for any row — every
   trace ends at a Daily Statement page or a raw posting ledger.
5. Bookmark the
   [L1 Invariants reference](../L1_Invariants.md). Every check on
   L1 Exceptions ties back to one of these SHOULD-constraints;
   when you see an unfamiliar `check_type`, look it up here.

## The drill chain you'll use every day

*L1 Exceptions → per-invariant narrowing → Daily Statement →
Transactions.*

- **Left-click an `account_id`** on any L1 Exceptions row →
  narrows the per-invariant sheets (Drift / Overdraft / Limit
  Breach) to that account.
- **Right-click → "View Daily Statement"** → opens the
  per-account-day walk: opening balance, debits, credits, closing
  stored, drift KPI, every-leg detail table.
- From any per-invariant detail row, **right-click → "View Daily
  Statement"** for the same drill-forward.
- From any Daily Statement leg, **right-click → "View Transactions"**
  for the raw posting ledger filtered to that transfer's legs.

Every sheet is also filterable independently — date range pickers
(Date From / Date To, default 7 days), per-sheet category
dropdowns (Account, Account Role, Transfer Type, Rail, Status,
Origin), and parameter pickers on Daily Statement (Account +
Business Day).

## The concepts you'll want grounded

Each is a ~5 minute read. Come back to them as the walkthroughs
reference them; don't front-load all of them.

- [Double-entry posting](../concepts/accounting/double-entry.md) — the
  conservation invariant every L1 check ultimately rests on.
- [Eventual consistency](../concepts/accounting/eventual-consistency.md) —
  why "in-flight" and "stuck" are different bands of the same
  spectrum, and how the aging-watch sheets surface it.
- [Escrow with reversal](../concepts/accounting/escrow-with-reversal.md) —
  the suspense-account lifecycle behind most stuck-pending
  exceptions.
- [Sweep / net / settle](../concepts/accounting/sweep-net-settle.md) — why
  daily aggregating accounts (sweep / clearing / suspense) should
  end at zero EOD, and what an Expected EOD Balance violation
  means.

## What "good" looks like

After a few weeks of daily use:

- You're opening fewer pipeline tickets for traces.
- You're finding exceptions before lunch instead of the next
  morning.
- When you do escalate, you hand over a specific
  `transfer_id` / `account_id` + business day, not a vague
  "something's off in the GL".
- You're comfortable saying "the dashboard answered this" without
  needing the data team to confirm.

That's the acceptance bar. The tool works when {{ vocab.institution.acronym }}
trusts it to carry the morning routine.
