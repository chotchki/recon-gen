# P.2 — Dialect catalog

Phase P.2 audit. Inventory of every Postgres-specific SQL construct
in the emit surfaces (`common/l2/schema.py`, `common/l2/seed.py`,
`apps/<app>/datasets.py`, `common/sheets/app_info.py`). Each entry
becomes a helper in `common/sql/dialect.py`; helpers' Oracle branch
fills in during P.3.

## §1 — Constructs that need a helper

### §1.1 Column type names (DDL)

| Postgres | Oracle 19c | Helper |
|---|---|---|
| `BIGSERIAL` | `NUMBER GENERATED ALWAYS AS IDENTITY` | `serial_type(d)` |
| `BOOLEAN` | `NUMBER(1) CHECK (… IN (0,1))` | `boolean_type(d)` |
| `TEXT` | `CLOB` | `text_type(d)` |
| `TIMESTAMPTZ` | `TIMESTAMP WITH TIME ZONE` | `timestamp_tz_type(d)` |
| `VARCHAR(n)` | `VARCHAR2(n)` | `varchar_type(n, d)` |
| `DECIMAL(p,s)` | `NUMBER(p,s)` | `decimal_type(p, s, d)` |
| `DATE` | `DATE` | portable — no helper |
| `TIMESTAMP` | `TIMESTAMP` | portable — no helper |

Usage sites: `common/l2/schema.py` `_SCHEMA_TEMPLATE` (~30 column defs
across `{p}_transactions` + `{p}_daily_balances`).

### §1.2 Cast operator

| Postgres | Oracle 19c | Helper |
|---|---|---|
| `expr::type` | `CAST(expr AS type)` | `cast(expr, type, d)` |
| `NULL::numeric` | `CAST(NULL AS NUMBER)` | `typed_null(type, d)` |
| `posting::date` | `CAST(posting AS DATE)` (or `TRUNC(posting)`) | `to_date(expr, d)` |

Usage sites: `common/l2/schema.py` `_render_limit_breach_cases` (typed
NULL fallback), `_render_pending_age_cases` (typed NULL bigint),
`_INV_MATVIEWS_TEMPLATE` (multiple `posting::date` casts).

### §1.3 JSON validation constraint

| Postgres | Oracle 19c | Helper |
|---|---|---|
| `CHECK (col IS JSON)` | `CHECK (col IS JSON)` | `json_check(col, d)` (portable but emit through helper for the OR-NULL guard) |

Usage sites: `{p}_transactions.metadata` constraint;
`{p}_daily_balances.metadata` constraint (post-AV; was `limits`).

### §1.4 JSON path query

| Postgres | Oracle 19c | Helper |
|---|---|---|
| `JSON_VALUE(col, '$.key')` | `JSON_VALUE(col, '$.key')` | portable — no helper needed |

Usage sites: `apps/l2_flow_tracing/datasets.py` (~6 dynamic-key
`JSON_VALUE` invocations + `'$.' || <<$pKey>>` concatenation).

The `||` concat is also portable (both dialects).

### §1.5 Date/time arithmetic

| Postgres | Oracle 19c | Helper |
|---|---|---|
| `CURRENT_TIMESTAMP` | `CURRENT_TIMESTAMP` | portable — no helper |
| `EXTRACT(EPOCH FROM (a - b))` | `EXTRACT(DAY FROM (a-b))*86400 + EXTRACT(HOUR FROM (a-b))*3600 + …` (or use `(a - b) * 86400` if both are DATE — but TIMESTAMP arithmetic returns INTERVAL DAY TO SECOND in Oracle, requires the EXTRACT chain) | `epoch_seconds_between(later, earlier, d)` |
| `INTERVAL '1 day'` | `INTERVAL '1' DAY` | `interval_days(n, d)` |
| `(date - INTERVAL '1 day')` | `(date - 1)` | `date_minus_days(date_expr, n, d)` |

Usage sites: `common/l2/schema.py` (3 `EXTRACT(EPOCH FROM …)` calls
in stuck-aging matviews; `INTERVAL '1 day'` in
`_INV_MATVIEWS_TEMPLATE` rolling-window logic).

### §1.6 Materialized views

| Postgres | Oracle 19c | Helper |
|---|---|---|
| `CREATE MATERIALIZED VIEW <name> AS …` | `CREATE MATERIALIZED VIEW <name> BUILD IMMEDIATE REFRESH ON DEMAND AS …` | `create_matview(name, body, d)` |
| `REFRESH MATERIALIZED VIEW <name>;` | `BEGIN DBMS_MVIEW.REFRESH('<name>'); END;` | `refresh_matview(name, d)` |
| `ANALYZE <name>;` | `BEGIN DBMS_STATS.GATHER_TABLE_STATS(USER, '<name>'); END;` | `analyze_table(name, d)` |

Usage sites: `common/l2/schema.py` emits 13 matviews; `refresh_matviews_sql`
emits per-matview REFRESH + ANALYZE.

### §1.7 DROP IF EXISTS

Oracle 19c has no `IF EXISTS` clause on DROP. Standard pattern is a
PL/SQL block that catches `ORA-00942` (table or view does not exist).

| Postgres | Oracle 19c | Helper |
|---|---|---|
| `DROP TABLE IF EXISTS <name> CASCADE` | `BEGIN EXECUTE IMMEDIATE 'DROP TABLE <name> CASCADE CONSTRAINTS'; EXCEPTION WHEN OTHERS THEN IF SQLCODE != -942 THEN RAISE; END IF; END;` | `drop_table_if_exists(name, d)` |
| `DROP MATERIALIZED VIEW IF EXISTS <name>` | (same pattern, `DROP MATERIALIZED VIEW`) | `drop_matview_if_exists(name, d)` |
| `DROP INDEX IF EXISTS <name>` | (same pattern, `DROP INDEX`, ORA-01418) | `drop_index_if_exists(name, d)` |
| `DROP VIEW IF EXISTS <name>` | (same pattern, `DROP VIEW`) | `drop_view_if_exists(name, d)` |

Usage sites: `common/l2/schema.py` `_SCHEMA_TEMPLATE` drop-block
(~10 DROPs); per-matview drop pairs (~13 more).

### §1.8 Recursive CTE

| Postgres | Oracle 19c | Helper |
|---|---|---|
| `WITH RECURSIVE chain AS (…)` | `WITH chain AS (…)` (recursion is implicit; the `RECURSIVE` keyword is rejected) | `with_recursive(d) -> "WITH RECURSIVE" \| "WITH"` |

Usage sites: `_INV_MATVIEWS_TEMPLATE` `inv_money_trail_edges` walk
over `transfer_parent_id` chains.

## §2 — Constructs that DON'T need a helper (already portable)

- `CHECK (col IN ('a', 'b'))` — both dialects accept inline IN list.
- `||` string concatenation — both dialects.
- `CASE WHEN … THEN … ELSE … END` — both dialects.
- `COALESCE(…)` — both dialects.
- Window functions (`OVER (PARTITION BY … ORDER BY …)`) — both dialects.
- `JSON_VALUE`, `JSON_QUERY`, `JSON_EXISTS` — both dialects (the
  portability constraint that motivated SQL/JSON path was Oracle
  parity).
- Subquery JOINs, LATERAL JOIN, basic SELECT/WHERE/GROUP BY/ORDER BY.
- `LIMIT n OFFSET m` — Oracle 19c has `OFFSET … FETCH FIRST` but the
  current emit doesn't use LIMIT/OFFSET in any dataset SQL (verified
  by grep). If this changes, add a helper then.

## §3 — Helper API summary

The complete helper surface:

```python
from enum import Enum

class Dialect(str, Enum):
    POSTGRES = "postgres"
    ORACLE = "oracle"

# Type names (DDL)
def serial_type(d: Dialect) -> str: ...
def boolean_type(d: Dialect) -> str: ...
def text_type(d: Dialect) -> str: ...
def timestamp_tz_type(d: Dialect) -> str: ...
def varchar_type(n: int, d: Dialect) -> str: ...
def decimal_type(precision: int, scale: int, d: Dialect) -> str: ...

# Casts
def cast(expr: str, type_name: str, d: Dialect) -> str: ...
def typed_null(type_name: str, d: Dialect) -> str: ...
def to_date(timestamp_expr: str, d: Dialect) -> str: ...

# JSON
def json_check(col: str, d: Dialect) -> str:
    """Returns ``"CHECK ({col} IS NULL OR {col} IS JSON)"``."""

# Date/time
def epoch_seconds_between(later: str, earlier: str, d: Dialect) -> str: ...
def interval_days(n: int, d: Dialect) -> str: ...
def date_minus_days(date_expr: str, n: int, d: Dialect) -> str: ...

# DDL idempotency
def drop_table_if_exists(name: str, d: Dialect) -> str: ...
def drop_matview_if_exists(name: str, d: Dialect) -> str: ...
def drop_index_if_exists(name: str, d: Dialect) -> str: ...
def drop_view_if_exists(name: str, d: Dialect) -> str: ...

# Materialized views
def create_matview(name: str, body_sql: str, d: Dialect) -> str: ...
def refresh_matview(name: str, d: Dialect) -> str: ...
def analyze_table(name: str, d: Dialect) -> str: ...

# Recursive CTE
def with_recursive(d: Dialect) -> str:
    """Returns "WITH RECURSIVE" or "WITH" depending on dialect."""
```

22 helpers total. Oracle branch raises `NotImplementedError` until
P.3.

## §4 — Refactor scope for P.2.d

`common/l2/schema.py` only — the dataset SQL surfaces (`apps/<app>/datasets.py`)
move in P.4. Refactor the schema template + helper functions to call
the dialect helpers; the snapshot tests (`test_l2_schema_*`) verify
the emitted bytes are unchanged from the pre-refactor Postgres
output.

`common/l2/seed.py` is largely INSERT statement emission — the
dialect-sensitive bits there (bind syntax, column quoting) get
addressed in P.5 alongside the demo-apply work, not P.2.

## §5 — Decisions snapshot

- **Default dialect**: `Dialect.POSTGRES`. Existing callers continue
  to work without passing a dialect arg by way of a default-parameter
  pattern on every helper call site. The downstream callers
  (`common/l2/schema.py`'s public emit functions) gain an optional
  `dialect: Dialect = Dialect.POSTGRES` parameter that propagates
  inward.
- **Helper module location**: `src/quicksight_gen/common/sql/dialect.py`
  (new `common/sql/` subpackage; future SQL utilities can live
  alongside).
- **Test location**: `tests/test_sql_dialect.py` for unit tests on
  every helper; `tests/test_l2_schema_*.py` snapshot suite stays
  green as the regression check for the refactor.
- **Pyright strict**: the new module joins `[tool.pyright].include`
  in `pyproject.toml`. Helpers are simple functions with `Dialect`
  enum + `str` types — clean fit for strict mode.
