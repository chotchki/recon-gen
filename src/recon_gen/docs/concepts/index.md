# Concepts

Why the dashboards look the way they do. Two halves.

- **[Accounting](accounting/index.md)** — the banking primitives the L1
  invariants and the per-app sheets assume you already know (double-entry
  posting, escrow / suspense, sweep-net-settle, vouchering, eventual
  consistency, open vs. closed loop).
- **[L2 model](l2/index.md)** — the vocabulary an integrator uses to declare
  one institution's shape: Account / AccountTemplate / Rail / TransferTemplate
  / Chain / LimitSchedule. Each page takes ONE primitive on its own and draws
  it straight from the loaded L2 instance, so the diagram is YOUR data, not a
  stock picture.

Read these before the per-app reference if a term ("rail", "chain",
"supersession", "double-entry posting") is unfamiliar. Anyone touching the
dashboards or the L2 YAML for the first time (operators, integrators, ETL
engineers, executives) starts here. If you landed here cold, the [role
pages](../for-your-role/index.md) say which concepts each role should ground
first.
