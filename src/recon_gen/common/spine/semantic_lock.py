"""Semantic lock — the spine's replacement for byte-locked seed SQL.

The audit's payoff (`docs/audits/date_range_model_audit.md` §5 "AP.3
GREEN → now load-bearing"): once the spine round-trip holds
(`Invariant.detect(ViolationGenerator.emit()) ⊇ intended`), byte-
locked seed SQL can retire. The lock currency moves from SQL bytes to
the Violation set the detectors produce — semantic correctness
becomes a direct check, stronger than byte-identity.

Two functions, intentionally minimal:

- `apply_scenario(conn, *emitters)` — emit + commit + refresh the
  matviews. The composition layer above any individual
  `ViolationGenerator` or `LedgerSimulation`; takes anything with
  `.emit(conn)`.
- `semantic_lock(conn, invariants)` — run detect across every named
  invariant; return a frozen dict `{invariant_name: frozenset[
  Violation]}`. The new "lock" — equality on this dict is the
  byte-stable semantic gate.

What this does NOT do yet (defers to a post-AU+AT cutover):

- REPLACE the existing `tests/data/_locked_seeds/<instance>.<dialect>.sql`
  files. AS.5 lands the mechanism alongside the existing byte-locked
  tests; the actual removal waits until the spine's invariant set
  covers everything the byte-locked seeds encode (AU finishes L1,
  AT covers L2).

Why semantic equivalence > byte equivalence: generator implementation
churn (different account_id strings, different SQL ordering, etc.)
that preserves the violation set passes the semantic lock; byte-locked
breaks. The lock guards INTENT, not implementation. AR.5's
substitution-path lesson generalizes here — what matters is the
violations the data produces, not the SQL that built it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from recon_gen.common.db import execute_script
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import refresh_matviews_sql
from recon_gen.common.spine.invariant import Invariant
from recon_gen.common.spine.violation import Violation
from recon_gen.common.sql import Dialect


class _Emitter(Protocol):
    """Anything with `.emit(conn)` — covers `ViolationGenerator` (via
    its Protocol) and `LedgerSimulation` (the AS.4 composition layer)
    plus any future `AccountSimulation`-using emitter."""

    def emit(self, conn: sqlite3.Connection) -> None: ...


def apply_scenario(
    conn: sqlite3.Connection,
    *emitters: _Emitter,
    prefix: str = "spec_example",
    instance_path: Path | None = None,
    dialect: Dialect = Dialect.SQLITE,
) -> None:
    """Emit every passed object's rows into `conn`, commit, then
    refresh the matviews for the `prefix`'s L2 instance.

    `instance_path=None` uses the bundled `tests/l2/spec_example.yaml`
    — same default the existing in-process spike harness pattern uses.
    Pass an explicit path for AT's Investigation-surface scenarios.

    `dialect` defaults to `Dialect.SQLITE` for the in-process test
    harness shape (the dominant call site). AY.3 lifted the hardcode
    so production callers + per-dialect semantic_lock generation
    (AZ.1 will produce `_semantic_locks/<instance>.<dialect>.json`
    snapshots that need PG + Oracle paths through this function) can
    thread the deployed dialect through. The spine emitters already
    detect the dbapi placeholder style per-connection (AT.5.b's
    `_emit_helpers._placeholder_style`); this kwarg covers the
    matview refresh + script execution leg of the same path.
    """
    for emitter in emitters:
        emitter.emit(conn)
    conn.commit()

    repo_root = Path(__file__).resolve().parents[4]
    yaml_path = instance_path or (
        repo_root / "tests" / "l2" / "spec_example.yaml"
    )
    instance = load_instance(yaml_path)
    cur = conn.cursor()
    execute_script(
        cur,
        refresh_matviews_sql(instance, prefix=prefix, dialect=dialect),
        dialect=dialect,
    )
    conn.commit()


def semantic_lock(
    conn: sqlite3.Connection,
    invariants: Iterable[Invariant],
) -> dict[str, frozenset[Violation]]:
    """Run `detect(conn)` for every invariant; return the lock dict.

    Returned dict is `{invariant.name: frozenset(detected_violations)}`.
    `frozenset` makes the value byte-stable: two runs that produce the
    same set in different insertion orders compare equal.

    The DICT itself is not frozen, but its keys + values are immutable;
    equality (`lock_a == lock_b`) is the gate. The caller decides
    whether to lock against a literal Python value, a JSON-serialized
    snapshot, or a `tests/data/_semantic_locks/<scenario>.json` file
    (the eventual successor to `_locked_seeds`).
    """
    return {
        inv.name: frozenset(inv.detect(conn))
        for inv in invariants
    }
