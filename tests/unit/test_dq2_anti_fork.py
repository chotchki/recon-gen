"""DQ.2.3 — the DbObject module must NOT branch on a specific SQL dialect.

Every per-dialect decision DELEGATES to ``common/sql/dialect.py``, which
owns the ~60 hand-won gotchas (json_extract_string-not-JSON_VALUE, the
Oracle CONNECT-BY spine, the money_trail constant-fold trap, matview-as-
CTAS on DuckDB). A ``Dialect.POSTGRES`` / ``.ORACLE`` / ``.DUCKDB``
reference inside ``db_objects.py`` means the dispatch layer has started
re-implementing (forking) that logic, where it will silently rot out of
sync with dialect.py — the single self-inflicted risk of the hand-roll
approach (DQ.0 §7 / the DQ.1 review's top honest-risk).

The module may still ANNOTATE with the ``Dialect`` type (a bare ``Name``
reference) and dispatch on ``DbObjectKind`` — only a specific-MEMBER
access is the fork smell.
"""
from __future__ import annotations

import ast
from pathlib import Path

_DB_OBJECTS = (
    Path(__file__).resolve().parents[2]
    / "src" / "recon_gen" / "common" / "db_objects.py"
)
#: SQL dialect enum members. Referencing any of these in db_objects.py is
#: the fork smell (SQLITE is retired but kept so a resurrected reference
#: still trips).
_DIALECT_MEMBERS = frozenset({"POSTGRES", "ORACLE", "DUCKDB", "SQLITE"})


def _dialect_member_hits(source: str) -> list[tuple[int, str]]:
    """Line numbers + labels of every ``Dialect.<MEMBER>`` attribute
    access in ``source`` (a bare ``Dialect`` type annotation is NOT a
    hit — only a member access is)."""
    tree = ast.parse(source)
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "Dialect"
            and node.attr in _DIALECT_MEMBERS
        ):
            hits.append((node.lineno, f"Dialect.{node.attr}"))
    return hits


def test_dq2_db_objects_never_branches_on_dialect() -> None:
    hits = _dialect_member_hits(_DB_OBJECTS.read_text(encoding="utf-8"))
    assert not hits, (
        f"db_objects.py references specific SQL Dialect member(s) "
        f"{[h[1] for h in hits]} at line(s) {[h[0] for h in hits]} — the "
        "DbObject dispatch layer must DELEGATE every per-dialect decision "
        "to common/sql/dialect.py, never fork it (DQ.2.3). Annotate with "
        "the Dialect type and dispatch on DbObjectKind; route the dialect "
        "choice through a dialect.py helper (drop_matview_if_exists, "
        "refresh_matview, matview_create_keyword, …)."
    )


def test_dq2_anti_fork_lint_finds_a_planted_fork() -> None:
    """The lint must FIRE on a planted per-dialect branch, so a future
    refactor of the AST walker can't silently no-op the guard
    (the cheapest-validation-must-fire discipline)."""
    planted = (
        "from recon_gen.common.sql import Dialect\n"
        "def emit(self, dialect):\n"
        "    if dialect is Dialect.POSTGRES:\n"
        "        return 'REFRESH MATERIALIZED VIEW'\n"
        "    return 'CREATE TABLE AS'\n"
    )
    hits = _dialect_member_hits(planted)
    assert hits, (
        "anti-fork lint failed to detect a planted Dialect.POSTGRES "
        "branch — the AST walker is broken and the real check is dead."
    )
