"""AY.4.b — unit tests for `render_captured_sql`.

The renderer that turns AY.4.a's captured `(sql, params)` pairs into
static SQL text. AY.4.d's `build_full_seed_sql` rewrite composes
`dry_run_capture(d)` + `ScenarioContext.compose(dry_run=True)` + this
renderer to produce the same SQL-script shape `emit_to_target`
writes today.
"""

from __future__ import annotations

import pytest

from recon_gen.common.spine import (
    DriftInvariant,
    ScenarioContext,
    dry_run_capture,
    render_captured_sql,
)
from recon_gen.common.sql import Dialect


# ---------------------------------------------------------------------------
# Literal escaping — every Python type the spine emits.
# ---------------------------------------------------------------------------


def test_renders_none_as_null() -> None:
    """`None` → bare `NULL` (NOT `'None'` — that's a real-world
    bug if it slips through)."""
    out = render_captured_sql(
        [("INSERT INTO t (a) VALUES (?)", (None,))],
        dialect=Dialect.SQLITE,
    )
    assert "VALUES (NULL)" in out
    assert "'None'" not in out


def test_renders_str_with_single_quote_doubling() -> None:
    """String containing a single quote must escape to two singles —
    matches the OLD `_sql_str(s)` shape."""
    out = render_captured_sql(
        [("INSERT INTO t (a) VALUES (?)", ("can't",))],
        dialect=Dialect.SQLITE,
    )
    assert "VALUES ('can''t')" in out


def test_renders_int_as_bare_numeric() -> None:
    out = render_captured_sql(
        [("INSERT INTO t (n) VALUES (?)", (42,))],
        dialect=Dialect.SQLITE,
    )
    assert "VALUES (42)" in out


def test_renders_float_as_bare_numeric() -> None:
    out = render_captured_sql(
        [("INSERT INTO t (n) VALUES (?)", (3.14,))],
        dialect=Dialect.SQLITE,
    )
    assert "VALUES (3.14)" in out


def test_renders_negative_amount() -> None:
    """Spine emits negative `amount_money` for Debit legs; the
    minus sign must survive."""
    out = render_captured_sql(
        [("INSERT INTO t (m) VALUES (?)", (-250.5,))],
        dialect=Dialect.SQLITE,
    )
    assert "VALUES (-250.5)" in out


def test_renders_bool_as_zero_or_one() -> None:
    out = render_captured_sql(
        [
            ("INSERT INTO t (b) VALUES (?)", (True,)),
            ("INSERT INTO t (b) VALUES (?)", (False,)),
        ],
        dialect=Dialect.SQLITE,
    )
    assert "VALUES (1)" in out
    assert "VALUES (0)" in out


def test_renders_json_string_with_inner_quotes() -> None:
    """The spine writes JSON metadata as a string param; quote-doubling
    must preserve the JSON structure."""
    json_blob = '{"scenario_id":"foo","sender_id":"acct-a"}'
    out = render_captured_sql(
        [("INSERT INTO t (m) VALUES (?)", (json_blob,))],
        dialect=Dialect.SQLITE,
    )
    # The outer quote wraps; inner double-quotes pass through untouched.
    assert '\'{"scenario_id":"foo","sender_id":"acct-a"}\'' in out


# ---------------------------------------------------------------------------
# Per-dialect placeholder dispatch.
# ---------------------------------------------------------------------------


def test_sqlite_dialect_substitutes_question_mark() -> None:
    out = render_captured_sql(
        [("INSERT INTO t (a, b) VALUES (?, ?)", ("foo", 42))],
        dialect=Dialect.SQLITE,
    )
    assert "VALUES ('foo', 42)" in out
    assert "?" not in out  # all placeholders substituted


def test_postgres_dialect_substitutes_percent_s() -> None:
    out = render_captured_sql(
        [("INSERT INTO t (a, b) VALUES (%s, %s)", ("foo", 42))],
        dialect=Dialect.POSTGRES,
    )
    assert "VALUES ('foo', 42)" in out
    assert "%s" not in out


def test_oracle_dialect_substitutes_numeric_placeholders() -> None:
    out = render_captured_sql(
        [("INSERT INTO t (a, b) VALUES (:1, :2)", ("foo", 42))],
        dialect=Dialect.ORACLE,
    )
    assert "VALUES ('foo', 42)" in out
    assert ":1" not in out
    assert ":2" not in out


def test_oracle_numeric_placeholders_respect_index_not_order() -> None:
    """Oracle's :N is explicit numeric — :2 then :1 should substitute
    out-of-order if the SQL puts them that way."""
    out = render_captured_sql(
        [("UPDATE t SET a = :2, b = :1", ("first", "second"))],
        dialect=Dialect.ORACLE,
    )
    assert "SET a = 'second', b = 'first'" in out


# ---------------------------------------------------------------------------
# Statement composition.
# ---------------------------------------------------------------------------


def test_concats_multiple_statements_with_semicolons() -> None:
    out = render_captured_sql(
        [
            ("INSERT INTO t (a) VALUES (?)", ("first",)),
            ("INSERT INTO t (a) VALUES (?)", ("second",)),
        ],
        dialect=Dialect.SQLITE,
    )
    assert "VALUES ('first');" in out
    assert "VALUES ('second');" in out


def test_empty_input_produces_empty_string() -> None:
    out = render_captured_sql([], dialect=Dialect.SQLITE)
    assert out == ""


def test_custom_statement_separator() -> None:
    out = render_captured_sql(
        [
            ("INSERT INTO t VALUES (?)", (1,)),
            ("INSERT INTO t VALUES (?)", (2,)),
        ],
        dialect=Dialect.SQLITE,
        statement_separator=";\n\n",
    )
    assert "VALUES (1);\n\nINSERT" in out


# ---------------------------------------------------------------------------
# Error surfaces — loud failures, not silent garbage.
# ---------------------------------------------------------------------------


def test_placeholder_count_mismatch_raises_loudly() -> None:
    """Too few params for the SQL's `?` count — surface immediately
    rather than emit a half-substituted statement."""
    with pytest.raises(ValueError, match="placeholder count mismatch"):
        render_captured_sql(
            [("INSERT INTO t (a, b) VALUES (?, ?)", ("only_one",))],
            dialect=Dialect.SQLITE,
        )


def test_oracle_out_of_range_placeholder_raises_loudly() -> None:
    with pytest.raises(ValueError, match="has no matching param"):
        render_captured_sql(
            [("INSERT INTO t VALUES (:5)", ("foo",))],
            dialect=Dialect.ORACLE,
        )


# ---------------------------------------------------------------------------
# Round-trip — DriftGenerator capture + render produces valid-looking SQL.
# ---------------------------------------------------------------------------


def test_round_trip_with_drift_generator_produces_valid_inserts() -> None:
    """End-to-end: capture a DriftGenerator's emit, render, assert the
    output is a string of INSERT statements with no leftover
    placeholders + recognizable spine emit shape."""
    ctx = ScenarioContext(scenario_id="test-ay4b-roundtrip")
    cap = dry_run_capture(Dialect.SQLITE)
    gen = DriftInvariant().scenario_for(
        "CustomerSubledger", magnitude=5.0,
    )
    captured = ctx.compose(cap, gen, dry_run=True)
    assert captured is not None
    sql = render_captured_sql(captured, dialect=Dialect.SQLITE)
    # No leftover placeholders.
    assert "?" not in sql
    # At least one INSERT INTO statement.
    assert "INSERT INTO" in sql
    # Single-quoted scenario_id appears in the JSON metadata literal.
    assert "test-ay4b-roundtrip" in sql
    # Statement terminator pattern.
    assert ";\n" in sql


# ---------------------------------------------------------------------
# BC.14 — Oracle TIMESTAMP wrapping for ANSI-form date literals.
# ---------------------------------------------------------------------

def test_oracle_wraps_timestamp_shaped_string_with_ansi_form() -> None:
    """`YYYY-MM-DD HH:MM:SS` (24-hour) is the spine's canonical
    stored-timestamp shape. Oracle's default NLS_DATE_FORMAT is
    `DD-MON-RR`; bare quoted strings of the spine's shape trip
    ORA-01843. BC.14 fix: wrap with ANSI `TIMESTAMP '...'` which
    Oracle accepts unambiguously."""
    out = render_captured_sql(
        [("INSERT INTO t (posting) VALUES (:1)", ("2026-05-24 12:00:00",))],
        dialect=Dialect.ORACLE,
    )
    assert "VALUES (TIMESTAMP '2026-05-24 12:00:00')" in out
    # NOT bare quoted (the pre-BC.14 shape that ORA-01843s).
    assert "VALUES ('2026-05-24 12:00:00')" not in out


def test_oracle_does_not_wrap_non_timestamp_strings() -> None:
    """Other strings (account IDs, JSON metadata, descriptions) stay
    bare-quoted. The wrap is shape-gated on
    `YYYY-MM-DD HH:MM:SS` exactly."""
    out = render_captured_sql(
        [("INSERT INTO t (id, descr) VALUES (:1, :2)",
          ("acct-001", "not a date"))],
        dialect=Dialect.ORACLE,
    )
    assert "'acct-001'" in out
    assert "'not a date'" in out
    assert "TIMESTAMP" not in out


def test_oracle_wraps_timestamp_with_microseconds() -> None:
    """Sub-second precision (e.g. CURRENT_TIMESTAMP-derived values) —
    the wrap regex allows optional `.NNNNNN` after seconds."""
    out = render_captured_sql(
        [("INSERT INTO t (ts) VALUES (:1)",
          ("2026-05-24 12:00:00.123456",))],
        dialect=Dialect.ORACLE,
    )
    assert "TIMESTAMP '2026-05-24 12:00:00.123456'" in out


def test_postgres_does_not_wrap_timestamp_strings() -> None:
    """PG accepts bare quoted timestamp strings (no NLS_DATE_FORMAT
    sensitivity). The wrap is Oracle-only — keep PG output unchanged."""
    out = render_captured_sql(
        [("INSERT INTO t (posting) VALUES (%s)", ("2026-05-24 12:00:00",))],
        dialect=Dialect.POSTGRES,
    )
    assert "VALUES ('2026-05-24 12:00:00')" in out
    assert "TIMESTAMP" not in out


def test_sqlite_does_not_wrap_timestamp_strings() -> None:
    """SQLite stores TEXT for timestamps; the wrap is Oracle-only."""
    out = render_captured_sql(
        [("INSERT INTO t (posting) VALUES (?)", ("2026-05-24 12:00:00",))],
        dialect=Dialect.SQLITE,
    )
    assert "VALUES ('2026-05-24 12:00:00')" in out
    assert "TIMESTAMP" not in out
