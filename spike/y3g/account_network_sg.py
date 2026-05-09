"""Y.3.g spike — Account Network dataset, ported via sqlglot transpile + build APIs.

Throwaway. Run via `.venv/bin/python spike/y3g/account_network_sg.py`.

Two approaches under one roof:

A. **Transpile-from-PG.** Take our existing PG SQL string (with QS
   placeholders pre-swapped to `:name`), parse via sqlglot, transpile
   to oracle/sqlite, swap placeholders back. Lightest-touch — keeps
   the SQL human-readable in source form.

B. **Build via expression API.** Use `sqlglot.exp.Select / With / ...`
   to construct the query in Python, render per dialect. Closer
   conceptually to the SQLAlchemy port — direct comparison of the
   builder ergonomics.

Writes output to spike/y3g/sg-output/transpile/<dialect>.sql and
spike/y3g/sg-output/build_via_api/<dialect>.sql.
"""

from __future__ import annotations

from pathlib import Path

import sqlglot
from sqlglot import exp

OUT_DIR = Path(__file__).parent / "sg-output"
TRANSPILE_DIR = OUT_DIR / "transpile"
BUILD_DIR = OUT_DIR / "build_via_api"

PREFIX = "spec_example"

P_ANCHOR = "pInvANetworkAnchor"
P_MIN_AMOUNT = "pInvANetworkMinAmount"

# Source SQL — the same shape the current f-string emitter produces
# for Postgres. QS placeholders pre-swapped to `:name` so sqlglot's
# parser doesn't choke on `<<$...>>` (it parses `<<` as bitwise-shift).
SOURCE_PG_SQL = (
    f"WITH base AS (\n"
    f"SELECT\n"
    f"    e.*,\n"
    f"    source_account_name || ' (' || source_account_id || ')' AS source_display,\n"
    f"    target_account_name || ' (' || target_account_id || ')' AS target_display\n"
    f"FROM {PREFIX}_inv_money_trail_edges e\n"
    f")\n"
    f"SELECT * FROM base\n"
    f"WHERE 1=1\n"
    f"  AND (\n"
    f"    source_display = :{P_ANCHOR}\n"
    f"    OR target_display = :{P_ANCHOR}\n"
    f"  )\n"
    f"  AND hop_amount >= :{P_MIN_AMOUNT}"
)


def _qs_swap(sql: str) -> str:
    """Swap dialect-specific bind output back to `<<$paramName>>` for QS.

    sqlglot's transpile-to-postgres rewrites `:name` → `%(name)s` (psycopg2
    paramstyle); other dialects preserve `:name`. Both surface area covered.
    """
    for name in (P_ANCHOR, P_MIN_AMOUNT):
        sql = sql.replace(f":{name}", f"<<${name}>>")
        sql = sql.replace(f"%({name})s", f"<<${name}>>")
    return sql


def _transpile_to(dialect: str) -> str:
    """Approach A: parse PG → transpile to target dialect → QS-swap."""
    transpiled = sqlglot.transpile(
        SOURCE_PG_SQL, read="postgres", write=dialect, pretty=True,
    )[0]
    return _qs_swap(transpiled)


def _build_via_api():  # type: ignore[explicit-any]: sqlglot exp is dynamic
    """Approach B: construct the query via sqlglot's expression API."""
    edges_table = exp.Table(
        this=exp.to_identifier(f"{PREFIX}_inv_money_trail_edges"),
        alias=exp.TableAlias(this=exp.to_identifier("e")),
    )

    # Display string concats: e.source_account_name || ' (' || e.source_account_id || ')'
    def _display_concat(name_col: str, id_col: str, alias: str) -> exp.Alias:
        chain = exp.func(
            "CONCAT",
            exp.column(name_col, table="e"),
            exp.Literal.string(" ("),
            exp.column(id_col, table="e"),
            exp.Literal.string(")"),
        )
        return exp.alias_(chain, alias)

    source_display = _display_concat(
        "source_account_name", "source_account_id", "source_display",
    )
    target_display = _display_concat(
        "target_account_name", "target_account_id", "target_display",
    )

    base_select = (
        exp.Select()
        .select(exp.Star(table=exp.to_identifier("e")))
        .select(source_display)
        .select(target_display)
        .from_(edges_table)
    )

    # CTE wrapping
    base_cte = exp.CTE(
        this=base_select,
        alias=exp.TableAlias(this=exp.to_identifier("base")),
    )

    # Outer SELECT with WHERE
    outer = (
        exp.Select()
        .select(exp.Star())
        .from_("base")
        .where(
            exp.or_(
                exp.column("source_display").eq(
                    exp.Placeholder(this=P_ANCHOR),
                ),
                exp.column("target_display").eq(
                    exp.Placeholder(this=P_ANCHOR),
                ),
            )
        )
        .where(
            exp.column("hop_amount") >= exp.Placeholder(this=P_MIN_AMOUNT),
        )
        .with_(base_cte.alias_or_name, as_=base_select)
    )
    return outer


def _build_to(dialect: str) -> str:
    """Approach B: build via API → render per dialect → QS-swap."""
    stmt = _build_via_api()
    rendered = stmt.sql(dialect=dialect, pretty=True)
    return _qs_swap(rendered)


def main() -> None:
    TRANSPILE_DIR.mkdir(parents=True, exist_ok=True)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    for dialect in ("postgres", "oracle", "sqlite"):
        # Approach A — transpile-from-PG
        transpiled = _transpile_to(dialect)
        path_a = TRANSPILE_DIR / f"{dialect}.sql"
        path_a.write_text(transpiled + "\n")
        print(
            f"[transpile/{dialect}] {len(transpiled.splitlines())} lines, "
            f"{len(transpiled)} chars → {path_a}"
        )

        # Approach B — build via expression API
        try:
            built = _build_to(dialect)
            path_b = BUILD_DIR / f"{dialect}.sql"
            path_b.write_text(built + "\n")
            print(
                f"[build/{dialect}] {len(built.splitlines())} lines, "
                f"{len(built)} chars → {path_b}"
            )
        except Exception as e:
            print(f"[build/{dialect}] FAILED: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
