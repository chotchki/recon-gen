# ETL Hook — Bulk Helpers Reference

*Integrator reference for the Python ETL hook surface. Covers the
canonical column tuples, the `bulk_insert_tx` / `bulk_insert_balance`
helpers, the `metadata.source` contract, and the
`cfg.app2.etl_hook` ⇄ standalone-mode boundary. Companion to
[Data Integration handbook](etl.md) and
[Schema v6](../Schema_v6.md).*

## The contract in one sentence

Your job: emit rows into `{{ l2_instance_name }}_transactions` +
`{{ l2_instance_name }}_daily_balances` matching the canonical column
tuples. recon-gen handles the schema, the matview refresh, the
dashboards, and the audit PDFs.

That's the entire integration surface. You own the projection from
your source system into two tables; everything else
(L1 invariant matviews, Investigation matviews, four dashboards,
audit reconciliation report) is generated against your data without
further customization.

## Canonical column tuples — source of truth

The bulk helpers consume `Sequence[tuple[object, ...]]` in positional
order over two tuples defined at
`src/recon_gen/common/spine/_emit_helpers.py`. Build your row tuples
against these — the helpers do not accept named kwargs (the per-row
dict construction cost would dominate at 10k+ row loads).

### `TX_COLS` — 17 columns for `_transactions`

```python
TX_COLS = (
    "id", "account_id", "account_name", "account_role", "account_scope",
    "account_parent_role", "amount_money", "amount_direction", "status",
    "posting", "transfer_id", "transfer_parent_id", "rail_name",
    "template_name", "origin", "metadata", "supersedes",
)
```

Column-by-column:

- `id` — `TEXT NOT NULL`. The transaction's stable identifier (your
  upstream system's leg ID, prefixed if needed for uniqueness).
- `account_id` — `TEXT NOT NULL`. The account this leg lands on.
  Denormalized: must match an `Account` declared in the L2 YAML.
- `account_name` — `TEXT NOT NULL`. Denormalized human-readable name
  from the L2 account.
- `account_role` — `TEXT NOT NULL`. The account's role string from
  the L2 (e.g., `customer-dda`, `gl-control-cash`).
- `account_scope` — `TEXT NOT NULL`. `'internal'` or `'external'`.
- `account_parent_role` — `TEXT NULL`. The parent role if this is a
  leaf account; NULL for top-level accounts.
- `amount_money` — `BIGINT NOT NULL` (integer cents). Signed by
  `amount_direction`. **Bulk helpers auto-coerce dollars → cents** —
  pass floats / Decimals / `Cents` / ints freely.
- `amount_direction` — `TEXT NOT NULL`. `'credit'` (money in) or
  `'debit'` (money out).
- `status` — `TEXT NOT NULL`. `'Posted'`, `'Pending'`, `'Reversed'`,
  etc. — your L2's declared status enum.
- `posting` — `TEXT NOT NULL`. ISO timestamp string the leg was
  posted at (naive, interpreted in the DB's local TZ — see the
  [local-TZ convention](../reference/quicksight-quirks.md)).
- `transfer_id` — `TEXT NOT NULL`. Groups all legs of one
  money-movement event. The conservation-of-money invariant fires on
  this: non-failed legs of a non-single-leg transfer net to zero.
- `transfer_parent_id` — `TEXT NULL`. Set on chained transfers so the
  Money Trail / Account Network views can walk the chain recursively.
- `rail_name` — `TEXT NOT NULL`. L2-declared rail name.
- `template_name` — `TEXT NULL`. L2-declared transfer template; only
  the AX-shape L1 invariants (chain parent disagreement, XOR group,
  fan-in disagreement, multi-XOR) GROUP BY this.
- `origin` — `TEXT NOT NULL`. `'InternalInitiated'`,
  `'ExternalForcePosted'`, etc. — drives the L1 drift split between
  bank-initiated and force-posted activity.
- `metadata` — `TEXT NULL`. JSON string with extras. **Your hook MUST
  stamp `{"source": "real"}` here on every row** — see
  [The `metadata.source` contract](#the-metadatasource-contract) below.
- `supersedes` — `TEXT NULL`. Set on `'TechnicalCorrection'` rows
  that supersede a prior posting (drives the Supersession Audit sheet).

### `DB_COLS` — 10 columns for `_daily_balances`

```python
DB_COLS = (
    "account_id", "account_name", "account_role", "account_scope",
    "account_parent_role", "expected_eod_balance", "business_day_start",
    "business_day_end", "money", "metadata",
)
```

- `account_id` / `account_name` / `account_role` / `account_scope`
  / `account_parent_role` — same denormalization as `TX_COLS`.
- `expected_eod_balance` — `BIGINT NULL` (integer cents).
  L2-declared target; the L1 `expected_eod_balance_breach` matview
  fires when `money <> expected_eod_balance` at EOD. NULL = no
  declared target. **Auto-coerced from dollars.**
- `business_day_start` / `business_day_end` — `TEXT NOT NULL`. ISO
  timestamps bracketing the business day (typically midnight-to-
  midnight in the DB's local TZ).
- `money` — `BIGINT NOT NULL` (integer cents). Stored end-of-day
  balance. The drift check compares this to
  `SUM(signed amount_money)` from `_transactions` for the same
  `(account_id, business_day)`. **Auto-coerced from dollars.**
- `metadata` — `TEXT NULL`. JSON string. Holds the per-day
  limit-schedule payload + the
  `metadata.source` stamp (same contract as `_transactions`).

## The bulk helpers

Two functions, both at `src/recon_gen/common/spine/_emit_helpers.py`:

```python
def bulk_insert_tx(
    conn: SyncConnection,
    rows: Sequence[tuple[object, ...]],
    *,
    prefix: str = DEFAULT_PREFIX,
) -> None: ...

def bulk_insert_balance(
    conn: SyncConnection,
    rows: Sequence[tuple[object, ...]],
    *,
    prefix: str = DEFAULT_PREFIX,
) -> None: ...
```

Properties of both:

- **Positional tuple input.** Build rows in `TX_COLS` / `DB_COLS`
  order. The named-kwarg `insert_tx` / `insert_balance` helpers
  exist for one-row inserts; bulk is positional by design.
- **Money auto-coercion.** Money columns route through
  `_coerce_to_cents_int` at the insert boundary, which interprets
  values as follows:
  - `float`, `Decimal`, `int` → DOLLARS. `100.50` becomes `10050` cents;
    `100` becomes `10000` cents (a hundred dollars, NOT a hundred cents).
  - `Cents(N)` instance → already cents, passed through unchanged
    (use this when your source system already gives you integer cents).
  - `None` → SQL NULL (use for the optional money cols).
  - To pass a literal integer-cents value, wrap it: `Cents(15432)`
    means 15432 cents = $154.32. **Passing `15432` directly means
    $15,432.00** — easy footgun, see Pitfalls below.
- **Empty rows = no-op.** `bulk_insert_tx(conn, [])` does not open
  a cursor and does not fire SQL.
- **Dialect dispatch.** DuckDB connections route through the CA.10
  multi-row `VALUES (…), (…), …` coalescer (measured 54× faster than
  DuckDB's `executemany` at 50k rows). PG (psycopg) and Oracle
  (oracledb) connections route through `cursor.executemany` in
  1000-row chunks.
- **No `metadata.source` stamping.** The helpers do not write
  metadata for you. Build the JSON string yourself + put it in the
  tuple's metadata slot.

### Example: `bulk_insert_tx`

```python
import json
from recon_gen.common.spine._emit_helpers import bulk_insert_tx

tx_rows = [
    (
        "tx-2026-06-10-000001",       # id
        "cust-0042",               # account_id
        "Customer 42 DDA",             # account_name
        "customer-dda",                # account_role
        "internal",                    # account_scope
        None,                          # account_parent_role
        100.50,                        # amount_money (DOLLARS — auto-coerced to 10050 cents)
        "credit",                      # amount_direction
        "Posted",                      # status
        "2026-06-10 09:32:11",         # posting
        "transfer-xyz-001",            # transfer_id
        None,                          # transfer_parent_id
        "ACHOriginationDailySweep",    # rail_name
        None,                          # template_name
        "InternalInitiated",           # origin
        json.dumps({                   # metadata
            "source": "real",
            "external_reference": "ACH-2026-061000001",
        }),
        None,                          # supersedes
    ),
    # ... more rows ...
]

bulk_insert_tx(conn, tx_rows, prefix="myprefix")
```

### Example: `bulk_insert_balance`

```python
import json
from recon_gen.common.spine._emit_helpers import bulk_insert_balance

bal_rows = [
    (
        "cust-0042",               # account_id
        "Customer 42 DDA",             # account_name
        "customer-dda",                # account_role
        "internal",                    # account_scope
        None,                          # account_parent_role
        None,                          # expected_eod_balance (NULL = no target)
        "2026-06-10 00:00:00",         # business_day_start
        "2026-06-11 00:00:00",         # business_day_end
        15432.75,                      # money (DOLLARS — auto-coerced to 1543275 cents)
        json.dumps({"source": "real"}),  # metadata
    ),
    # ... more rows ...
]

bulk_insert_balance(conn, bal_rows, prefix="myprefix")
```

## The `metadata.source` contract

Two values matter:

- **`'training'`** — set by recon-gen's own seed and plant pipelines
  (the synthetic baseline + the L1-violation plants the dashboards
  surface against). Trainer reset's standalone-mode path uses this
  as a predicate: `DELETE WHERE JSON_VALUE(metadata, '$.source') = 'training'`.
- **`'real'`** — what your ETL hook MUST stamp on every row it
  writes. Rows without the stamp are presumed real by the
  standalone-mode gate (when `cfg.app2.etl_hook is None`), so leaving
  metadata at NULL also presents as real — but the explicit stamp
  is the contract; future tooling may tighten the gate.

Build the metadata JSON yourself + include it in the tuple. There's
no helper because the canonical form is small:

```python
import json

metadata = json.dumps({
    "source": "real",
    # Optional per-rail / per-leg extras the L2 + dashboards consume:
    "external_reference": upstream_row["wire_ref"],
    "expected_complete_at": upstream_row["rail_eta"],
})
```

The bulk helpers do not call `scenario_metadata` — that's recon-gen's
*internal* helper for stamping `source='training'` on synthetic plant
rows. The integrator surface is deliberately low-level: stamping at
the bulk boundary would silently overwrite intentional `source='real'`
rows.

## The `cfg.app2.etl_hook` ⇄ standalone-mode contract

`cfg.app2.etl_hook` is a single optional field in the operator's
`config.yaml`:

```yaml
app2:
  etl_hook: ./bin/my_etl.py
```

When **configured** (pointing at your wrapper):

- recon-gen knows your ETL owns the data.
- Trainer reset and Studio's *Deploy changes* truncate the base tables
  freely — the next ETL cycle refills them.
- Synthetic-plant scenarios composed via `ScenarioContext` still go
  through the `source='training'` tag, so they coexist with your real
  rows.

When **`None`** (no hook configured):

- recon-gen treats the demo DB as **standalone mode**: there is no
  upstream feed, so the synthetic seed IS the data.
- Trainer reset narrows its DELETE to
  `WHERE JSON_VALUE(metadata, '$.source') = 'training'` — rows your
  hook would have written (`source='real'`) survive any reset.
- Studio's *Deploy changes* refuses, because a deploy that re-emits
  the schema would drop your real-data rows.

Your hook is invoked as a subprocess. recon-gen passes the active cfg
path via `RECON_GEN_CONFIG` and the deployment's table prefix via
`RECON_GEN_DB_TABLE_PREFIX`. The wrapper's `stdout` and `stderr`
stream to recon-gen's run log; non-zero exit halts the deploy
pipeline (the demo DB is not touched).

## End-to-end skeleton — `my_etl.py`

A minimal reference implementation. Wire your source-system pulls
into `fetch_transactions_from_your_source` / `fetch_daily_balances_from_your_source`:

```python
#!/usr/bin/env python3
"""Reference ETL hook — emits real-data rows into recon-gen's demo DB."""

from __future__ import annotations

import json
import os
import sys

from recon_gen.common.config import load_config
from recon_gen.common.db import connect_demo_db
from recon_gen.common.spine._emit_helpers import (
    bulk_insert_balance, bulk_insert_tx,
)


def fetch_transactions_from_your_source() -> list[tuple[object, ...]]:
    # Your source-system pull here. Return rows positional in TX_COLS order.
    # Stamp metadata.source='real' in the metadata slot.
    return []


def fetch_daily_balances_from_your_source() -> list[tuple[object, ...]]:
    # Your source-system pull here. Return rows positional in DB_COLS order.
    # Stamp metadata.source='real' in the metadata slot.
    return []


def main() -> int:
    cfg = load_config(os.environ["RECON_GEN_CONFIG"])
    prefix = cfg.db.table_prefix
    conn = connect_demo_db(cfg)
    try:
        tx_rows = fetch_transactions_from_your_source()
        bal_rows = fetch_daily_balances_from_your_source()
        bulk_insert_tx(conn, tx_rows, prefix=prefix)
        bulk_insert_balance(conn, bal_rows, prefix=prefix)
        conn.commit()
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

After your hook completes, refresh the matviews. The CLI does this
for you in `recon-gen data refresh --execute`; from Python:

```python
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import refresh_matviews_sql

l2 = load_instance(cfg.db.default_l2_instance)
for stmt in refresh_matviews_sql(l2).split(";\n"):
    if stmt.strip():
        conn.execute(stmt)
conn.commit()
```

## Per-dialect cfg pointers

Operator cfgs at `run/config.<dialect>.yaml`:

- **`run/config.duckdb.yaml`** — `db.url: "duckdb:///run/<your-l2>.duckdb"`.
  Single-process; the bulk helpers route to the CA.10 multi-row
  VALUES coalescer.
- **`run/config.postgres.yaml`** — `db.url:
  "postgresql://user:pass@host:port/db"`. The schema emitter prepends
  `CREATE EXTENSION IF NOT EXISTS pgcrypto` (audit-provenance hash);
  your role needs `CREATE EXTENSION` privilege OR the extension
  pre-installed by your DBA.
- **`run/config.oracle.yaml`** — `db.url:
  "oracle+oracledb://user:pass@host:port/?service_name=...`.
  The bulk helpers use `cursor.executemany` in 1000-row chunks so
  each iteration gets its own IDENTITY value — composite `(id, entry)`
  PKs don't collide the way `INSERT ALL` would.

Add the `etl_hook` line to whichever cfg matches your dialect.

## Common pitfalls

- **Forgetting `metadata.source='real'`** — your rows present as
  synthetic to standalone-mode Trainer reset, which means a future
  reset could DELETE them. Always stamp the metadata.
- **Mixing dollars and cents in the money columns** — `int`,
  `float`, `Decimal` are ALL treated as DOLLARS. If your source
  system gives you integer cents (`15432` = $154.32), you MUST
  wrap as `Cents(15432)` — passing the bare `15432` makes it
  $15,432.00 silently. `Cents(N)` is the only path that means
  "this is already cents." Don't pass `15432.75` if you meant
  "15432.75 cents" — that's `$15,432.75` after coercion (the
  fractional part is a floor-to-cents thing).
- **Skipping matview refresh after the load** — the L1 invariant
  matviews and Investigation matviews do not auto-refresh on PG or
  Oracle. Dashboards lag the source data until
  `refresh_matviews_sql(l2_instance)` runs. The
  [Data Integration handbook](etl.md#symptom-dashboards-show-data-older-than-expected)
  has the diagnostic ladder for this symptom.
- **Empty rows list** — no-op, no crash, no SQL fired. Safe to call
  unconditionally from a loop that may produce zero rows for a
  given batch window.
- **Authoring named kwargs against the bulk helpers** — they accept
  positional tuples only. If you want column-by-name, use `insert_tx`
  / `insert_balance` (one row per call, no `executemany` fast path).

## Reference

- [Data Integration handbook](etl.md) — the higher-level walkthrough
  of the two-table contract, matview refresh sequence, and idempotency.
- [Schema v6 — Data Feed Contract](../Schema_v6.md) — column-by-column
  contract with per-column failure modes.
- [Seed generator](seed-generator.md) — what the synthetic
  `source='training'` baseline looks like.
- [Walkthrough: how do I populate transactions?](../walkthroughs/etl/how-do-i-populate-transactions.md)
  — `INSERT INTO ... SELECT FROM` shape for a SQL-only ETL.
- `src/recon_gen/common/spine/_emit_helpers.py` — source of truth
  for the bulk helpers, `TX_COLS`, `DB_COLS`, and the money coercion.
- `src/recon_gen/common/config.py` — `Config.etl_hook` field docs +
  AO.1 money contract notes.

[Vocabulary](../handbook/etl.md)
