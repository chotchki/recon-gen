"""Y.3.g spike — Account Network dataset, ported to SQLAlchemy Core.

Throwaway. Run via `.venv/bin/python spike/y3g/account_network_sa.py`.

Builds the equivalent of `build_account_network_dataset`'s SQL via a
SQLAlchemy Core expression tree, compiles per-dialect, applies the
QuickSight `:bindparam` → `<<$paramName>>` substitution, and writes
output to spike/y3g/sa-output/<dialect>.sql.

Findings the emitted output should let us judge:

1. Lines of Python required to express the query (vs ~20 lines of
   f-strings in the current builder).
2. Lines of SQL emitted (vs current emitter — see baseline/).
3. Cross-dialect uniformity (does SA's compile produce the same
   shape on PG / Oracle / SQLite, or does each dialect emit something
   meaningfully different?).
4. How the QS `<<$paramName>>` substitution layer composes with SA's
   bindparam compile output.
5. Whether the column-case story is solved by SA's quoting rules.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import (
    Column,
    MetaData,
    String,
    Table,
    Integer,
    Numeric,
    bindparam,
    literal_column,
    or_,
    select,
)
from sqlalchemy.dialects import oracle, postgresql, sqlite
from sqlalchemy.sql.compiler import SQLCompiler

OUT_DIR = Path(__file__).parent / "sa-output"

PREFIX = "spec_example"
DIALECTS = {
    "postgres": postgresql.dialect(),
    "oracle": oracle.dialect(),
    "sqlite": sqlite.dialect(),
}

# Param names matching the existing P_INV_ANETWORK_ANCHOR / _MIN_AMOUNT.
P_ANCHOR = "pInvANetworkAnchor"
P_MIN_AMOUNT = "pInvANetworkMinAmount"


def _build_query() -> "select":  # type: ignore[explicit-any]: SA Select is generic
    """Return a SQLAlchemy Select equivalent to the current Account Network SQL.

    Mirrors the f-string emitter's structure: a base SELECT over the
    matview that adds two display-string concats, wrapped as a CTE so
    the outer WHERE can reference the new columns.
    """
    metadata = MetaData()

    # Declare just the matview columns we touch. Real-world adoption
    # would either reflect from a live DB at apply time or maintain a
    # canonical Table-per-matview registry; both are tractable.
    edges = Table(
        f"{PREFIX}_inv_money_trail_edges",
        metadata,
        Column("source_account_name", String),
        Column("source_account_id", String),
        Column("target_account_name", String),
        Column("target_account_id", String),
        Column("hop_amount", Numeric),
        # Other columns the matview projects (root_transfer_id,
        # transfer_id, etc) are caught by the `*` projection below;
        # we don't need to spell them out for the query to work,
        # but the compiled SQL will only enumerate what we declare
        # if we use `select(edges)` over `select("*")`.
    )

    # Base CTE: e.* + display columns. Reference the *alias* columns for
    # the concat expressions so SA doesn't pull both the alias AND the
    # original table into the FROM clause (the Cartesian-product bug
    # the first iteration produced).
    e = edges.alias("e")
    source_display = (
        e.c.source_account_name
        .concat(literal_column("' ('"))
        .concat(e.c.source_account_id)
        .concat(literal_column("')'"))
        .label("source_display")
    )
    target_display = (
        e.c.target_account_name
        .concat(literal_column("' ('"))
        .concat(e.c.target_account_id)
        .concat(literal_column("')'"))
        .label("target_display")
    )
    base = (
        select(literal_column("e.*"), source_display, target_display)
        .select_from(e)
        .cte("base")
    )

    # Outer SELECT against the CTE with the parameter-substituted WHERE
    # clauses. Bindparams get compiled to `:name` and post-processed to
    # `<<$name>>` for QuickSight.
    anchor_param = bindparam(P_ANCHOR)
    min_amount_param = bindparam(P_MIN_AMOUNT, type_=Integer)
    return (
        select(literal_column("*"))
        .select_from(base)
        .where(
            or_(
                literal_column("source_display") == anchor_param,
                literal_column("target_display") == anchor_param,
            )
        )
        .where(literal_column("hop_amount") >= min_amount_param)
    )


def _compile_to_sql(stmt, dialect_name: str) -> str:  # type: ignore[explicit-any]: stmt is SA Select
    """Compile a SA Select to a literal SQL string for the given dialect.

    Uses `compile_kwargs={"literal_binds": False}` so bindparams stay as
    `:name` placeholders (we'll post-process to `<<$name>>` next).
    """
    dialect = DIALECTS[dialect_name]
    compiled: SQLCompiler = stmt.compile(  # type: ignore[assignment]: SA returns Compiled but typeshed widens
        dialect=dialect,
        compile_kwargs={"literal_binds": False},
    )
    return str(compiled)


def _swap_binds_for_qs(sql: str, param_names: list[str], dialect_name: str) -> str:
    """Replace dialect-specific bind placeholders with `<<$paramName>>`.

    Per-dialect bind syntax SA emits:
    - postgres: ``%(name)s`` (psycopg2 paramstyle="format")
    - oracle:   ``:name`` (cx_Oracle paramstyle="named")
    - sqlite:   ``?`` (positional, paramstyle="qmark") — a real
                problem for QS since QS needs named substitution. Would
                need to compile with a different dialect-paramstyle
                override OR use literal_binds (loses parameterization).

    For now: handle PG + Oracle. SQLite returns the unmodified output
    so the spike findings can call out the gap.
    """
    if dialect_name == "postgres":
        for name in param_names:
            sql = sql.replace(f"%({name})s", f"<<${name}>>")
        return sql
    if dialect_name == "oracle":
        for name in param_names:
            sql = sql.replace(f":{name}", f"<<${name}>>")
        return sql
    # sqlite — positional binds; can't recover the names without more setup
    return sql


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    stmt = _build_query()
    for dialect_name in DIALECTS:
        raw = _compile_to_sql(stmt, dialect_name)
        qs_sql = _swap_binds_for_qs(raw, [P_ANCHOR, P_MIN_AMOUNT], dialect_name)
        path = OUT_DIR / f"{dialect_name}.sql"
        path.write_text(qs_sql + "\n")
        line_count = len(qs_sql.splitlines())
        char_count = len(qs_sql)
        print(
            f"[{dialect_name}] {line_count} lines, {char_count} chars → {path}"
        )


if __name__ == "__main__":
    main()
