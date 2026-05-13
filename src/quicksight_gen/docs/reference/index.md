# Reference

*If you arrived here directly, the [role pages](../for-your-role/index.md)
curate which handbooks each role uses day-to-day.*

Per-app structural reference — what each sheet shows, which dataset
backs it, which filters and drills are wired, and which L1 invariants
each row represents.

The intended user is the operator looking up "what does column X on
sheet Y mean" or the integrator validating that their L2 instance
produces the rows the dashboard expects.

## Pages

- [L1 Reconciliation Dashboard](../handbook/l1.md) — surfaces L1
  invariant violations from any L2 instance:
    - Drift
    - Overdraft
    - Limit Breach
    - Stuck Pending
    - Stuck Unbundled
    - Supersession Audit
- [L2 Flow Tracing](../handbook/l2_flow_tracing.md) — Rails, Chains,
  Transfer Templates, and L2 hygiene exceptions for the integrator
  validating their L2 instance against the SPEC.
- [Investigation](../handbook/investigation.md) — recipient fanout,
  volume anomalies, money-trail provenance, and account-network graphs
  for the compliance / AML triage team.
- [Install](install.md) — which PyPI extras to pick for your use
  case (emit-only / deploy / demo DB / audit PDF / docs build / full
  dev environment).
- [Self-hosting the dashboards (App 2)](self-host.md) — running the
  four apps as a self-hosted HTMX page server (no AWS account), what
  browser-side assets ship in the wheel, and the maintainer recipes
  for bumping a vendored dep or rebuilding the Tailwind stylesheet.
- [ETL — Data Integration](../handbook/etl.md) — for the engineer
  populating the two base tables from upstream systems.
- [Customization](../handbook/customization.md) — for the developer
  dropping the dashboards onto their own backend, brand, and AWS
  account.
- [Domain Model (SPEC)](../SPEC.md) — the canonical L1 / L2 / L3
  layer model: primitives, derivatives, system constraints, and the
  L2 institutional vocabulary every shipped app reads.
- [Schema v6 — Data Feed Contract](../Schema_v6.md) — column specs +
  metadata key catalog + ETL examples for the two base tables.
- [L1 Invariants](../L1_Invariants.md) — the formal SHOULD-violation
  matview definitions that every L1 dashboard sheet rolls up.
