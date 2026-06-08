# L2 Flow Tracing

> **What this sheet teaches.** An introduction to the L2 ([Flow Tracing](../_glossary.md#l2-flow-tracing--per-chain-transfer-integrity)) dashboard and its five explorer tabs. Use this page to orient yourself before drilling into Rails, Chains, Transfer Templates, or L2 Exceptions.

## What you're looking at

You're on the Getting Started tab — the dashboard's entry point. Above you'll see tabs for the four sheets where the real work happens: *Rails* (transactions explorer), *Chains* (parent-child firing relationships), *Transfer Templates* (multi-leg flow), *L2 Exceptions* (hygiene violations), and *Info* (diagnostic health). Below, two text blocks orient you: the first explains what L2 Flow Tracing measures; the second describes the specific L2 instance your dashboard is connected to.

## How to read the numbers

This sheet carries no tables or KPIs — it's a welcome page. The L1 dashboard answers "are my postings internally consistent?" One step up: L2 Flow Tracing answers "is my L2 declaration alive?" — every Rail, every Chain, every TransferTemplate, every LimitSchedule the L2 instance declares should produce activity in the runtime data. When it doesn't, that's an L2 hygiene problem, not an L1 ledger problem.

The L2 Instance block below shows a description you'll see different text here depending on which L2 instance this dashboard is connected to — it describes the specific transfer shapes and business rules your institution declared.

## Common patterns

N/A — this is an orientation page, not an analysis sheet.

## What "no rows" means

N/A — this page always renders the same welcome content.

## Cross-sheet drills

- **Tab navigation** (click any tab). Jump directly to *Rails*, *Chains*, *Transfer Templates*, *L2 Exceptions*, or *Info*.

## Related handbook pages

- [Rails — Transactions Explorer](rails.md) — drill here to inspect individual legs by date, rail, status, or metadata.
- [Chains — Per-Instance Explorer](chains.md) — drill here to inspect parent-child firing relationships and completion status.
- [Transfer Templates — Multi-Leg Flow](transfer-templates.md) — drill here to visualize template flow as a Sankey and validate balance.
- [L2 Exceptions](l2-exceptions.md) — drill here to triage the six L2 hygiene violations unified into one view.

---

*First time here? See the [Vocabulary](../_glossary.md) for `L2`, `chain`, `rail`, `template`, `matview`, and the other project-specific terms.*
