# How do I map my production database to the two base tables?

*Customization walkthrough — Developer / Product Owner. Foundational. Read this first.*

## The story

You've stood up the demo, clicked through the dashboards, and
decided you want this product against your own data. Now you're
sitting in front of your bank's production database and asking
the load-bearing question: **how much work is this, actually?**

The honest answer: the visual layer (32+ datasets across four
L2-fed apps, the L1 invariant surface, drill-downs, filters,
theming) binds to a contract that is two tables wide. Everything
you see in the demo reads from `{{ l2_instance_name }}_transactions` and
`{{ l2_instance_name }}_daily_balances` (where `{{ l2_instance_name }}` is your L2 instance
name). If you can land your data into those two shapes — once, by
your morning cut — every dashboard works without further plumbing
on the dashboard side.

The work that *isn't* trivial is the upstream ETL projection
itself: deciding which of your source tables map to a leg in
`{{ l2_instance_name }}_transactions`, getting the sign convention right on
`amount_money`, populating `transfer_parent_id` for chained
transfers, tagging force-posts. That work belongs to your data
integration team and lives in the
[Data Integration Handbook](../../handbook/etl.md). This
walkthrough is the *strategic* read for the product owner: what
your source system needs to expose, what shape the contract takes,
and the signals that you have a workable fit.

## The question

"My bank has a core banking system, a card processor feed, a
Fed statement file, and an in-house sweep engine. Can I get
**this product** running on **that data**, and what do I need
to know before I commit?"

## Where to look

Two reference points before you write a line of mapping code:

- **[Schema_v6.md → The minimum viable feed](../../Schema_v6.md#etl-contract-minimum-viable-feed)** —
  the 11 mandatory columns on `{{ l2_instance_name }}_transactions` + 6 on
  `{{ l2_instance_name }}_daily_balances`. Read these first. Anything beyond
  the minimum is conditional and can wait for v2.
- **`common/l2/schema.py::emit_schema(l2_instance)`** — the
  source of truth for the prefixed DDL. Call it from Python to
  see the full rendered output for your L2 instance, including
  base tables, Current* views, computed-balance helpers, and L1
  invariant views (or apply directly via `quicksight-gen
  schema apply -c run/config.yaml --execute`).

The contract is deliberately small. If you find yourself
proposing a third base table, push back: every persona we've
shipped — L1 operator, L2 integrator, Investigation analyst,
Executive scorecard — reads from these same two tables.

## What you'll see in the demo

After the demo flow (`quicksight-gen schema apply --execute &&
quicksight-gen data apply --execute && quicksight-gen data
refresh --execute`), your demo database (Postgres or Oracle,
dispatched off `dialect:`) holds:

- **`{{ l2_instance_name }}_transactions`** — every money-movement leg, one
  row per leg. Multiple legs of one financial event share a
  `transfer_id` and net to zero (the double-entry invariant).
- **`{{ l2_instance_name }}_daily_balances`** — one row per `(account_id,
  business_day_start)`. The `money` column is what your ETL
  writes; the L1 Drift view recomputes `SUM(amount_money)` and
  surfaces the delta.
- **A handful of L1 invariant views** under the same prefix —
  drift, overdraft, limit_breach, stuck_pending, stuck_unbundled,
  expected_eod_balance_breach. These are computed from the two
  base tables; you don't write to them.

That's it. No `pr_sales`, no `pr_settlements`, no
`ledger_postings`, no per-persona staging tables, no separate AR
dimension tables. Every exception check, every drill-down, every
aging bucket reads from `{{ l2_instance_name }}_transactions` and
`{{ l2_instance_name }}_daily_balances`.

## What it means

For your source-system-to-base-table mapping, three patterns
cover the common cases:

### Pattern 1 — Core banking → `{{ l2_instance_name }}_transactions` + `{{ l2_instance_name }}_daily_balances`

Your core banking system has a `gl_postings` (or equivalent)
detail table — one row per posting leg already. This is the
natural match for `{{ l2_instance_name }}_transactions`. Your nightly EOD
`account_balance` snapshot maps to `{{ l2_instance_name }}_daily_balances`.
Most of the projection is a column rename plus the sign-convention
conversion.

This is the canonical case. The
[How do I populate `{{ l2_instance_name }}_transactions` from my core banking system?](../etl/how-do-i-populate-transactions.md)
walkthrough has the full SQL projection.

### Pattern 2 — Card processor / external feed → `{{ l2_instance_name }}_transactions` (`external_txn`)

Your card processor sends a daily settlement file. Each row is
the processor's view of money landing in your account. These
become `{{ l2_instance_name }}_transactions` rows with
`rail_name = 'external_txn'`,
`origin = 'ExternalForcePosted'`, and a populated
`external_system` (e.g., `BankSync`, `PaymentHub`).

You don't need a separate table for these. The L1 drift split
between bank-initiated activity and force-posted activity reads
these rows correctly via the `origin` column, and Investigation's
Money Trail walks them via `transfer_parent_id`.

### Pattern 3 — Sweep engine / internal transfer log → `{{ l2_instance_name }}_transactions` (multi-leg)

Your CMS sweep engine emits one record per sweep operation —
"move $X from sub-ledger A to concentration master B". That
single record becomes **two** `{{ l2_instance_name }}_transactions` rows (a
debit leg on A, a credit leg on B) sharing one `transfer_id`. The
legs must net to zero. The L1 drift checks read this directly.

Multi-leg projection is where most ETL teams get tripped up.
Read [How do I prove my ETL is working before going live?](../etl/how-do-i-prove-my-etl-is-working.md)
— Invariant 1 (every transfer's legs net to zero) is the
universal pre-flight check that catches multi-leg projection
bugs immediately.

## Drilling in

A few decisions to surface explicitly with your team before
you commit:

- **Sign convention.** `amount_money > 0` means money IN to
  the account; `< 0` means money OUT. If your upstream uses
  the opposite convention (some core systems use bank's-
  bookkeeping where debits are positive on asset accounts and
  negative on liability accounts), you flip the sign in the
  ETL projection — *not* in a downstream view. Every dashboard
  check assumes our sign convention; flipping at the projection
  boundary keeps that assumption honest everywhere downstream.
- **`business_day_start` is denormalized from `posting`
  deliberately.** The dashboard datasets do fast date-range scans
  on `business_day_start` — populating it as a separate column
  (rather than expression-casting `posting::date` on every
  query) is a deliberate redundancy for query speed. Your ETL
  writes one extra column; the dashboard reads it without a
  cast.
- **`account_role` describes role, not structural level.** Six
  canonical values: `gl_control`, `dda`, `merchant_dda`,
  `external_counter`, `concentration_master`, `funds_pool`.
  Structural level (control vs. sub-ledger) derives from
  `account_parent_role`. Don't pack the level into the role
  field — see
  [Schema_v6.md → Canonical account_type values](../../Schema_v6.md#table-1-prefix_transactions).
- **`metadata` is the extension point, not a schema migration.**
  Your bank wants to surface a custom field (a transaction
  reference number, a regulatory flag, a per-merchant tier
  code). Add it as a JSON key in `metadata`; read it from
  dataset SQL via `JSON_VALUE`. No DDL change, no rebuild. See
  [How do I add a metadata key without breaking the dashboards?](../etl/how-do-i-add-a-metadata-key.md)
  for the ETL-side write pattern; the dashboard-side read
  pattern is in the
  [How do I add an app-specific metadata key?](how-do-i-add-a-metadata-key.md)
  walkthrough.

## Next step

Once you've decided this product fits your data:

1. **Stand up the schema.** Call
   `emit_schema(l2_instance, dialect=...)` from `common.l2.schema` to
   render the per-prefix DDL — base tables
   (`{{ l2_instance_name }}_transactions` / `{{ l2_instance_name }}_daily_balances`), Current*
   views, computed-balance helpers, and L1 invariant matviews.
   Apply it to a dev Postgres or Oracle instance directly, or chain
   `quicksight-gen schema apply -c run/config.yaml --execute &&
   quicksight-gen data apply -c run/config.yaml --execute &&
   quicksight-gen data refresh -c run/config.yaml --execute` to
   land the schema + seed + matviews (dispatches off the
   `dialect:` field on `config.yaml`).
2. **Hand the projection task to your data integration team.**
   The
   [Data Integration Handbook](../../handbook/etl.md) is
   their entry point. Walk them through the
   minimum-viable-feed columns, the sign convention, and the
   pre-flight invariants. Their first deliverable is one
   day's load against `{{ l2_instance_name }}_transactions` +
   `{{ l2_instance_name }}_daily_balances`.
3. **Validate with the dashboards.** Once a slice is loaded,
   open the L1 Reconciliation Dashboard's Today's Exceptions
   sheet. KPI at 0 with no detail rows means the feed landed
   cleanly. KPIs spiking unexpectedly is the signal to walk
   [What do I do when the demo passes but my prod data fails?](../etl/what-do-i-do-when-demo-passes-but-prod-fails.md)
   with your ETL team.
4. **Configure the deploy for your AWS account.** Once the
   data side works, the deployment side is one config file
   away — that's the
   [How do I configure the deploy?](how-do-i-configure-the-deploy.md)
   walkthrough.

## Related walkthroughs

- [Data Integration Handbook → How do I populate transactions?](../etl/how-do-i-populate-transactions.md) —
  the ETL-engineer view: the actual SQL projection from
  `core_banking.gl_postings` to `{{ l2_instance_name }}_transactions`.
- [Data Integration Handbook → How do I prove my ETL is working?](../etl/how-do-i-prove-my-etl-is-working.md) —
  the universal pre-flight invariants (net-zero, drift-recompute,
  parent-chain integrity) your ETL team runs before declaring a
  load complete.
- [Data Integration Handbook → How do I add a metadata key?](../etl/how-do-i-add-a-metadata-key.md) —
  the ETL-engineer view of metadata key extension. The
  customization counterpart (dashboard-side read pattern) is
  [How do I add an app-specific metadata key?](how-do-i-add-a-metadata-key.md).
- [Schema_v6 → Getting Started for Data Teams](../../Schema_v6.md#etl-contract-minimum-viable-feed) —
  the column-level contract, including the per-column failure
  modes ("if you skip this, what dashboard breaks?").
