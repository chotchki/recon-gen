"""ScenarioContext — composition safety + cleanup attribution (AV.5).

Promoted from the `scenario-context-spike` branch. The spike's central
finding ("daily_balances has no metadata column → use a side table")
was resolved by Phase AV.1 (the column was renamed from ``limits`` to
``metadata`` and demoted the per-rail caps to a nested key). Both
base tables now carry the symmetric ``metadata`` column, so the
production version tags PER ROW on both tables — no sidecar table.

What this module solves (the three user-flagged concerns from the
2026-05-23 spike planning):

1. **Same-class collision.** Two ``DriftGenerator`` instances on the
   same role plant into the same account_id ⇒ PK collision at INSERT
   time, silent data masking otherwise. ``ScenarioContext.compose()``
   pre-checks pairwise disjoint ``claimed_accounts`` across the
   composed generators and raises a clear error naming both classes.

2. **Cross-scenario interference.** Apply scenario A; apply scenario
   B that claims one of A's accounts ⇒ B would overwrite or compound
   A's plant without warning. ``compose()`` runs a portable
   ``JSON_VALUE(metadata, '$.scenario_id')`` SELECT against both base
   tables and refuses if any of B's claims overlap rows already
   tagged with a different scenario_id.

3. **Cleanup attribution.** Multiple scenarios in one DB need surgical
   tear-down. ``ScenarioContext.cleanup()`` deletes every row whose
   ``metadata.scenario_id`` matches — no sidecar bookkeeping, the
   tag itself IS the bookkeeping.

API shape (intentionally minimal):

- ``ScenarioContext(scenario_id="...")`` — frozen dataclass owning
  the scenario_id string.
- ``ctx.compose(conn, *generators, dialect=Dialect.SQLITE)`` — runs
  the pre-checks, calls each generator's ``emit(conn,
  scenario_id=ctx.scenario_id)``, commits.
- ``ctx.cleanup(conn, dialect=Dialect.SQLITE)`` — DELETE FROM both
  base tables WHERE ``metadata.scenario_id`` matches; returns total
  rowcount.
- ``ClaimedAccountsGenerator`` Protocol — extension of
  ``ViolationGenerator`` adding ``claimed_accounts: frozenset[str]``
  and the ``scenario_id`` kwarg on ``emit``.

Backward compat — every existing call site stays untouched:

- ``generator.emit(conn)`` still works (scenario_id defaults to None).
- When ``scenario_id`` is None, the generator skips the metadata
  tagging entirely → byte-identical to pre-AV.5 emission. AS.5's
  semantic_lock and AT.6's training scenarios both keep working
  without changes.
- Only when a caller threads ``ScenarioContext`` does tagging happen.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from recon_gen.common.spine.violation import Violation
from recon_gen.common.sql import Dialect
from recon_gen.common.sql.dialect import json_value


# ---------------------------------------------------------------------------
# AY.4.a — dry-run capture mode.
#
# The spine emitters write through `insert_tx` / `insert_balance` (in
# `_emit_helpers`), which dispatch placeholder style via
# `type(conn).__module__` ("sqlite3" → ?, "psycopg" → %s, "oracledb" →
# :N). For the production seed reroute (AY.4) `build_full_seed_sql`
# needs SQL TEXT, not a side-effect on a live connection. The capture
# pattern: a fake dbapi conn that records `(sql, params)` pairs
# instead of executing them, then a downstream renderer (AY.4.b)
# converts to literal SQL.
#
# Three concrete subclasses — one per dialect — so each carries the
# `__module__` value `_placeholder_style` already routes off. Captured
# SQL ends up in the target dialect's placeholder convention, which
# makes the renderer's job purely a literal-substitution loop. Factory
# `dry_run_capture(dialect)` picks the right subclass.
# ---------------------------------------------------------------------------


class _DryRunCursor:
    """Mock dbapi cursor: records `execute(sql, params)` calls onto
    its parent capture conn's `captured` list. `close()` is a no-op.
    """

    def __init__(self, parent: "_DryRunBase") -> None:
        self._parent = parent

    def execute(
        self, sql: str, params: object = (),
    ) -> None:
        # Params may be a list, tuple, or empty — normalize to tuple
        # so downstream renderers don't care.
        normalized: tuple[object, ...] = (
            tuple(params) if params else ()  # type: ignore[arg-type]: dbapi takes any iterable
        )
        self._parent.captured.append((sql, normalized))

    def close(self) -> None:
        pass


class _DryRunBase:
    """Shared instance interface for the per-dialect dry-run capture
    subclasses. `captured` accumulates every `cursor.execute` call;
    `cursor()` returns a fresh `_DryRunCursor`; `commit`/`close`
    are no-ops. The per-dialect subclasses only differ in
    `__module__` (which `_emit_helpers._placeholder_style` reads).
    """

    captured: list[tuple[str, tuple[object, ...]]]

    def __init__(self) -> None:
        self.captured = []

    def cursor(self) -> _DryRunCursor:
        return _DryRunCursor(self)

    def commit(self) -> None:  # noqa: D401 — dbapi-shaped no-op
        pass

    def close(self) -> None:  # noqa: D401 — dbapi-shaped no-op
        pass


class _DryRunCaptureSqlite(_DryRunBase):
    """SQLite-dialect dry-run capture; `_placeholder_style` picks `?`."""


_DryRunCaptureSqlite.__module__ = "sqlite3"


class _DryRunCapturePostgres(_DryRunBase):
    """Postgres-dialect dry-run capture; `_placeholder_style` picks `%s`."""


_DryRunCapturePostgres.__module__ = "psycopg"


class _DryRunCaptureOracle(_DryRunBase):
    """Oracle-dialect dry-run capture; `_placeholder_style` picks `:N`."""


_DryRunCaptureOracle.__module__ = "oracledb"


_DRY_RUN_CLASSES: dict[Dialect, type[_DryRunBase]] = {
    Dialect.SQLITE: _DryRunCaptureSqlite,
    Dialect.POSTGRES: _DryRunCapturePostgres,
    Dialect.ORACLE: _DryRunCaptureOracle,
}


def dry_run_capture(dialect: Dialect = Dialect.SQLITE) -> _DryRunBase:
    """Return a fresh dry-run capture conn for the given dialect.

    The returned object satisfies the dbapi 2.0 shape `insert_tx` /
    `insert_balance` rely on: `cursor()` → cursor with `execute(sql,
    params)`, `commit()` / `close()` no-op. `captured` accumulates
    every emitted (sql, params) pair in arrival order.

    Pair with `ScenarioContext.compose(conn=dry_run_capture(d), ...,
    dry_run=True)` (AY.4.a) + the AY.4.b renderer.
    """
    return _DRY_RUN_CLASSES[dialect]()


@runtime_checkable
class ClaimedAccountsGenerator(Protocol):
    """Extension of ViolationGenerator: declares the account_ids the
    plant will touch + accepts a ``scenario_id`` kwarg on emit.

    The Protocol stays minimal — concrete generators add this property
    derived from their construction kwargs. ``emit``'s ``scenario_id``
    is keyword-only and defaults to ``None`` (preserves untagged
    behavior for existing call sites).
    """

    @property
    def claimed_accounts(self) -> frozenset[str]: ...

    @property
    def intended(self) -> Violation | None: ...

    def emit(
        self,
        conn: sqlite3.Connection,
        *,
        scenario_id: str | None = None,
    ) -> None: ...


def scenario_metadata(scenario_id: str, **extra: object) -> str:
    """Build the JSON metadata blob a tagged plant row carries.

    Centralized so the cleanup query can extract via a portable
    ``JSON_VALUE(metadata, '$.scenario_id')`` and every tagging caller
    writes the same shape. Extra kwargs round-trip — useful for
    per-generator attribution (e.g. ``generator='DriftGenerator'``).
    """
    return json.dumps(
        {"scenario_id": scenario_id, **extra},
        sort_keys=True,
        separators=(",", ":"),  # typing-smell: ignore[json-indent]: compact deterministic — this is a per-row DB metadata payload, not a human-diffable file
    )


def _check_pairwise_disjoint(
    scenario_id: str,
    generators: tuple[ClaimedAccountsGenerator, ...],
) -> None:
    """Raise ``ValueError`` if any two generators claim the same
    account_id. Names BOTH offending classes so the operator's fix is
    immediate (rename one, split the scenario, etc.)."""
    seen: dict[str, type] = {}
    for gen in generators:
        for account in gen.claimed_accounts:
            if account in seen:
                raise ValueError(
                    f"account_id collision in scenario_id="
                    f"{scenario_id!r}: both {seen[account].__name__} "
                    f"and {type(gen).__name__} claim {account!r}. "
                    f"Use distinct account selectors or split into "
                    f"separate scenarios."
                )
            seen[account] = type(gen)


def _check_cross_scenario(
    conn: Any,  # typing-smell: ignore[explicit-any]: per-driver dbapi connection (sqlite3/psycopg/oracledb); no shared Protocol
    scenario_id: str,
    accounts: Iterable[str],
    prefix: str,
    dialect: Dialect,
) -> None:
    """Raise ``ValueError`` if any account in ``accounts`` is already
    tagged with a DIFFERENT scenario_id on either base table.

    Uses the portable ``json_value`` helper so the same code runs on
    SQLite (``json_extract``) / PG / Oracle (``JSON_VALUE``). Same-
    scenario_id rows are intentionally allowed — own-scenario
    continuations are a feature (see the spike's
    ``test_same_scenario_can_recompose_on_its_own_accounts``).
    """
    accounts_tuple = tuple(accounts)
    if not accounts_tuple:
        return
    placeholders = ", ".join("?" if dialect is Dialect.SQLITE else "%s"
                              if dialect is Dialect.POSTGRES else f":{i + 1}"
                              for i in range(len(accounts_tuple)))
    sid_placeholder = (
        "?" if dialect is Dialect.SQLITE
        else "%s" if dialect is Dialect.POSTGRES
        else f":{len(accounts_tuple) + 1}"
    )
    sid_extract = json_value("metadata", "'$.scenario_id'", dialect)
    cur = conn.cursor()
    try:
        for table in (
            f"{prefix}_transactions", f"{prefix}_daily_balances",
        ):
            sql = (
                f"SELECT account_id, {sid_extract} AS sid "
                f"FROM {table} "
                f"WHERE account_id IN ({placeholders}) "
                f"  AND {sid_extract} IS NOT NULL "
                f"  AND {sid_extract} <> {sid_placeholder} "
                f"LIMIT 1"
            )
            cur.execute(sql, list(accounts_tuple) + [scenario_id])
            row = cur.fetchone()
            if row is not None:
                conflicting_account, conflicting_sid = row
                raise ValueError(
                    f"cross-scenario interference: account "
                    f"{conflicting_account!r} is already claimed by "
                    f"scenario_id={conflicting_sid!r} (in table "
                    f"{table}). Cannot compose scenario_id="
                    f"{scenario_id!r} on top — cleanup "
                    f"{conflicting_sid!r} first or pick a different "
                    f"account."
                )
    finally:
        cur.close()


@dataclass(frozen=True)
class ScenarioContext:
    """Owns a ``scenario_id``; orchestrates a multi-generator plant with
    compose-time collision detection + per-row metadata tagging on
    both base tables for surgical cleanup.

    AV.5 — promoted from the spike. The spike used a side-table
    (``<prefix>_scenario_claims``) because daily_balances had no
    metadata column; AV.1 fixed that, so the production version tags
    PER ROW on both tables and drops the sidecar entirely.

    ``prefix`` is the deployment's table prefix (matches
    ``cfg.db_table_prefix``). ``dialect`` selects the SQL JSON path
    helper. Defaults match the in-process SQLite test harness — every
    AS/AT/AU unit test that builds a generator + composes via
    ScenarioContext can pass nothing.
    """

    scenario_id: str
    prefix: str = "spec_example"
    dialect: Dialect = Dialect.SQLITE

    def compose(
        self,
        conn: sqlite3.Connection,
        *generators: ClaimedAccountsGenerator,
        dry_run: bool = False,
    ) -> list[tuple[str, tuple[object, ...]]] | None:
        """Pre-compose checks + emit all generators with the
        scenario_id threaded through + commit.

        Two checks run before any emit fires:

        1. **Pairwise disjoint claims** across the composed generators
           (catches "two DriftGenerators on the same role" at the
           wiring site, not at the DB-level PK violation).
        2. **Cross-scenario non-overlap** against the data tables'
           ``metadata.scenario_id`` (catches "scenario B overwrites
           scenario A's rows" before any INSERT runs).

        On success: each generator's ``emit(conn,
        scenario_id=self.scenario_id)`` runs in order; the generator
        is responsible for tagging every row it writes.

        AY.4.a — ``dry_run=True`` mode: ``conn`` must be a
        ``dry_run_capture(dialect)`` instance. The cross-scenario
        check is SKIPPED in dry-run (no real DB state exists to
        check against; the pairwise-disjoint check still fires since
        it's a pure-data check on the generators themselves).
        Returns the captured ``[(sql, params), ...]`` list — pair
        with the AY.4.b renderer to produce static SQL text for the
        ``build_full_seed_sql`` path.

        Live mode (default): returns ``None``, commits the conn.
        """
        _check_pairwise_disjoint(self.scenario_id, generators)

        all_accounts: set[str] = set()
        for gen in generators:
            all_accounts.update(gen.claimed_accounts)

        if not dry_run:
            _check_cross_scenario(
                conn, self.scenario_id, all_accounts,
                prefix=self.prefix, dialect=self.dialect,
            )

        for gen in generators:
            gen.emit(conn, scenario_id=self.scenario_id)

        if dry_run:
            # The capture conn carries the (sql, params) list; return
            # it for the renderer. Defensive isinstance: a stray
            # real-conn call with dry_run=True would silently no-op
            # otherwise.
            if not isinstance(conn, _DryRunBase):
                raise TypeError(
                    f"dry_run=True requires a dry_run_capture() conn; "
                    f"got {type(conn).__name__}. Use "
                    f"`dry_run_capture(dialect)` from "
                    f"`recon_gen.common.spine`."
                )
            return list(conn.captured)
        conn.commit()
        return None

    def cleanup(self, conn: sqlite3.Connection) -> int:
        """Delete every row on either base table whose
        ``metadata.scenario_id`` matches this scenario. Returns the
        total rowcount across both tables.

        No sidecar table to consult — the tag IS the bookkeeping
        (AV.1 unlocked this). Other scenarios on overlapping accounts
        survive because their rows carry a different scenario_id.
        """
        sid_extract = json_value(
            "metadata", "'$.scenario_id'", self.dialect,
        )
        placeholder = (
            "?" if self.dialect is Dialect.SQLITE
            else "%s" if self.dialect is Dialect.POSTGRES
            else ":1"
        )
        total = 0
        cur = conn.cursor()
        try:
            for table in (
                f"{self.prefix}_transactions",
                f"{self.prefix}_daily_balances",
            ):
                cur.execute(
                    f"DELETE FROM {table} WHERE {sid_extract} = {placeholder}",
                    (self.scenario_id,),
                )
                total += cur.rowcount
        finally:
            cur.close()
        conn.commit()
        return total
