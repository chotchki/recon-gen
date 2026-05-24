"""AY.4.b — render captured (sql, params) pairs as static SQL text.

`ScenarioContext.compose(dry_run=True)` returns
`[(sql_with_placeholders, params_tuple), ...]` per AY.4.a. This
module walks that list and substitutes each placeholder with the
properly-escaped literal value, producing a static SQL script the
`build_full_seed_sql` path can write to disk or pipe to a real DB.

Per-dialect placeholder patterns:

  - SQLite: `?` — substitution in left-to-right order
  - Postgres: `%s` — substitution in left-to-right order
  - Oracle: `:1`, `:2`, ... — explicit numeric, mapped by index

Literal escaping is type-dispatched:

  - `None` → `NULL`
  - `str` → `'escaped'` (single-quote doubling)
  - `int` / `float` → bare numeric (no scientific notation; AY.4.b
    matches the OLD `_sql_str(money)` shape)
  - `bool` → `1` / `0` (no dialect uses bool yet but defensive)

What the renderer does NOT handle yet:

  - **Oracle TIMESTAMP wrapping.** The OLD `_sql_timestamp_literal`
    wraps Oracle timestamp values in `TIMESTAMP '...'`. The renderer
    can't tell which string is a timestamp without column context.
    AY.4.b ships SQLite + Postgres parity; Oracle output is
    syntactically valid (bare quoted strings) but Oracle's TIMESTAMP
    column may reject. AY.5's re-lock pass either accepts the bare
    form or adds a per-column wrapper at the spine emit layer.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from recon_gen.common.sql import Dialect


def render_captured_sql(
    captured: Iterable[tuple[str, tuple[object, ...]]],
    *,
    dialect: Dialect,
    statement_separator: str = ";\n",
) -> str:
    """Walk captured (sql, params) pairs + render each as a static
    SQL statement. Returns the concatenated script with each
    statement terminated by `statement_separator` (default `;\\n`).

    Use after `ScenarioContext.compose(dry_run=True)` to feed the
    output to `emit_to_target` / write-to-disk / pipe-to-psql.
    """
    return statement_separator.join(
        _render_one(sql, params, dialect) for sql, params in captured
    ) + (statement_separator if captured else "")


def _render_one(
    sql: str, params: tuple[object, ...], dialect: Dialect,
) -> str:
    """Substitute placeholders in `sql` with literal renderings of
    `params`. Picks the substitution strategy from `dialect`."""
    if dialect is Dialect.SQLITE:
        return _substitute_sequential(sql, params, "?")
    if dialect is Dialect.POSTGRES:
        return _substitute_sequential(sql, params, "%s")
    if dialect is Dialect.ORACLE:
        return _substitute_numeric(sql, params)
    raise ValueError(f"unknown dialect: {dialect!r}")


def _substitute_sequential(
    sql: str, params: tuple[object, ...], marker: str,
) -> str:
    """SQLite + Postgres: walk the SQL, replacing each `marker`
    occurrence with the next param's literal rendering in order.

    Uses index-tracked single-pass to avoid the bug where a literal
    value containing the marker (e.g., a string with `?` in it)
    would be re-substituted. Splits on `marker` to get the segments,
    then inserts literals between them.
    """
    parts = sql.split(marker)
    expected_placeholders = len(parts) - 1
    if expected_placeholders != len(params):
        raise ValueError(
            f"placeholder count mismatch: SQL has "
            f"{expected_placeholders} {marker!r} occurrences but "
            f"got {len(params)} params. SQL: {sql!r}"
        )
    out: list[str] = []
    for i, part in enumerate(parts):
        out.append(part)
        if i < len(params):
            out.append(_render_literal(params[i]))
    return "".join(out)


_ORACLE_PLACEHOLDER = re.compile(r":(\d+)")


def _substitute_numeric(
    sql: str, params: tuple[object, ...],
) -> str:
    """Oracle: replace each `:N` (1-indexed) with the corresponding
    param's literal rendering. Re-substitution-safe because the
    literal can't contain `:N` after rendering (numbers are bare,
    strings get single-quoted).
    """
    def _sub(m: "re.Match[str]") -> str:
        idx = int(m.group(1)) - 1  # 1-indexed → 0-indexed
        if idx < 0 or idx >= len(params):
            raise ValueError(
                f"Oracle placeholder :{idx + 1} has no matching param "
                f"(got {len(params)} params). SQL: {sql!r}"
            )
        return _render_literal(params[idx])
    return _ORACLE_PLACEHOLDER.sub(_sub, sql)


def _render_literal(value: object) -> str:
    """Render a Python value as a SQL literal token.

    - `None` → `NULL`
    - `bool` → `1` / `0` (defensive; spine doesn't emit bools today)
    - `int` / `float` → bare numeric
    - `str` → `'escaped'` (single-quote doubled)
    - other → fall through to `str(value)` quoted (defensive — any
      future date/Decimal/etc. lands here; consumers should add a
      type branch rather than rely on the fallback)
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        # bool MUST come before int (bool is a subclass of int in Python).
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    # Defensive fallback — quote stringification.
    return "'" + str(value).replace("'", "''") + "'"
