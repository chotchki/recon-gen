"""BX.6/11 — role-cardinality helpers (editor-only).

Lightweight utilities the role-reframe surfaces use:

- ``RoleCompleteness`` + ``compute_role_completeness`` — typed rollup
  for the home page's outer **Roles** wrapper checkmark. The
  per-kind state (``account`` / ``account_template``) is combined
  under the lattice ``empty < partial < set`` (so the Roles entry
  reads "set" iff BOTH children are set, "partial" if either is
  partial-but-not-fully-empty, "empty" if both are empty).

- ``instance_count_by_role`` — live COUNT(DISTINCT account_id) per
  AccountTemplate role against the institution's
  ``<prefix>_daily_balances`` table for the latest balance_date.
  Returns ``None`` when no DB is reachable / the query errors;
  caller renders "awaiting first ETL" (the BX.11 D5 decision the
  operator confirmed on the BX.6/11 OQ3 lock).

The helpers are typed primitives per
``[[feedback_invariants_in_types]]`` — callers don't sprint
strings together; the lattice is a typed Literal.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from recon_gen.common.config import Config
from recon_gen.common.l2.editor import EntityKind
from recon_gen.common.l2.primitives import L2Instance


# Lattice values. The ordering matters: the rollup picks the *max*
# state under the order ``empty < partial < set`` (so "set + empty"
# → "partial", not "set"). See ``_rollup`` below.
CompletenessState = Literal["empty", "partial", "set"]


@dataclass(frozen=True)
class RoleCompleteness:
    """Per-kind completeness state for the role-wrapper rollup.

    ``account`` covers 1:1 (Account) entities; ``account_template``
    covers 1:N (AccountTemplate) entities. ``roles`` is the rollup
    state used as the outer Roles section header's checkmark.
    """

    account: CompletenessState
    account_template: CompletenessState
    roles: CompletenessState


_LATTICE_RANK: Mapping[CompletenessState, int] = {
    "empty": 0,
    "partial": 1,
    "set": 2,
}
_LATTICE_BY_RANK: tuple[CompletenessState, ...] = ("empty", "partial", "set")


def _rollup(a: CompletenessState, b: CompletenessState) -> CompletenessState:
    """Combine two per-kind states under the ``empty < partial < set``
    lattice. The outer roles header takes the MIN under that ordering
    so the rollup reads "set" only when both children are set,
    "partial" when at least one child is partial-or-better but they
    don't both clear "set", "empty" when both are empty.

    Truth table (a, b → rollup):
      empty,   empty   → empty
      empty,   partial → partial
      empty,   set     → partial
      partial, partial → partial
      partial, set     → partial
      set,     set     → set
    """
    if a == b:
        return a
    # Anything-mixed-with-set demotes to partial. Anything-mixed-with-
    # empty (when the other isn't empty too) is also partial. So
    # "not equal" + neither rank-2 → partial.
    return "partial"


def _per_kind_state(
    instance: L2Instance, kind: EntityKind,
) -> CompletenessState:
    """Per-kind completeness for the role-relevant kinds.

    Both ``account`` and ``account_template`` are tuples on the
    L2Instance; ``empty`` when zero entries, ``set`` when at least
    one. (The reframe doesn't carry a "partially complete" notion
    per-kind at the home-section level — that signal lives on
    per-field validators inside the edit form, not here.)
    """
    if kind == "account":
        return "set" if instance.accounts else "empty"
    if kind == "account_template":
        return "set" if instance.account_templates else "empty"
    raise ValueError(
        f"compute_role_completeness only supports account / "
        f"account_template; got {kind!r}"
    )


def compute_role_completeness(instance: L2Instance) -> RoleCompleteness:
    """Compute the per-kind + rollup state for the Roles home section.

    Drives the outer Roles section header's ``✓ / ⚠ / ✗`` (set /
    partial / empty) glyph + the sub-bucket headers' individual
    glyphs. Returns the typed dataclass — callers don't unpack tuples.
    """
    account_state = _per_kind_state(instance, "account")
    template_state = _per_kind_state(instance, "account_template")
    rollup = _rollup(account_state, template_state)
    return RoleCompleteness(
        account=account_state,
        account_template=template_state,
        roles=rollup,
    )


def completeness_glyph(state: CompletenessState) -> str:
    """Visible glyph per state. Matches the broader Studio convention:
    ✓ for set, ⚠ for partial, ✗ for empty (cold-read v3 + CG-era
    chrome). Plain ASCII; theme-fg colored at the callsite via
    Tailwind utilities, not inline."""
    if state == "set":
        return "✓"
    if state == "partial":
        return "⚠"
    return "✗"


# ---------------------------------------------------------------------------
# Live instance-count helper for AccountTemplate (1:N) cards.
# ---------------------------------------------------------------------------


def instance_count_by_role(
    template_role: str,
    cfg: Config,
    *,
    connection_factory: Callable[[], Any] | None = None,
) -> int | None:
    """Live ``COUNT(DISTINCT account_id)`` against the institution's
    ``<prefix>_daily_balances`` table for the latest balance_date.

    Used by the AccountTemplate read-card cardinality badge to
    surface the runtime fan-out (BX.11 D5 carry-forward per the
    BX.6/11 OQ3 operator lock). Returns ``None`` on any of:

    - no DB reachable (the demo-DB URL isn't wired);
    - the table doesn't exist yet (pre-first-ETL);
    - the query errors for any other reason.

    Caller renders the math notation alone (``1:N``) when None +
    falls through to the secondary-fg "Templated role" sub-line.
    A returned count of ``0`` means "template declared but no rows
    materialized yet" — caller renders "awaiting first ETL".

    Args:
        template_role: The AccountTemplate.role string. Used as the
            ``account_role`` filter value; bound, not concatenated,
            so a malicious YAML can't inject SQL.
        cfg: loaded Config; supplies ``db_table_prefix`` + the
            connection params via ``connect_demo_db``.
        connection_factory: callable returning a fresh DB-API 2.0
            connection. Defaults to ``connect_demo_db(cfg)``. Tests
            inject a fake; production opens the real DB.

    Returns:
        The integer count when the query succeeds; ``None`` on any
        failure (broad-catch is intentional — this is a *cosmetic*
        badge, never a correctness gate).
    """
    prefix = cfg.db.table_prefix
    # Compose the table name (NOT a bound param — prefix comes from
    # cfg, not user input — same shape as `_query_money_trail_edges`
    # in `_db_fetcher.py`). The role filter IS bound (DB-API 2.0
    # qmark / pyformat paramstyle, depending on dialect).
    sql = (
        f"SELECT COUNT(DISTINCT account_id) "
        f"FROM {prefix}_daily_balances "
        f"WHERE account_role = ? "
        f"AND balance_date = ("
        f"  SELECT MAX(balance_date) FROM {prefix}_daily_balances"
        f")"
    )

    if connection_factory is None:
        try:
            # Lazy import — connect_demo_db pulls psycopg2 / oracledb
            # / duckdb, which are optional extras. Probe surfaces
            # might run without them installed.
            from recon_gen.common.db import connect_demo_db  # noqa: PLC0415
        except ImportError:
            return None

        def _default_factory() -> Any:  # typing-smell: ignore[explicit-any]: DB-API 2.0 connection is duck-typed across psycopg2 / oracledb / duckdb
            return connect_demo_db(cfg)

        connection_factory = _default_factory

    try:
        conn = connection_factory()
    except Exception:  # noqa: BLE001 — cosmetic badge, swallow any DB error
        return None

    try:
        # Dialect paramstyle varies (qmark on duckdb / sqlite,
        # pyformat on psycopg2, named on oracledb). connect_demo_db
        # returns the dialect's native cursor; the simplest portable
        # form is to attempt the qmark style first then fall back to
        # pyformat. psycopg2 / oracledb accept lists of params under
        # `%s` / numbered styles, so we hand-translate when qmark
        # rejects.
        cur = conn.cursor()
        try:
            try:
                cur.execute(sql, [template_role])
            except Exception:  # noqa: BLE001 — try the alternate paramstyle below
                # Fallback: %s paramstyle (psycopg2 / oracledb in
                # numbered mode). Rebuild the SQL string with %s in
                # place of ?.
                pyformat_sql = sql.replace("?", "%s")
                cur.execute(pyformat_sql, [template_role])
            row = cur.fetchone()
        finally:
            cur.close()
    except Exception:  # noqa: BLE001 — cosmetic badge, swallow any DB error
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        return None

    try:
        conn.close()
    except Exception:  # noqa: BLE001
        pass

    if row is None or row[0] is None:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return None


__all__ = [
    "CompletenessState",
    "RoleCompleteness",
    "_LATTICE_BY_RANK",
    "_LATTICE_RANK",
    "completeness_glyph",
    "compute_role_completeness",
    "instance_count_by_role",
]
