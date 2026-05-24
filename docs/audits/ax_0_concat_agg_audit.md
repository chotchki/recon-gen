# AX.0 — `concat_agg` SQLite routing audit

**Status:** AX.0 complete (audit + spike) — 2026-05-23.
**Branch:** `ax-promote-l2-shape-invariants`.

## TL;DR

**No shim work needed.** The `concat_agg(column_expr, separator,
dialect)` helper in `src/recon_gen/common/sql/dialect.py:663-691`
already routes SQLite to `GROUP_CONCAT(col, ',')` — built into the
SQLite stdlib since 3.5.4. All 4 target matviews (including
`xor_group_violation` and `multi_xor_violation` which use
`concat_agg` for their `fired_rails` / `fired_children` columns)
refresh cleanly against an in-memory SQLite harness. AX.1–4 can
proceed directly; no `_register_sqlite_aggregates` extension
required.

## End-to-end verification

Ran against fresh in-memory SQLite + `tests/l2/spec_example.yaml`:

```python
import sqlite3
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
# … register aggregates, emit schema, refresh matviews …
```

Result — all 4 target matviews exist and queryable post-refresh:

| Matview | Row count | Column shape |
|---|---|---|
| `spec_example_chain_parent_disagreement` | 0 | `(transfer_id, child_template_name, business_day, distinct_parent_count, parent_transfer_id_min, parent_transfer_id_max)` |
| `spec_example_xor_group_violation` | 0 | `(transfer_id, template_name, xor_group_index, firing_count, fired_rails, business_day)` |
| `spec_example_fan_in_disagreement` | 0 | `(child_transfer_id, chain_parent_name, child_template_name, parent_count, expected_parent_count, disagreement_kind, business_day)` |
| `spec_example_multi_xor_violation` | 0 | `(parent_transfer_id, parent_rail_or_template_name, child_count, fired_children, disagreement_kind, business_day)` |

Zero rows is expected (the bundled `spec_example.yaml` doesn't plant
any of these violations; the AX generators will manufacture them).
What this verifies is **the SQL itself executes** — the
`GROUP_CONCAT` route on the SQLite path doesn't trip an
`OperationalError`.

## Existing SQLite aggregate registry

`src/recon_gen/common/db.py::_register_sqlite_aggregates` currently
registers only one aggregate:

```python
conn.create_aggregate("STDDEV_SAMP", 1, _StddevSampAggregate)
```

`STDDEV_SAMP` is needed for the `inv_pair_rolling_anomalies`
matview (Phase AT). `GROUP_CONCAT` is built-in to SQLite stdlib
(since 3.5.4 — 2007); no registration needed.

## Identity tuple refinements (informing AX.1–4 implementation)

The runtime column inspection refined the plan's identity tuples
slightly:

- **`chain_parent_disagreement`** — plan said
  `(transfer_id, child_template_name)`. Confirmed by the matview's
  GROUP BY. Other columns (`distinct_parent_count`,
  `parent_transfer_id_{min,max}`) are diagnostic; not part of
  identity.

- **`xor_group_violation`** — plan said
  `(transfer_id, template_name, xor_group_index)`. Confirmed by the
  matview's GROUP BY. `firing_count` distinguishes
  missed (0) vs overlap (≥2); `fired_rails` is the comma-separated
  diagnostic.

- **`fan_in_disagreement`** — plan said
  `(child_transfer_id, disagreement_kind)`. Confirmed — the matview's
  output carries `disagreement_kind` ∈ {'orphan', 'missing', 'extra'}
  per the AB.6 refinement; the `(child_transfer_id, disagreement_kind)`
  pair is the natural key.

- **`multi_xor_violation`** — plan said
  `(parent_transfer_id, disagreement_kind)`. Confirmed — the matview's
  `disagreement_kind` ∈ {'missed', 'overlap'} discriminates between
  the two failure modes. `child_count` is diagnostic (0 for missed,
  ≥2 for overlap).

## What AX.1–4 still need

Per-matview implementation pattern (unchanged from the plan):

1. New `<Name>Invariant` class with `detect(conn) -> set[Violation]`
   that `SELECT`s the identity tuple from `<prefix>_<matview>` and
   wraps each row as `Violation.of(name, **identity_dict)`.

2. New `<Name>Generator` class(es) — one per discriminator value
   where the plant differs (e.g., `XorGroupViolation` needs separate
   generators for missed vs overlap because the transfer-leg
   construction differs). The reference template is
   `src/recon_gen/common/spine/expected_eod.py` for the
   single-variant case and `src/recon_gen/common/spine/stuck_pending.py`
   for the multi-discriminator case (pending vs unbundled live as
   separate modules).

3. Smart constructor `scenario_for(...)` per invariant that calls
   the L2 picker (`_pick_xor_missed_firing_inputs`, etc. from
   `common/l2/auto_scenario.py`).

4. Plant emit pattern — port the existing
   `_emit_<plant>_rows` helpers from `common/l2/seed.py` into
   `Transfer`-based `LedgerSimulation.emit` calls (the AT.3 shape).
   This keeps each generator dialect-aware (insert_tx detects the
   dbapi placeholder style per AT.5.b).

No SQL-side or aggregate-side blockers remain.
