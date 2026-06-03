"""SQLite -> DuckDB SQL translator for the CA.0 spike.

Goal: take the SQL the production emitter produces for ``Dialect.SQLITE``
and rewrite the small set of SQLite-specific function call shapes to
their DuckDB equivalents, so the spike can validate "can DuckDB run
this matview chain at all + how fast" without first re-plumbing all
of common/sql/dialect.py.

This is NOT a production translator. The CA.1+ migration would do the
right thing by adding a ``Dialect.DUCKDB`` arm to every helper in
common/sql/dialect.py. The translator exists to short-circuit that work
for the spike's reconnaissance question.

Translations applied:

- ``INTEGER PRIMARY KEY AUTOINCREMENT`` → ``BIGINT DEFAULT nextval('seq_<table>')``
  plus a preceding ``CREATE SEQUENCE`` for each affected table. The seed
  INSERTs don't provide ``entry`` explicitly, so a DEFAULT is required;
  DuckDB lacks SQLite's special-cased ``INTEGER PRIMARY KEY AUTOINCREMENT``.

- ``julianday(a)`` arithmetic — the pattern
  ``((julianday(a) - julianday(b)) * 86400)`` is "seconds between" and
  becomes ``EXTRACT(EPOCH FROM (a - b))``. The harness also handles bare
  ``julianday(x)`` in window ORDER BYs by replacing with the column itself
  (DuckDB's RANGE clause accepts ``INTERVAL`` directly on a DATE).

- ``datetime(x, 'start of day')`` → ``date_trunc('day', x)``.

- ``date(x, '-N days')`` → ``(x - INTERVAL 'N day')`` — date arithmetic.

- ``RANGE BETWEEN N PRECEDING`` over julianday → ``RANGE BETWEEN INTERVAL
  'N' DAY PRECEDING`` over the bare column.

- ``json_extract(col, '$.path')`` → ``json_extract_string(col, '$.path')``.
  SQLite's ``json_extract`` returns text for scalar leaves; DuckDB's
  ``json_extract`` returns the raw JSON (quoted strings). DuckDB's
  ``json_extract_string`` matches SQLite's unwrap-string behavior.

- DuckDB's parser doesn't accept the SQLite ``CREATE TABLE x AS WITH
  cte AS (...) SELECT ...`` shape if the SQL has a trailing semicolon
  mid-statement. The harness's statement-splitter handles this — same
  shape as BX.

Anything not on this list is passed through unchanged. If DuckDB rejects
something not covered here, the spike has identified a portability
issue we'd need to address before CA.1.
"""

from __future__ import annotations

import re


def translate_sqlite_to_duckdb(sql: str) -> str:
    """Best-effort translation of SQLite-shaped SQL to DuckDB.

    Returns the rewritten SQL. The translation is regex-based and
    therefore brittle on edge cases; the spike's matview SQL is the
    only target. If DuckDB rejects the output, we read the error and
    add another rewrite here.
    """
    out = sql

    # INTEGER PRIMARY KEY AUTOINCREMENT → BIGINT with sequence-fed default.
    # The harness creates the sequences separately because DuckDB needs
    # them defined before the CREATE TABLE references them.
    # Pattern: "entry  INTEGER PRIMARY KEY AUTOINCREMENT,"
    # Replace with: "entry  BIGINT DEFAULT nextval('seq_<surrounding-table>')
    # PRIMARY KEY,"
    # Use a placeholder; the post-pass will replace with the right seq name.
    # Simpler: replace AUTOINCREMENT with PRIMARY KEY + DEFAULT nextval
    # off a fixed per-table sequence name. We extract the table from the
    # preceding CREATE TABLE context.
    out = _rewrite_autoincrement_columns(out)

    # julianday differences: ((julianday(A) - julianday(B)) * 86400) →
    # EXTRACT(EPOCH FROM (A - B)). A and B can themselves contain
    # parentheses (e.g. ``(SELECT ...)``), so we balanced-paren match.
    out = _rewrite_julianday_seconds_diff(out)

    # Standalone julianday inside ORDER BY ... RANGE BETWEEN N PRECEDING:
    # the BX/BZ shape is
    #   ORDER BY julianday(posted_day) RANGE BETWEEN 1 PRECEDING AND CURRENT ROW
    # On DuckDB we want INTERVAL on the bare DATE:
    #   ORDER BY posted_day RANGE BETWEEN INTERVAL '1' DAY PRECEDING AND CURRENT ROW
    out = re.sub(
        r"ORDER BY julianday\(([^()]+?)\)\s+RANGE BETWEEN (\d+) PRECEDING AND CURRENT ROW",
        r"ORDER BY \1 RANGE BETWEEN INTERVAL '\2' DAY PRECEDING AND CURRENT ROW",
        out,
    )

    # datetime(X, 'start of day') → date_trunc('day', X). DuckDB returns
    # TIMESTAMP at the day boundary, which matches SQLite's text shape
    # semantically and joins/groups correctly.
    out = re.sub(
        r"datetime\(([^,]+?),\s*'start of day'\)",
        r"date_trunc('day', \1)",
        out,
    )

    # date(X, '-N days') → (X - INTERVAL 'N day')
    out = re.sub(
        r"date\(([^,]+?),\s*'-(\d+)\s+days?'\)",
        r"(\1 - INTERVAL '\2 day')",
        out,
    )

    # json_extract(col, '$.path') → json_extract_string. The SQLite
    # contract for json_extract on a scalar leaf is "return TEXT"; DuckDB
    # split this into two functions and json_extract_string is the right
    # one for our use sites.
    out = re.sub(
        r"\bjson_extract\(",
        "json_extract_string(",
        out,
    )

    # DuckDB doesn't have STDDEV_SAMP as an overrideable function; it
    # has stddev_samp natively. The BZ.0 harness registered a Python
    # aggregate; DuckDB ships the SQL-standard one. No rewrite needed.

    # SQLite's ``ANALYZE <table>;`` works in DuckDB too (it has ANALYZE).
    # No rewrite needed.

    return out


_AUTOINC_PATTERN = re.compile(
    r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT", re.IGNORECASE,
)


def _match_balanced_parens(s: str, start: int) -> int:
    """Given ``s`` and ``start`` pointing at an open paren ``(``, return
    the index AFTER the matching close paren, or -1 if unbalanced.
    """
    depth = 0
    i = start
    while i < len(s):
        c = s[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def _rewrite_julianday_seconds_diff(sql: str) -> str:
    """Find ``((julianday(A) - julianday(B)) * 86400)`` and rewrite as
    ``EXTRACT(EPOCH FROM ((A) - (B)))``.

    Balanced-paren-aware: A or B may themselves contain parenthesized
    expressions (e.g. ``(SELECT value FROM ... WHERE key='as_of')``).
    """
    out: list[str] = []
    i = 0
    n = len(sql)
    target = "((julianday("
    while i < n:
        j = sql.find(target, i)
        if j < 0:
            out.append(sql[i:])
            break
        out.append(sql[i:j])
        # j points at "((julianday("; the inner julianday's open paren
        # is at j+len("((julianday(") - 1 = j + 11
        first_julian_paren_open = j + len("((julianday(") - 1
        first_close = _match_balanced_parens(sql, first_julian_paren_open)
        if first_close < 0:
            out.append(sql[j:])
            break
        # A is the substring between the open + close (exclusive).
        a_expr = sql[first_julian_paren_open + 1 : first_close - 1]
        # After the first ")": expect " - julianday(" — possibly with
        # whitespace.
        rest = sql[first_close:]
        m = re.match(r"\s*-\s*julianday\(", rest)
        if m is None:
            # Not the shape we expect; emit '(' and continue past it.
            out.append(sql[j])
            i = j + 1
            continue
        second_julian_paren_open = first_close + m.end() - 1
        second_close = _match_balanced_parens(sql, second_julian_paren_open)
        if second_close < 0:
            out.append(sql[j:])
            break
        b_expr = sql[second_julian_paren_open + 1 : second_close - 1]
        # After the second ")": expect ") * 86400)" — possibly with
        # whitespace.
        rest2 = sql[second_close:]
        m2 = re.match(r"\)\s*\*\s*86400\)", rest2)
        if m2 is None:
            out.append(sql[j])
            i = j + 1
            continue
        end = second_close + m2.end()
        out.append(
            f"EXTRACT(EPOCH FROM (CAST(({a_expr}) AS TIMESTAMP) "
            f"- CAST(({b_expr}) AS TIMESTAMP)))"
        )
        i = end
    return "".join(out)


def _rewrite_autoincrement_columns(sql: str) -> str:
    """Replace ``INTEGER PRIMARY KEY AUTOINCREMENT`` with a
    sequence-fed BIGINT default, and prepend the necessary
    CREATE SEQUENCE for each affected table.

    Looks back at the most recent ``CREATE TABLE <name>`` before each
    occurrence to derive the sequence name. Doesn't preserve PRIMARY
    KEY here — the existing schema declares the table-level UNIQUE
    elsewhere (matching SQLite's PRIMARY KEY enforcement); for DuckDB
    we ALSO want the column to be PRIMARY KEY since DuckDB doesn't
    have SQLite's special "INTEGER PRIMARY KEY = rowid alias" rule.
    """
    out_parts: list[str] = []
    seq_creates: list[str] = []
    last_end = 0
    for m in _AUTOINC_PATTERN.finditer(sql):
        # Look back for nearest CREATE TABLE <name>.
        head = sql[:m.start()]
        tm = list(re.finditer(
            r"CREATE\s+TABLE\s+([A-Za-z_][A-Za-z0-9_]*)",
            head, re.IGNORECASE,
        ))
        if not tm:
            # Pass through unmodified if we can't find the table name.
            out_parts.append(sql[last_end:m.end()])
            last_end = m.end()
            continue
        table_name = tm[-1].group(1)
        seq_name = f"seq_{table_name}_entry"
        # Schedule the sequence for top-of-file creation.
        seq_creates.append(f"CREATE SEQUENCE IF NOT EXISTS {seq_name};")
        out_parts.append(sql[last_end:m.start()])
        out_parts.append(
            f"BIGINT DEFAULT nextval('{seq_name}') PRIMARY KEY"
        )
        last_end = m.end()
    out_parts.append(sql[last_end:])
    rewritten = "".join(out_parts)
    if seq_creates:
        # Prepend the sequence creates (deduped).
        seen: set[str] = set()
        prepend: list[str] = []
        for s in seq_creates:
            if s not in seen:
                prepend.append(s)
                seen.add(s)
        rewritten = "\n".join(prepend) + "\n" + rewritten
    return rewritten


def translate_sqlite_to_duckdb_pg_csb(sql: str) -> str:
    """Variant translation that ALSO reverts the BZ.0 scratch-table
    body for ``computed_subledger_balance`` back to the original
    correlated-subquery shape (the one PG + Oracle use). Used by the
    bonus probe — measures whether DuckDB handles the original shape
    natively (predicted by the audit).

    Implementation: applies the standard SQLite→DuckDB translation,
    then surgically rewrites the BZ.0 scratch section back to the
    PG-style single CREATE TABLE AS body.
    """
    out = translate_sqlite_to_duckdb(sql)

    # The BZ.0 scratch section starts with the comment marker
    # "-- BZ.0 (SQLite):" and ends after the matview's index. Replace
    # the whole block with the PG-style body.
    # Capture the prefix from the existing scratch-table name.
    m = re.search(r"DROP TABLE IF EXISTS ([a-zA-Z0-9_]+?)_csb_scratch", out)
    if m is None:
        # Already the PG shape (or unexpected output); return as-is.
        return out
    prefix = m.group(1)

    # Replace EVERYTHING from the BZ.0 (SQLite) marker through the
    # final scratch DROP at the end of the section with the PG-style
    # body (the same body PG/Oracle would have emitted).
    pg_body = (
        f"-- CA.0 spike override: original correlated-SUM body (PG/Oracle\n"
        f"-- shape) running on DuckDB to test the vectorized executor's\n"
        f"-- native handling.\n"
        f"CREATE TABLE {prefix}_computed_subledger_balance AS\n"
        f"SELECT\n"
        f"    sb.account_id,\n"
        f"    sb.business_day_start,\n"
        f"    sb.business_day_end,\n"
        f"    sb.account_parent_role,\n"
        f"    COALESCE((\n"
        f"        SELECT SUM(tx.amount_money)\n"
        f"        FROM {prefix}_current_transactions tx\n"
        f"        WHERE tx.account_id = sb.account_id\n"
        f"          AND tx.status = 'Posted'\n"
        f"          AND tx.posting <= sb.business_day_end\n"
        f"    ), 0) AS computed_balance\n"
        f"FROM {prefix}_current_daily_balances sb\n"
        f"WHERE sb.account_scope = 'internal'\n"
        f"  AND sb.account_parent_role IS NOT NULL;\n"
        f"CREATE INDEX idx_{prefix}_csb_account_day\n"
        f"    ON {prefix}_computed_subledger_balance (account_id, business_day_start);\n"
    )
    # Match the WHOLE BZ.0 block: from the BZ.0 marker comment through
    # the trailing scratch-cleanup DROP. The block contains TWO DROP
    # statements (pre + post) — anchor on the "Scratch cleanup" comment
    # so we always match the trailing one (and a non-greedy `.*?` from
    # the BZ.0 head to the cleanup comment + final DROP).
    pattern = re.compile(
        r"-- BZ\.0 \(SQLite\): scratch \+ index sidesteps"
        r".*?"
        r"-- Scratch cleanup — transient table is no longer needed\.\s*\n"
        rf"DROP TABLE IF EXISTS {prefix}_csb_scratch;",
        re.DOTALL,
    )
    if pattern.search(out) is None:
        raise RuntimeError(
            "translate_sqlite_to_duckdb_pg_csb: BZ.0 block pattern did "
            "not match — the SQLite emitter may have changed shape. "
            "Update the regex in spike/ca_0_duckdb_spike/translate.py."
        )
    # NB: there are TWO copies of the BZ.0 block in a full schema+refresh
    # bundle (initial CREATE from emit_schema + DROP+CREATE from
    # refresh_matviews_sql). Replace ALL occurrences.
    out = pattern.sub(pg_body, out)
    return out
