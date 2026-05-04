# Schema v6 — the L2-fed two-table reconciliation feed contract

This document is the contract for **what your ETL writes** and **what
the dashboards read**. Two base tables under the hood; everything else
— L1 invariant matviews, dashboard-shape matviews, the analyst-facing
prose — derives from them.

> **Schema_v6 supersedes Schema_v3.** Same two-table promise; new
> column shape (per-leg `amount_money` + `amount_direction` instead
> of v3's `signed_amount`; new `entry` BIGSERIAL for supersession;
> per-rail aging caps; per-instance prefix isolation). v3 was the
> last single-tenant pre-supersession iteration; everything since
> M.1a runs on v6.

> **PostgreSQL 17+ or Oracle 19c+.** SQL is dialect-aware (see
> `common.sql.dialect`) but stays portable across the dialect family
> this app targets:
> - JSON storage in `TEXT` columns (PG) / `CLOB` (Oracle) with
>   `IS JSON` constraints.
> - JSON extraction via SQL/JSON path functions (`JSON_VALUE`,
>   `JSON_QUERY`, `JSON_EXISTS`) — supported on both engines.
> - B-tree indexes only on real columns.
> - **No** `JSONB`, no `->>` / `->` / `@>` / `?` operators, no GIN
>   indexes on JSON, no Postgres extensions, no array / range types,
>   no named `WINDOW w AS` clause (Oracle 19c lacks it).
> - All timestamp columns are TZ-naive `TIMESTAMP` on both engines
>   (P.9a). **Timezone normalization is the integrator's contract** —
>   the schema does not store timezone metadata and does not convert
>   across zones at query time. ETL teams reading from sources in
>   multiple zones MUST normalize at the ETL boundary (typically to
>   UTC or the institution's local business zone).
> See **Forbidden SQL patterns** at the end. Pick the dialect via the
> `dialect:` field on your config YAML (default `postgres`).

---

## The layered model

Your ETL writes two tables. The L1 library projects everything else:

```
ETL writes
  ├── {{ l2_instance_name }}_transactions       — one row per money-movement leg
  └── {{ l2_instance_name }}_daily_balances     — one row per (account, date) snapshot
                  ↓ (supersession projection — M.1.5)
Current* matviews
  ├── {{ l2_instance_name }}_current_transactions
  └── {{ l2_instance_name }}_current_daily_balances
                  ↓ (computed-balance derivation)
Helpers
  ├── {{ l2_instance_name }}_computed_subledger_balance
  └── {{ l2_instance_name }}_computed_ledger_balance
                  ↓ (SHOULD-constraint surfaces)
L1 invariant matviews
  ├── {{ l2_instance_name }}_drift                          — leaf account drift
  ├── {{ l2_instance_name }}_ledger_drift                   — parent account drift
  ├── {{ l2_instance_name }}_overdraft                      — non-negative balance
  ├── {{ l2_instance_name }}_expected_eod_balance_breach    — declared EOD target
  ├── {{ l2_instance_name }}_limit_breach                   — outbound flow cap
  ├── {{ l2_instance_name }}_stuck_pending                  — per-rail Pending aging (M.2b.8)
  └── {{ l2_instance_name }}_stuck_unbundled                — per-rail Unbundled aging (M.2b.9)
                  ↓ (UI convenience)
Dashboard-shape matviews
  ├── {{ l2_instance_name }}_daily_statement_summary
  └── {{ l2_instance_name }}_todays_exceptions              — UNION over the 5 baselines
```

13 matviews per L2 instance; full per-view contract in
[L1 Invariants](L1_Invariants.md). The **L2 instance prefix** isolates
all of them — multiple institutions coexist in one database via
prefix-namespaced DDL.

---

## Per-instance prefix isolation

Every CREATE in the emitted DDL is prefixed by `instance.instance`
(an `Identifier` from the L2 YAML). For an instance named
`{{ l2_instance_name }}`:

| Layer | Object name |
|---|---|
| Base table | `{{ l2_instance_name }}_transactions`, `{{ l2_instance_name }}_daily_balances` |
| Current* | `{{ l2_instance_name }}_current_transactions`, `{{ l2_instance_name }}_current_daily_balances` |
| Helper | `{{ l2_instance_name }}_computed_subledger_balance`, `{{ l2_instance_name }}_computed_ledger_balance` |
| L1 invariant | `{{ l2_instance_name }}_drift`, `{{ l2_instance_name }}_overdraft`, … |
| Dashboard | `{{ l2_instance_name }}_daily_statement_summary`, `{{ l2_instance_name }}_todays_exceptions` |
| Index | `idx_{{ l2_instance_name }}_<...>` |

Two L2 instances coexist in one database without conflict — `myorg_*`
+ `{{ l2_instance_name }}_*` live side-by-side. The dashboard queries are also
prefix-parameterized; switching the deployed dashboard's L2 instance
swaps every dataset's `FROM {{ l2_instance_name }}_*` clause.

Emit the schema for an instance:

```python
from quicksight_gen.common.l2 import emit_schema, load_instance
instance = load_instance("path/to/myorg.yaml")
sql = emit_schema(instance)  # full DDL: drop + create + indexes
```

`emit_schema` is idempotent — every CREATE is preceded by a
`DROP IF EXISTS`. Re-running on a stale instance converges to the
target state.

---

## Table 1 — `{{ l2_instance_name }}_transactions`

One row per money-movement **leg**. Two-leg transfers (debit + credit
pairs) write two rows; single-leg transfers (sales, external
observations) write one row; multi-leg bundled transfers write N rows.
Every row identifies its parent transfer via `transfer_id`.

### Columns

| Column | Type | Notes |
|---|---|---|
| `entry` | `BIGSERIAL NOT NULL` | Append-only supersession key. Higher entry overrides lower for the same logical `id`. The Current* matview projects max(entry) per logical key. |
| `id` | `VARCHAR(100) NOT NULL` | Logical transaction id. Multiple entries per `id` form the supersession audit trail (M.2b.12). |
| `account_id` | `VARCHAR(100) NOT NULL` | Account this leg posted to. |
| `account_name` | `VARCHAR(255) NOT NULL` | Denormalized display name. |
| `account_role` | `VARCHAR(100) NOT NULL` | The L2 role this account materializes. |
| `account_scope` | `VARCHAR(20) NOT NULL` | `'internal'` or `'external'`. |
| `account_parent_role` | `VARCHAR(100)` | NULL for parent / external accounts; populated for sub-ledger child accounts. |
| `amount_money` | `DECIMAL(20,2) NOT NULL` | Signed amount. **Positive = Credit (money in), Negative = Debit (money out).** Per L1 Amount invariant. |
| `amount_direction` | `VARCHAR(20) NOT NULL` | `'Debit'` or `'Credit'`. Constrained agreement with `amount_money` sign — see CHECK below. |
| `status` | `VARCHAR(20) NOT NULL` | `'Pending'`, `'Posted'`, `'Failed'`. Drives stuck_pending + non-zero-transfer math. |
| `posting` | `TIMESTAMP NOT NULL` (TZ-naive) | When the leg posted to the underlying ledger. |
| `transfer_id` | `VARCHAR(100) NOT NULL` | Groups legs of one financial event. Conservation invariant: `Σ amount_money` over non-Failed legs of one transfer = expected_net (typically 0 for two-leg, ExpectedNet for templates). |
| `transfer_type` | `VARCHAR(50) NOT NULL` | The L2 TransferType (`'ach'`, `'wire'`, `'fee'`, `'internal'`, etc). |
| `transfer_completion` | `TIMESTAMP` (TZ-naive) | When the transfer finished its full lifecycle (last leg posted). NULL while in flight. |
| `transfer_parent_id` | `VARCHAR(100)` | Recursive parent — links a transfer to its parent (PR pattern: `external_txn → payment → settlement → sale`). |
| `rail_name` | `VARCHAR(100) NOT NULL` | Which Rail produced this leg. Drives stuck_pending / stuck_unbundled per-rail caps. |
| `template_name` | `VARCHAR(100)` | If posted via a TransferTemplate, the template name. NULL otherwise. |
| `bundle_id` | `VARCHAR(100)` | If picked up by an AggregatingRail, the bundle id. NULL until bundled. |
| `supersedes` | `VARCHAR(50)` | NULL for original entries; one of `'Inflight'` / `'BundleAssignment'` / `'TechnicalCorrection'` on rewrite entries. |
| `origin` | `VARCHAR(50) NOT NULL` | `'InternalInitiated'` / `'ExternalForcePosted'` / `'ExternalAggregated'`. Per leg — different legs of the same transfer can carry different Origins. |
| `metadata` | `TEXT` | Open per-row JSON for app-specific keys. `IS JSON` constraint. See **Metadata** below. |

### Constraints

```sql
PRIMARY KEY (entry)

CHECK (amount_direction IN ('Debit', 'Credit'))
CHECK (status IN ('Pending', 'Posted', 'Failed'))
CHECK (account_scope IN ('internal', 'external'))
CHECK (origin IN ('InternalInitiated', 'ExternalForcePosted', 'ExternalAggregated'))
CHECK (supersedes IS NULL
    OR supersedes IN ('Inflight', 'BundleAssignment', 'TechnicalCorrection'))

-- L1 Amount invariant — money agrees with direction.
CHECK (
    (amount_direction = 'Credit' AND amount_money >= 0)
 OR (amount_direction = 'Debit'  AND amount_money <= 0)
)

-- Portable JSON storage.
CHECK (metadata IS NULL OR metadata IS JSON)
```

**No FKs** between transactions and daily_balances — the join is logical
(via `account_id` + day truncation), not enforced. Lets the two tables
load independently.

### Indexes

```sql
CREATE INDEX idx_{{ l2_instance_name }}_transactions_account_posting ON {{ l2_instance_name }}_transactions (account_id, posting);
CREATE INDEX idx_{{ l2_instance_name }}_transactions_transfer        ON {{ l2_instance_name }}_transactions (transfer_id);
CREATE INDEX idx_{{ l2_instance_name }}_transactions_type_status     ON {{ l2_instance_name }}_transactions (transfer_type, status);
CREATE INDEX idx_{{ l2_instance_name }}_transactions_parent          ON {{ l2_instance_name }}_transactions (transfer_parent_id);

-- Bundler eligibility hot-path: AggregatingRails query for Posted,
-- unbundled rows by rail_name. Partial index on `bundle_id IS NULL`
-- keeps it small as bundled-row count grows.
CREATE INDEX idx_{{ l2_instance_name }}_transactions_bundler_eligibility
    ON {{ l2_instance_name }}_transactions (rail_name, status)
    WHERE bundle_id IS NULL;
```

---

## Table 2 — `{{ l2_instance_name }}_daily_balances`

One row per `(account_id, business_day_start)` snapshot. The bank's
end-of-day stored balance for each account each day.

### Columns

| Column | Type | Notes |
|---|---|---|
| `entry` | `BIGSERIAL NOT NULL` | Same supersession story as `transactions.entry` — Current* projects max(entry) per logical key. |
| `account_id` | `VARCHAR(100) NOT NULL` | Same logical id space as `transactions.account_id`. |
| `account_name` | `VARCHAR(255) NOT NULL` | Denormalized. |
| `account_role` | `VARCHAR(100) NOT NULL` | L2 role. |
| `account_scope` | `VARCHAR(20) NOT NULL` | `'internal'` / `'external'`. |
| `account_parent_role` | `VARCHAR(100)` | Parent role; NULL for parent / external. |
| `expected_eod_balance` | `DECIMAL(20,2)` | If set, the L1 invariant `expected_eod_balance_breach` fires when `money <> expected_eod_balance` at EOD. NULL = no expected target declared. |
| `business_day_start` | `TIMESTAMP NOT NULL` (TZ-naive) | Beginning-of-day UTC midnight. The composite key `(account_id, business_day_start)` is the logical row id. |
| `business_day_end` | `TIMESTAMP NOT NULL` (TZ-naive) | End-of-day = `business_day_start + INTERVAL '1 day'`. |
| `money` | `DECIMAL(20,2) NOT NULL` | Stored EOD balance. Computed-vs-stored disagreement surfaces as drift. |
| `limits` | `TEXT` | Per-row JSON; per-day limit overrides. See **Metadata** below. |
| `supersedes` | `VARCHAR(50)` | Same vocabulary as transactions.supersedes. |

### Constraints

```sql
PRIMARY KEY (entry)
CHECK (account_scope IN ('internal', 'external'))
CHECK (limits IS NULL OR limits IS JSON)
CHECK (supersedes IS NULL
    OR supersedes IN ('Inflight', 'BundleAssignment', 'TechnicalCorrection'))
```

### Indexes

```sql
CREATE INDEX idx_{{ l2_instance_name }}_daily_balances_business_day
    ON {{ l2_instance_name }}_daily_balances (business_day_start);
```

---

## Sign convention

`amount_money` is **signed**:

- Positive = Credit (money INTO the account, from the account-holder's
  perspective)
- Negative = Debit (money OUT of the account)
- `daily_balances.money` = `Σ amount_money` over the account's history
  (the drift-check invariant)

Same rule for every `account_scope`. A leg posted to a customer DDA
with `amount_money = +250.00, amount_direction = 'Credit'` means the
customer's balance went up by $250. The matching leg posted to the
counterparty (which sent the money) has `amount_money = -250.00,
amount_direction = 'Debit'`.

The CHECK constraint enforces sign-direction agreement at write time —
ETL bugs that emit `Credit` with negative money fail at INSERT.

---

## Supersession (`entry` + Current* + `supersedes`)

The base tables are append-only. To "correct" a prior posting, the
ETL writes a new row with the same logical id (`transactions.id` or
`(daily_balances.account_id, business_day_start)`) and a `supersedes`
reason.

PostgreSQL's `BIGSERIAL` auto-increments `entry` per insert, so the
correction lands at a higher entry than the original. The Current*
matviews project **max(entry) per logical key**, so dashboard queries
read the corrected version transparently:

```sql
CREATE MATERIALIZED VIEW {{ l2_instance_name }}_current_transactions AS
SELECT * FROM (
    SELECT *,
           ROW_NUMBER() OVER (PARTITION BY id ORDER BY entry DESC) AS rn
    FROM {{ l2_instance_name }}_transactions
) sub
WHERE rn = 1;
```

`supersedes` reasons (per L1 SPEC):

- `'Inflight'` — re-stating an in-flight leg (status flip, late
  metadata enrichment).
- `'BundleAssignment'` — re-stating a Posted leg when an AggregatingRail
  picks it up and assigns a `bundle_id`.
- `'TechnicalCorrection'` — re-stating after the original posting was
  wrong (amount fix, account swap, etc).

The Supersession Audit dashboard sheet (M.2b.12) reads from the BASE
tables (not Current*) since by definition Current* hides the audit-
relevant prior entries. See [L1 Invariants](L1_Invariants.md) and the
[Supersession Audit walkthrough](walkthroughs/l1/supersession-audit.md).

---

## Metadata JSON columns

Both `transactions.metadata` and `daily_balances.limits` are open
per-row JSON in `TEXT` columns. Read with SQL/JSON path:

```sql
SELECT JSON_VALUE(tx.metadata, '$.customer_id') AS customer_id
FROM   {{ l2_instance_name }}_transactions tx
WHERE  JSON_EXISTS(tx.metadata, '$.customer_id');
```

The portability constraint forbids `JSONB` and Postgres-specific
operators (`->>`, `->`, `@>`, `?`). All extraction must go through
the `JSON_VALUE` / `JSON_QUERY` / `JSON_EXISTS` family.

### Common patterns

- App-specific keys go in `metadata` rather than as new schema columns.
  Example: PR's `card_brand`, `cashier`, `settlement_type`,
  `payment_method`, `is_returned`, `return_reason` all live in
  `metadata`.
- L2 `LimitSchedules` are EMITTED as inline CASE branches in the
  `_limit_breach` view at schema-emit time — **not** read from
  `daily_balances.limits` at query time. The `limits` column exists
  for per-day override scenarios that may emerge later.

---

## Refresh contract

Every batch insert into `{{ l2_instance_name }}_transactions` or `{{ l2_instance_name }}_daily_balances`
MUST be followed by `refresh_matviews_sql(instance)` to recompute
every dependent matview in dependency order:

```python
from quicksight_gen.common.l2 import refresh_matviews_sql
from quicksight_gen.common.sql import Dialect

sql = refresh_matviews_sql(instance, dialect=Dialect.POSTGRES)
# 13 matviews × 2 statements (REFRESH + ANALYZE) = 26 statements

# Postgres
import psycopg2
conn = psycopg2.connect(your_db_url)
conn.autocommit = True
with conn.cursor() as cur:
    for stmt in sql.split(';'):
        s = stmt.strip()
        if s:
            cur.execute(s)

# Oracle (thin mode — no Instant Client install needed)
# `quicksight-gen schema apply -c run/config.oracle.yaml --execute` +
# `quicksight-gen data apply -c run/config.oracle.yaml --execute` +
# `quicksight-gen data refresh -c run/config.oracle.yaml --execute`
# is the canonical chain; both per-prefix DDL emission and the apply
# step handle PL/SQL terminators (DROP MATERIALIZED VIEW IF EXISTS
# wraps in a BEGIN…EXCEPTION…END block on Oracle). For a stand-alone
# refresh outside the CLI, copy the splitter from
# `common/db.py::execute_script` until a public helper lands.
```

Order matters — leaves first (Current\*), helpers second (computed_*),
L1 invariants third, dashboard-shape last. PostgreSQL refuses to
refresh a downstream matview before its upstream is fresh; the
emitter handles ordering.

The ANALYZE follow-ups update planner statistics so subsequent SELECTs
hit the indexed lookups (without ANALYZE the planner doesn't know the
post-REFRESH row count + value distribution and may pick a sequential
scan over the matview).

---

## ETL contract — minimum viable feed

To see *something* on the dashboard, populate these columns on every row:

### `{{ l2_instance_name }}_transactions` minimum columns

`entry` (auto), `id`, `account_id`, `account_name`, `account_role`,
`account_scope`, `amount_money`, `amount_direction`, `status`,
`posting`, `transfer_id`, `transfer_type`, `rail_name`, `origin`.

Optional on day 1; populate when a downstream check needs them:

| Column | Populates when |
|---|---|
| `account_parent_role` | The Drift / Limit Breach views need the parent rollup (most cases). |
| `transfer_completion` | The dashboard shows transfer-lifecycle aging. |
| `transfer_parent_id` | PR pipeline (sale → settlement → payment → external). |
| `template_name` | TransferTemplates with named variants (closure tracking). |
| `bundle_id` | AggregatingRails finalize bundles (sets `bundle_id`). |
| `supersedes` | Correction workflows. NULL on every original posting. |
| `metadata` | App-specific extension keys (per-app conventions). |

### `{{ l2_instance_name }}_daily_balances` minimum columns

`entry` (auto), `account_id`, `account_name`, `account_role`,
`account_scope`, `business_day_start`, `business_day_end`, `money`.

Optional on day 1:

| Column | Populates when |
|---|---|
| `account_parent_role` | Drift parent rollup. |
| `expected_eod_balance` | The L2 declares an EOD target for this account. |
| `limits` | Per-day limit override scenarios (rare; LimitSchedules cover the static case). |
| `supersedes` | Stored-balance restatement. |

### Order of operations for a new feed

1. Write your L2 instance YAML — declare accounts, rails, transfer
   templates, chains, limit schedules. Rich descriptions.
2. `emit_schema(instance)` → DDL → psql/psycopg2. Verifies the schema
   applies cleanly; idempotent on re-run.
3. Write minimum-viable rows to both base tables.
4. `refresh_matviews_sql(instance)` after every batch.
5. Deploy the L1 dashboard against the same `cfg` + `instance`. Open
   it. Confirm Today's Exceptions roll-up shows what you expect.
6. Iterate — populate optional columns as downstream checks demand
   them (L1 invariant matviews surface the gap).

---

## Lateness as data

Two complementary lateness signals coexist in the v6 contract:

- **Per-rail aging caps (L2-fed, M.2b path).** The L2 instance declares
  `max_pending_age` on each Rail and `max_unbundled_age` on rails picked
  up by an AggregatingRail. `emit_schema` inlines these as CASE branches
  in the `{{ l2_instance_name }}_stuck_pending` and `{{ l2_instance_name }}_stuck_unbundled` matviews;
  any leg whose `EXTRACT(EPOCH FROM (NOW() - posting))` exceeds its rail's
  cap surfaces as a violation. This is the recommended path going forward
  — caps are configured once in YAML, the dashboard reads them generically.
- **`expected_complete_at` (legacy v5 path).** The pre-L2 single-tenant
  schema carried an optional TIMESTAMP column on `transactions`. When set
  per-leg, the dashboard's data-driven `is_late` predicate fires off
  `CURRENT_TIMESTAMP > COALESCE(expected_complete_at, posted_at + INTERVAL '1 day')`;
  when NULL, every row falls back to a one-day default. Adopt one rail at
  a time. This path remains supported for the v5 hand-rolled per-app
  dashboards but is not the L2-fed surface.

Both signals answer the same SHOULD-constraint ("transactions on rail X
SHOULD complete within window W") — the L2 path makes the window a
schema-emit-time property of the rail; the v5 path makes it a per-row
property of the leg. Pick one per app; don't mix.

## Forbidden SQL patterns

The portability constraint targets PostgreSQL 17+ AND Oracle 19c+ —
every emitted statement must work on both. The library threads the
dialect through every emitter (see `common.sql.dialect`), so DDL like
`SERIAL` vs `NUMBER GENERATED BY DEFAULT AS IDENTITY` and matview
options like `BUILD IMMEDIATE REFRESH COMPLETE ON DEMAND` (Oracle) are
handled for you. What you write yourself (custom dataset SQL, ETL
projections) must follow these rules:

| Forbidden | Use instead | Why |
|---|---|---|
| `JSONB` column type | `TEXT` with `IS JSON` constraint | Oracle has no `JSONB` |
| `->>` `->` operators | `JSON_VALUE(col, '$.key')` | Postgres-only operators |
| `@>` `?` containment / existence | `JSON_EXISTS(col, '$.key')` | Postgres-only operators |
| GIN indexes on JSON | B-tree on real columns; metadata is searched, not indexed | Oracle has no GIN |
| Postgres extensions (e.g., `pg_trgm`, `uuid-ossp`) | None | Oracle has no extension model |
| Array types (`TEXT[]`, `INT[]`) | Normalized child rows, or JSON arrays in metadata | Oracle has no array types |
| Range types (`tstzrange`, etc) | Two `TIMESTAMP` columns | Oracle has no range types |
| Window functions inside CTE references that recurse | Plain recursive CTEs | Both dialects' planners |
| `RETURNING` for batch fanout | Re-SELECT after INSERT | Oracle's `RETURNING` is single-row |
| Multi-row `INSERT … VALUES (a),(b)` | Per-row `INSERT … VALUES (…)` statements | Oracle 19c rejects multi-row VALUES |
| Named `WINDOW w AS (…)` clause | Inline the `OVER (…)` definition on each window function | Oracle 19c added named WINDOW in 21c only |
| `TIMESTAMPTZ` / `TIMESTAMP WITH TIME ZONE` columns | Plain `TIMESTAMP` via `timestamp_type(dialect)`. TZ normalization happens at the ETL boundary (see top callout). | Single TZ-naive type unifies both engines; was previously a Postgres / Oracle PK divergence (ORA-02329). |
| Bare-string Oracle timestamp literal `'2030-01-01 10:00:00'` | `TIMESTAMP 'YYYY-MM-DD HH:MI:SS'` typed literal (no TZ offset) | Oracle's plain `TIMESTAMP` literal must be wrapped in the typed `TIMESTAMP '…'` form. |
| Recursive CTE without explicit column-alias list | `WITH chain (col1, col2, depth) AS (…)` | Oracle 19c requires the alias list (ORA-32039) |
| `EXTRACT(EPOCH FROM …)` | `epoch_seconds_between(a, b, dialect)` | Oracle's `EXTRACT` doesn't accept `EPOCH` |
| `IF EXISTS` on `DROP MATERIALIZED VIEW` | Use `drop_matview_if_exists(name, dialect)` (wraps Oracle in PL/SQL) | Oracle has no `IF EXISTS` clause on most DROPs |

`emit_schema` enforces these at code-gen time — every emitted DDL is
audit-clean. Custom dataset SQL written by integrators must follow
the same rules; `tests/test_l2_schema.py::test_no_forbidden_constructs`
walks the emitted DDL and asserts.

---

## See also

- [L1 Invariants](L1_Invariants.md) — the per-matview SHOULD-constraint
  reference. Read this when the dashboard surfaces a violation and
  you need to know which feed column to fix.
- [Customization Handbook](handbook/customization.md) — for product-
  owner / developer onboarding to the L2-fed pattern.
- [Data Integration Handbook](handbook/etl.md) — the ETL-engineer
  view: per-question walkthroughs ("how do I populate transactions",
  "how do I prove my ETL is working", etc).
- [L1 Reconciliation Dashboard](handbook/l1.md) — the analyst-facing
  surface this contract feeds.
