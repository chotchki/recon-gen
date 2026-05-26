# For the ETL engineer

*Audience — the engineer who owns the projection from upstream
source systems into the two shared base tables at
**{{ vocab.institution.name }}**.*

## What you do today

You run a load — nightly batch, hourly micro-batch, or streaming
near-real-time, depending on the upstream system — and the data
lands somewhere downstream that operators / executives /
compliance look at. When something goes wrong, the symptom is
rarely "ETL failed" (that's the easy case, which alerts catch);
it's usually "the dashboard showed wrong numbers" or "we have
drift on this account" or "this transfer is missing its
counterpart leg."

The hard case is silent: the load ran, the row counts look right,
but a metadata key got left blank, an external counterparty's leg
didn't make it across, or a force-posted transfer wasn't tagged
as such. The downstream sheets render as exceptions; the
operators escalate; you reverse-engineer which load step was
responsible.

## What this tool does differently

Every dashboard reads off two tables: `transactions` (one row per
posting leg) and `daily_balances` (one row per account-day). Every
sheet — every L1 invariant, every L2 hygiene check, every
Investigation question — projects off those two. Your ETL contract
is a stable, narrow column shape ([Schema v6](../Schema_v6.md));
your debug surface is the same Today's Exceptions / Daily
Statement / Transactions chain the operators use.

When a load goes silently wrong, the L1 dashboard surfaces it as
a specific row on a specific sheet. The check_type names the
class of failure — and tells you which load step to audit:

- Drift
- Overdraft
- Limit Breach
- Stuck Pending
- Stuck Unbundled
- Supersession

**The first time you ship a load fix and watch the corresponding
row disappear from Today's Exceptions on tomorrow's run — that's
the proof your ETL is observable, not just running.**

## What we are *not* asking you to learn

- **Not a new schema.** The two base tables are the contract; if
  you can write to them in the right shape, every dashboard
  works.
- **Not the dashboard internals.** You don't author visuals or
  filters or drills. The dashboards are generated from the L2
  YAML by the integrator role; you're upstream of that.
- **Not every L2 primitive.** You need to know which
  metadata keys your loads are responsible for setting. The L2
  YAML names them; the integrator can tell you which ones are
  load-time vs. set-by-downstream-process.

## How to start

1. Read the
   [Data Integration handbook](../handbook/etl.md). It covers the
   two-table contract, the metadata keys, the matview refresh
   sequence, and the idempotency / supersession rules that let
   you safely re-run a load.
2. Read [Schema v6 — Data Feed Contract](../Schema_v6.md). It's
   the column-by-column reference for the two base tables. Treat
   it as the authoritative source for any "what type does this
   field need to be?" question.
3. Walk the recipes in
   [Walkthroughs → ETL](../walkthroughs/index.md#etl) in this
   order:
    - [How do I populate transactions?](../walkthroughs/etl/how-do-i-populate-transactions.md)
    - [How do I populate daily_balances?](../walkthroughs/etl/how-do-i-populate-daily-balances.md)
    - [How do I validate a single account-day?](../walkthroughs/etl/how-do-i-validate-a-single-account-day.md)
    - [How do I prove my ETL is working?](../walkthroughs/etl/how-do-i-prove-my-etl-is-working.md)
    - [How do I tag a force-posted transfer?](../walkthroughs/etl/how-do-i-tag-a-force-posted-transfer.md)
    - [How do I add a metadata key?](../walkthroughs/etl/how-do-i-add-a-metadata-key.md)
4. Bookmark
   [What to do when demo passes but prod fails?](../walkthroughs/etl/what-do-i-do-when-demo-passes-but-prod-fails.md).
   It's the canonical first-stop for "the loader works locally,
   but doesn't on real data" debug arcs.

## Your daily routine

1. Verify the previous business day's load landed: open the
   **Info** sheet on any deployed dashboard. It carries a deploy
   stamp + matview row counts. Counts at zero or unchanged from
   yesterday means your load didn't run (or ran but landed in the
   wrong schema).
2. Spot-check a known busy account on
   [Daily Statement](../walkthroughs/l1/daily-statement.md). The
   per-(account, day) walk should show your loaded postings —
   opening + flow + closing should all reconcile.
3. Watch
   [Today's Exceptions](../walkthroughs/l1/todays-exceptions.md)
   for new violations that look ETL-shaped (drift on a stable
   account, missing counterparty leg, force-posted-without-
   internal-catchup, supersession trail).
4. After any schema or metadata change, refresh **all**
   `{{ l2_instance_name }}_*` matviews. The L1 invariant views don't auto-
   refresh; stale matviews are a frequent cause of "the
   dashboard shows yesterday's state."

## The matview-refresh contract

Per the
[Data Integration handbook](../handbook/etl.md), the refresh
sequence is dependency-ordered: base tables first, then
`{{ l2_instance_name }}_drift` / `{{ l2_instance_name }}_overdraft` / `{{ l2_instance_name }}_limit_breach`
/ `{{ l2_instance_name }}_stuck_pending` / `{{ l2_instance_name }}_stuck_unbundled`, then the
Investigation matviews
(`{{ l2_instance_name }}_inv_pair_rolling_anomalies`,
`{{ l2_instance_name }}_inv_money_trail_edges`), then the daily-statement
rollups. The CLI's `refresh_matviews_sql(l2_instance)` helper
emits the right ordering for any L2 instance — call it from your
load orchestrator after every transactions / daily_balances
write.

## The concepts you'll want grounded

- [Double-entry posting](../concepts/accounting/double-entry.md) — the
  conservation invariant your loads must preserve. Any leg you
  drop, double-load, or sign-flip surfaces as drift.
- [Sweep / net / settle](../concepts/accounting/sweep-net-settle.md) — the
  daily cycle behind aggregating rails. Impacts how you batch
  your loads (you can't "load Monday's sweep on Wednesday"
  without confusing the matviews).
- [Open vs. closed loop](../concepts/accounting/open-vs-closed-loop.md) —
  the system-boundary question. Closed-loop legs you load both
  sides; open-loop you load the internal side and wait for the
  external counterparty's confirmation.
- [Eventual consistency](../concepts/accounting/eventual-consistency.md) —
  why a stuck-pending row on a fresh load is not necessarily a
  bug; aging-bucket bands tell you when it becomes one.

## What "good" looks like

After a few weeks of running the load against the dashboard:

- You're catching load anomalies from the L1 surface within a day
  of them happening, not from end-of-month reconciliation.
- New metadata keys you add show up immediately on the relevant
  L2 Flow Tracing sheets.
- When operators ask "is the load OK?" you answer from the **Info**
  sheet's matview row counts in 10 seconds.
- Load reruns + supersession traces produce zero net change on
  the L1 dashboard (the supersession audit catches what changed,
  the conservation check confirms net delta = 0).

That's the acceptance bar. The ETL works when
{{ vocab.institution.acronym }}'s downstream surfaces stay
in-sync with the upstream feed without manual data-team
intervention.
