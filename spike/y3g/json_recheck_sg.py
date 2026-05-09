"""Y.3.g spike — re-probe sqlglot JSON path extraction with proper inputs.

First probe used `JSON_VALUE(metadata, '$.x')` as PG source. PG doesn't
natively have `JSON_VALUE` as a function — it uses `->`, `->>`, or
`jsonb_extract_path_text`. sqlglot's PG parser may not have tagged
`JSON_VALUE` as JSON-path-extraction in that probe.

This re-probe tests several input shapes:

A. Source dialect = oracle, expression = `JSON_VALUE(metadata, '$.x')`
   (Oracle-native JSON path extraction). Does sqlglot transpile to
   PG/SQLite correctly?

B. Source = postgres, expression = `metadata->>'field'` (PG-native).
   Does sqlglot transpile to Oracle/SQLite?

C. Source = postgres, expression = `JSON_EXTRACT(metadata, '$.x')`
   (sqlglot's canonical name). Does it transpile correctly to all 3?

D. Source = sqlite, expression = `json_extract(metadata, '$.x')`.
   Does it transpile to PG/Oracle?
"""

from __future__ import annotations

import sqlglot


def probe(label: str, sql: str, source: str, targets: list[str]) -> None:
    print(f"\n=== {label} ===")
    print(f"--- source ({source}) ---")
    print(sql.strip())
    for tgt in targets:
        print(f"--- → {tgt} ---")
        try:
            out = sqlglot.transpile(sql, read=source, write=tgt, pretty=True)[0]
            print(out)
        except Exception as e:
            print(f"FAILED: {type(e).__name__}: {e}")


def main() -> None:
    probe(
        "A. Oracle JSON_VALUE → PG/SQLite",
        "SELECT JSON_VALUE(metadata, '$.x') AS x FROM t",
        source="oracle",
        targets=["postgres", "sqlite"],
    )
    probe(
        "B. PG ->> operator → Oracle/SQLite",
        "SELECT metadata->>'x' AS x FROM t",
        source="postgres",
        targets=["oracle", "sqlite"],
    )
    probe(
        "C. JSON_EXTRACT (sqlglot canonical) → PG/Oracle/SQLite",
        "SELECT JSON_EXTRACT(metadata, '$.x') AS x FROM t",
        source="postgres",
        targets=["postgres", "oracle", "sqlite"],
    )
    probe(
        "D. SQLite json_extract → PG/Oracle",
        "SELECT json_extract(metadata, '$.x') AS x FROM t",
        source="sqlite",
        targets=["postgres", "oracle"],
    )


if __name__ == "__main__":
    main()
