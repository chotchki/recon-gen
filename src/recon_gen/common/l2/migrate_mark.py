"""CZ.6 — pre-CZ DB migration: stamp ``metadata.source`` on legacy rows.

Phase CZ's standalone-mode cleanup gate keys on
``JSON_VALUE(metadata, '$.source') = 'training'`` as the synthetic-row
predicate. CZ.2 wires the stamp through every seed-pipeline write path
so post-CZ rows are marked. This module handles the inverse problem:
pre-CZ rows already sitting in the DB at the moment the operator
upgrades to a CZ-aware build.

Two surfaces:

- ``count_unstamped_rows(conn, prefix, dialect)`` — per-base-table count
  of rows where ``$.source`` is absent (either ``metadata IS NULL`` or
  ``metadata`` is a JSON object missing the ``source`` key). Used by the
  ``data apply --execute`` pre-flight check (CZ.6 auto-mark path) and
  by the explicit ``schema migrate-mark`` verb.
- ``stamp_unstamped_rows(conn, prefix, dialect, source='training')`` —
  UPDATE every unstamped row in both base tables, merging the existing
  metadata dict with ``{"source": <value>}``. Idempotent: re-running
  against an already-stamped DB is a no-op (count returns 0).

Implementation notes:

- The merge happens in Python (fetch unstamped rows, mutate dict in
  Python, UPDATE back). This avoids dialect-specific JSON-mutation SQL
  (``jsonb_set`` is banned by the portability constraint; Oracle 19c's
  ``JSON_MERGEPATCH`` is the only standard form and isn't available on
  every supported version cleanly). The row count is small by
  construction (one-time per-upgrade migration), so the Python round-
  trip is acceptable vs. the portability tax.
- NULL metadata becomes ``'{"source":"training"}'``. Empty object
  ``{}`` becomes ``'{"source":"training"}'``. Existing-key objects
  ``{"transfer_key":"abc"}`` become
  ``'{"source":"training","transfer_key":"abc"}'``.
- The default source value is ``"training"`` because that matches the
  REPLAN-locked assumption: pre-CZ DBs were demonstrably running
  through the training/seed path (production-integrator etl_hook is
  new in BS.4 and only landed alongside CZ). Operator can override via
  ``--source=real`` on the explicit verb when they know better.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

from recon_gen.common.sql import Dialect
from recon_gen.common.sql.dialect import json_value

if TYPE_CHECKING:
    from recon_gen.common.db import SyncConnection


__all__ = [
    "count_unstamped_rows",
    "stamp_unstamped_rows",
]


_BASE_TABLE_SUFFIXES: tuple[str, ...] = ("transactions", "daily_balances")
# Both base tables carry a per-row autoincrement ``entry`` column that
# is unique within the table (the supersession entry per Phase L). Using
# it as the row-id avoids reaching for the dialect-varying composite
# PK shapes (PG/Oracle ``(id, entry)`` vs. ``(account_id, business_day_
# start, entry)``) — ``entry`` is unambiguous on both tables.
_ROW_ID_COLUMN = "entry"


def _placeholder(dialect: Dialect, index: int) -> str:
    """Per-dialect positional bind placeholder. Index is 1-based for
    Oracle's ``:1`` / ``:2`` shape; ignored for PG / DuckDB which use
    the same shape regardless of position.
    """
    if dialect is Dialect.POSTGRES:
        return "%s"
    if dialect is Dialect.DUCKDB:
        return "?"
    return f":{index}"


def _unstamped_predicate(dialect: Dialect) -> str:
    """SQL predicate matching every row where ``$.source`` is absent.

    Two cases combine via OR: ``metadata IS NULL`` (the pre-CZ.2
    ``_balance_row_tuple`` hard-coded NULL path) and ``metadata IS NOT
    NULL`` but ``$.source`` is missing (the pre-CZ.2 baseline metadata
    path that emitted rail-key pairs without the source stamp).
    """
    source_extract = json_value("metadata", "'$.source'", dialect)
    return f"(metadata IS NULL OR {source_extract} IS NULL)"


def count_unstamped_rows(
    conn: "SyncConnection",
    *,
    prefix: str,
    dialect: Dialect,
) -> tuple[int, int]:
    """Return ``(transactions, daily_balances)`` unstamped row counts.

    Idempotent + side-effect free: pure SELECT COUNT(*). Used by the
    ``data apply --execute`` pre-flight check to decide between
    auto-mark, refuse, or no-op.
    """
    predicate = _unstamped_predicate(dialect)
    cur = conn.cursor()
    counts: list[int] = []
    try:
        for suffix in _BASE_TABLE_SUFFIXES:
            table = f"{prefix}_{suffix}"
            cur.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {predicate}"
            )
            row = cur.fetchone()
            counts.append(int(row[0]) if row else 0)
    finally:
        cur.close()
    return counts[0], counts[1]


def stamp_unstamped_rows(
    conn: "SyncConnection",
    *,
    prefix: str,
    dialect: Dialect,
    source: str = "training",
) -> tuple[int, int]:
    """UPDATE every unstamped row in both base tables to carry
    ``metadata.source = <source>``, preserving existing metadata keys.

    Returns ``(transactions_updated, daily_balances_updated)`` — same
    shape as ``count_unstamped_rows`` so callers can log the delta.
    Idempotent: a second invocation against the resulting DB returns
    ``(0, 0)`` because every row now carries the stamp.

    The merge happens in Python (fetch → mutate → UPDATE) to avoid
    dialect-specific JSON-mutation SQL (see module docstring). For the
    expected migration volume (one-time per-upgrade), this is fine.
    Caller is responsible for ``conn.commit()`` — keeping that off the
    library lets the CLI surface either commit on success or roll back
    on an exception.
    """
    predicate = _unstamped_predicate(dialect)
    updated_totals: list[int] = []
    cur = conn.cursor()
    try:
        for suffix in _BASE_TABLE_SUFFIXES:
            table = f"{prefix}_{suffix}"
            cur.execute(
                f"SELECT {_ROW_ID_COLUMN}, metadata FROM {table} "
                f"WHERE {predicate}"
            )
            rows = cur.fetchall()
            updated = 0
            for pk_value, raw_metadata in rows:
                payload: dict[str, object]
                if raw_metadata is None:
                    payload = {"source": source}
                else:
                    parsed: object
                    try:
                        parsed = json.loads(raw_metadata)
                    except (TypeError, ValueError):
                        # Defensive: a corrupt metadata blob shouldn't
                        # block the migration. Replace with the bare
                        # source stamp; the original was already broken.
                        parsed = {}
                    if not isinstance(parsed, dict):
                        payload = {"source": source}
                    else:
                        # Re-key into a typed dict[str, object] so the
                        # merge + dumps are statically typed. json.loads
                        # returns Any so the cast is the typing seam.
                        # WHY: json.loads object values are heterogenous
                        # (str/int/bool/dict/list/None); collapse to
                        # `object` at the boundary.
                        parsed_typed = cast(dict[Any, Any], parsed)
                        existing: dict[str, object] = {
                            str(k): v for k, v in parsed_typed.items()
                        }
                        if "source" in existing:
                            # Race-safe no-op: another writer stamped
                            # this row between the count and the
                            # UPDATE. Skip.
                            continue
                        existing["source"] = source
                        payload = existing
                merged_blob = json.dumps(
                    payload,
                    sort_keys=True,
                    separators=(",", ":"),  # typing-smell: ignore[json-indent]: per-row DB payload, not human-diffed
                )
                ph_metadata = _placeholder(dialect, 1)
                ph_pk = _placeholder(dialect, 2)
                cur.execute(
                    f"UPDATE {table} SET metadata = {ph_metadata} "
                    f"WHERE {_ROW_ID_COLUMN} = {ph_pk}",
                    (merged_blob, pk_value),
                )
                updated += 1
            updated_totals.append(updated)
    finally:
        cur.close()
    return updated_totals[0], updated_totals[1]
