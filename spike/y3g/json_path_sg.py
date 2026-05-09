"""Y.3.g spike — sqlglot test of harder dialect cases.

Tests sqlglot's transpile on cases where our current dialect helpers
do real cross-dialect work:

1. **JSON path extraction** — PG `JSON_VALUE(metadata, '$.field')`
   vs Oracle `JSON_VALUE(metadata, '$.field')` (same SQL/JSON syntax)
   vs SQLite `json_extract(metadata, '$.field')` (JSON1 ext).

2. **Recursive CTE** — PG/SQLite `WITH RECURSIVE` vs Oracle bare `WITH`.

3. **Date arithmetic** — PG `expr - INTERVAL '5 day'` vs Oracle `expr - 5`
   vs SQLite `date(expr, '-5 days')`.

These are the cross-dialect smell — if sqlglot handles them
transparently, the case for transpile-mode adoption strengthens
significantly.
"""

from __future__ import annotations

import sqlglot

# Test 1: JSON path extraction (PG/Oracle natively, SQLite JSON1)
JSON_PG = """
SELECT
    id,
    JSON_VALUE(metadata, '$.customer_id') AS customer_id,
    JSON_VALUE(metadata, '$.amount') AS amount
FROM transactions
WHERE JSON_VALUE(metadata, '$.status') = 'pending'
"""

# Test 2: Recursive CTE walking parent chain
RECURSIVE_PG = """
WITH RECURSIVE chain AS (
    SELECT id, parent_id, 0 AS depth FROM transfers WHERE parent_id IS NULL
    UNION ALL
    SELECT t.id, t.parent_id, c.depth + 1
    FROM transfers t JOIN chain c ON t.parent_id = c.id
)
SELECT * FROM chain
"""

# Test 3: Date arithmetic — subtract N days
DATE_ARITH_PG = """
SELECT * FROM events
WHERE created_at >= CURRENT_DATE - INTERVAL '7 day'
"""


def _try_transpile(label: str, sql: str) -> None:
    print(f"\n=== {label} ===")
    print("--- source (postgres) ---")
    print(sql.strip())
    for dialect in ("oracle", "sqlite"):
        print(f"--- transpiled to {dialect} ---")
        try:
            out = sqlglot.transpile(sql, read="postgres", write=dialect, pretty=True)[0]
            print(out)
        except Exception as e:
            print(f"FAILED: {type(e).__name__}: {e}")


def main() -> None:
    _try_transpile("JSON path extraction", JSON_PG)
    _try_transpile("Recursive CTE", RECURSIVE_PG)
    _try_transpile("Date arithmetic", DATE_ARITH_PG)


if __name__ == "__main__":
    main()
