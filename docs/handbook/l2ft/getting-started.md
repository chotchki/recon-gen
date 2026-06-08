# L2 Flow Tracing

> **What this dashboard is for.** The L2 ([Flow Tracing](../_glossary.md#l2-flow-tracing--per-chain-transfer-integrity)) dashboard answers "is my L2 declaration alive?" — every Rail, every Chain, every TransferTemplate the L2 instance declares should produce activity in the runtime data. When it doesn't, that's an L2 hygiene problem, not an L1 ledger problem.

## About this dashboard

You're looking at the dashboard's entry point. Below, four sheets let you inspect your L2 declarations and their runtime activity. The L1 dashboard (the account-integrity sibling) answers "are my postings internally consistent?" and catches ledger disagreement at the account level. L2 answers a different question: "do my declared transfer shapes fire as designed?" If L1 is clean but L2 shows violations, your postings agree with your stored balances, but the transfers themselves aren't flowing through their declared chains correctly.

The data model joins three pieces:

- **Declared L2 structure** — the Rails, Chains, and TransferTemplates you registered in the L2 instance YAML. These are static; they don't change except when you redeploy.
- **Runtime transactions** — the actual postings (legs) that fired. Matviews read from the `<prefix>_transactions` base table and its `current_transactions` view (which materializes max-Entry-per-ID so Entry supersession is transparent).
- **Hygiene violations** — six checks that detect "declaration vs runtime" mismatches: Chain Orphans (declared child didn't fire), Unmatched Rail Name (posting claims a rail that isn't declared), Dead Rails (declared rail has no activity), Dead Bundles Activity (declared bundle slot fired nothing), Dead Metadata (declared key has no values in the window), Dead Limit Schedules (declared limit is unused).

## Common workflows on this dashboard

### Investigating a recent L2 violation

A hygiene check surfaced a problem. Drill into *L2 Exceptions* to see which check kind flagged it. If it's "Chain Orphan" (a parent transfer fired but its child didn't), right-click the row to jump to *Chains* with the parent chain filtered. If it's "Dead Rails" (a declared rail has no postings in the window), jump to *Rails* to see what legs that rail DID fire or confirm it's empty.

### Exploring transfer flow for a specific chain or rail

Pick a start point: *Chains* to inspect parent-child firings by declared chain, or *Rails* to see individual legs by rail name. Both sheets have date-range filters (narrowing to a business-day window), metadata cascade dropdowns (drilling down to key=value pairs in the leg metadata), and completion-status filters (narrowing to Completed, Incomplete, or No Required Children for chains).

### Validating a TransferTemplate's multi-leg shape

The *Transfer Templates* sheet shows declared templates as flow Sankeys: account → template → account, with edges showing the debit flow in and credit flow out. Pick a single template to collapse the visual to that shape alone, then use the completion status filter (Complete / Imbalanced / Orphaned) to find templates whose legs don't balance or whose chain children didn't fire.

## Common patterns

### Clean L2 state

Zero rows on the *L2 Exceptions* sheet (or the other sheets with narrowed filters) means your declarations are firing as designed. Every declared rail has postings, every declared chain's parent-firing has its required children, and no leg claims a rail or metadata key that isn't declared. This is the steady-state expectation.

### Dead Rail or Dead Metadata

*L2 Exceptions* shows a "Dead Rails" or "Dead Metadata" violation. Switch to *Rails* and look at the date range and metadata filters — if the rail or metadata key has no activity in the current window but WAS active historically, the silence may be normal (no transfers of that kind lately). If the rail or key is declared but never fired, either the L2 YAML is stale (you declared it but it's no longer used) or integrator ETL never connected it.

### Chain Orphan

A parent transfer fired but one or more of its required children didn't. Drill to *Chains* with the parent chain name filtered. Look at the `Required Children Fired` vs `Required Children Declared` columns — the gap shows which children are missing. Check *Rails* to see if those child legs exist under a different rail name (misconfiguration) or aren't there at all (feed issue upstream).

## Where to start

This sheet is a welcome page. The real work happens on the four explorer sheets:

- **Rails** — the transactions explorer. Filter by date, rail name, status (Pending / Posted / Failed), bundle status (Bundled / Unbundled), and metadata key=value pairs. See every individual leg in your window.
- **Chains** — parent transfer firings. See which parents fired, how many of their required children showed up, and their completion status (Completed / Incomplete / No Required Children).
- **Transfer Templates** — multi-leg template flows. Visualize template shape as a Sankey, then drill down with filters for date, template name, completion status (Complete / Imbalanced / Orphaned), and metadata.
- **L2 Exceptions** — all six hygiene violations unified. See the count by check type, then right-click any row to drill into *Rails* or *Chains* with the entity pre-filtered.

Start with *L2 Exceptions* if you have violations to triage. Start with *Rails* if you want to explore a specific transfer or rail. Start with *Chains* if you're checking parent-child firing relationships.

## Cross-sheet drills

Every sheet in this dashboard can drill to the others:

- **L2 Exceptions table row → Rails** (right-click). Narrows the *Rails* sheet to the rail named in the violation's `entity_a` column (works for Dead Rails, Dead Metadata, Unmatched Rail violations).
- **L2 Exceptions table row → Chains** (right-click). Narrows the *Chains* sheet to the chain parent named in the violation's `entity_a` column (works for Chain Orphan violations).
- **Rails metadata value → (narrows within Rails)** (text-field input). Pick a Metadata Key, then type a value to narrow the transactions table to legs carrying that key=value pair in their JSON metadata.
- **Chains metadata value → (narrows within Chains)** (text-field input). Same metadata cascade as Rails: pick a key, type a value.
- **Transfer Templates metadata value → (narrows within Templates)** (text-field input). Same cascade applies to the Sankey + instance table together.

## Related handbook pages

- [Rails — Transactions Explorer](rails.md) — the transactions explorer sheet; drill here from L2 Exceptions or start here to inspect individual legs.
- [Chains — Per-Instance Explorer](chains.md) — parent-child firing relationships; cross-reference when L1 is clean but L2 shows Chain Orphans.
- [Transfer Templates — Multi-Leg Flow](transfer-templates.md) — template flow Sankey; use this to validate that template legs balance and chains complete.
- [L2 Exceptions](l2-exceptions.md) — the unified hygiene-violation sheet; the canonical place to triage L2 violations before drilling deeper.

---

*First time here? See the [Vocabulary](../_glossary.md) for `L2`, `chain`, `rail`, `template`, `matview`, and the other project-specific terms.*
