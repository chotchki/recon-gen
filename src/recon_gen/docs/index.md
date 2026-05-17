# Recon Generator

*Programmatic AWS QuickSight analysis generator for financial
reporting. Currently rendered against
**{{ vocab.institution.name }}** ({{ l2_instance_name }}).*

Ships **four independent QuickSight apps** — L1 Reconciliation,
L2 Flow Tracing, Investigation, Executives — all L2-fed off one
institution YAML. Different audiences open different dashboards;
this site funnels each audience to the right one.

## Pick your role

The fastest way in. Each role page tells you which dashboard is
yours, what to read first, what concepts to ground, and what *not*
to spend time learning. Start here:

- **[For the operator](for-your-role/operator.md)** — daily
  reconciliation. L1 is your primary surface; L2 Flow Tracing is
  your second tab when an L1 trace ends with "but why is this
  happening every day?".
- **[For the integrator](for-your-role/integrator.md)** — owns
  the institution's L2 YAML. L2 Flow Tracing proves your
  declarations match runtime reality; L2 Hygiene Exceptions is
  your backlog.
- **[For the ETL engineer](for-your-role/etl-engineer.md)** —
  owns the projection from upstream systems into the two shared
  base tables. The L1 dashboard is your debug surface for silent
  load bugs.
- **[For the executive](for-your-role/executive.md)** — Money
  Moved / Transaction Volume / Account Coverage at weekly +
  monthly + quarterly cadences. Aggregate questions, no triage.
- **[For the compliance analyst](for-your-role/compliance-analyst.md)**
  — AML / SAR investigations. Question-shaped Investigation
  dashboard: Recipient Fanout, Volume Anomalies, Money Trail,
  Account Network.

## If you don't fit a role

The library shelves the role pages curate from. Jump in directly
when you know what you're after.

- **[Concepts](concepts/index.md)** — banking primitives + L2
  model primitives. Every reader benefits from grounding in
  double-entry, escrow / sweep / vouchering, and the L2 nouns
  (Account / Rail / Chain / TransferTemplate / LimitSchedule).
- **[Walkthroughs](walkthroughs/index.md)** — task recipes.
  "How do I X?" / "Where does this row lead?" / "Which sheet
  answers Y?". Bucketed by L1 sheets, Investigation, ETL, and
  Customization.
- **[Reference](reference/index.md)** — per-app structural
  handbooks (L1, L2 Flow Tracing, Investigation, Executives, ETL,
  Customization), plus the [Schema v6](Schema_v6.md) data feed
  contract and the [L1 Invariants](L1_Invariants.md) catalog.
- **[Demo Institution Tour](scenario/index.md)** — the L2 model
  rendered for the demo institution: chart of accounts, rails,
  transfer templates, chains, limit schedules.
- **[API Reference](api/index.md)** — for building a custom
  dashboard on the typed tree primitives.
