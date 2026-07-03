"""DS.6 — the per-dialect replay: the SAME packed enumeration domains,
re-emitted at PG / Oracle and loaded into a live db-tier connection, so
engine == residual is proven on every engine — not just the DuckDB
unit-tier path.

The insight that makes this cheap: every ``DetectorCheck.read_engine``
reads through only ``.prefix`` + ``.fetchall(sql)``. ``DialectReplayDB``
supplies exactly that surface over a psycopg / oracledb / duckdb
connection, so the domains' existing read functions — and their
residual-derived ``expected_for`` maps — replay verbatim on the target
dialect. The only dialect-specific machinery is the schema / config /
refresh emit (already branched in the emitters) and a RAW bulk load
that bypasses money coercion (the cell rows carry cents already, exactly
like the DuckDB harness's Arrow path — routing them through the
dollar→cents coercion the plant helpers use would multiply every amount
by 100).

Claim ledger (DS.0): PROVEN-on-D on DuckDB (the full unit-tier domain),
PROVEN-on-D_boundary on PG + Oracle (this lane). The subscript names the
domain SUBSET, never the venue — POLICY 1 holds: this test runs
identically local and on CI, dialect chosen by the cfg.
"""
from __future__ import annotations

from pathlib import Path

from recon_gen.common.db import SyncConnection, execute_script
from recon_gen.common.spine._emit_helpers import _bulk_insert
from recon_gen.common.sql import Dialect
from tests.enumeration.harness import (
    DB_COLS,
    TX_COLS,
    BalRow,
    InstanceArtifacts,
    PackedDomain,
    TxRow,
    artifacts_for,
)

#: Empty money-cols set → the raw load path: cell rows are already cents
#: (the residual world's native unit), so no dollar→cents coercion.
_NO_COERCE: frozenset[str] = frozenset()


class DialectReplayDB:
    """A live-connection stand-in for ``EnumerationDB`` exposing the
    ``read_engine`` surface (``prefix`` + ``fetchall``). Applies the
    emitted schema + config on construction; ``insert`` / ``refresh``
    mirror the harness. The caller owns the connection lifecycle (it is
    the db-tier ``connect_demo_db(isolated_cfg, read_only=False)``);
    ``drop`` cleans the emitted objects so a re-run is idempotent."""

    def __init__(
        self,
        conn: SyncConnection,
        artifacts: InstanceArtifacts,
        dialect: Dialect,
        *,
        drop_sql: str | None = None,
    ) -> None:
        self._conn = conn
        self.prefix = artifacts.prefix
        self._dialect = dialect
        self._refresh_sql = artifacts.refresh_sql
        self._drop_sql = drop_sql
        if drop_sql is not None:
            # Idempotent re-run: clear any prior emit of this prefix.
            self._script(drop_sql)
        self._script(artifacts.schema_sql)
        self._script(artifacts.config_sql)
        conn.commit()

    def _script(self, sql: str) -> None:
        """``execute_script`` wants a DB-API CURSOR (psycopg / oracledb
        connections have no ``.execute``); open one per script."""
        cur = self._conn.cursor()
        try:
            execute_script(cur, sql, dialect=self._dialect)
        finally:
            cur.close()

    def insert(self, tx_rows: list[TxRow], bal_rows: list[BalRow]) -> None:
        if tx_rows:
            _bulk_insert(
                self._conn, f"{self.prefix}_transactions", TX_COLS,
                [r.as_tuple() for r in tx_rows], _NO_COERCE,
            )
        if bal_rows:
            _bulk_insert(
                self._conn, f"{self.prefix}_daily_balances", DB_COLS,
                [r.as_tuple() for r in bal_rows], _NO_COERCE,
            )
        self._conn.commit()

    def refresh(self) -> None:
        self._script(self._refresh_sql)
        self._conn.commit()

    def fetchall(self, sql: str) -> list[tuple[object, ...]]:
        cur = self._conn.cursor()
        try:
            cur.execute(sql)
            return [tuple(row) for row in cur.fetchall()]
        finally:
            cur.close()

    def drop(self) -> None:
        if self._drop_sql is not None:
            self._script(self._drop_sql)
            self._conn.commit()


def replay_domain(
    conn: SyncConnection,
    domain: PackedDomain,
    l2_path: Path,
    *,
    prefix: str,
    dialect: Dialect,
) -> dict[str, dict[tuple[object, ...], object | None]]:
    """Re-emit ``domain``'s schema at ``dialect`` under ``prefix``, load
    all cells + anchors, refresh, and return ``{detector: engine_map}``
    for every check. The caller compares each against
    ``domain.expected_for(detector)`` (the residual-derived expectation,
    dialect-independent). Drops the emitted objects on the way out."""
    from recon_gen.common.l2.loader import load_instance  # noqa: PLC0415
    from recon_gen.common.l2.schema import emit_schema_drop_sql  # noqa: PLC0415

    instance = load_instance(l2_path)
    drop_sql = emit_schema_drop_sql(instance, prefix=prefix, dialect=dialect)
    artifacts = artifacts_for(l2_path, prefix=prefix, dialect=dialect)
    db = DialectReplayDB(conn, artifacts, dialect, drop_sql=drop_sql)
    try:
        tx: list[TxRow] = list(domain.anchor_tx)
        bal: list[BalRow] = list(domain.anchor_bal)
        for cell in domain.cells:
            tx.extend(cell.tx_rows)
            bal.extend(cell.bal_rows)
        db.insert(tx, bal)
        db.refresh()
        return {
            check.detector: check.read_engine(db)  # pyright: ignore[reportArgumentType]: read_engine takes the EnumerationDB structural surface (prefix + fetchall); DialectReplayDB supplies exactly that
            for check in domain.checks
        }
    finally:
        db.drop()
