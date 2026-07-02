"""DS.3.4 — enumeration harness: DB machinery, cell packer, comparator.

The harness runs the REAL emitted artifacts against a fresh in-memory
DuckDB per packed domain: ``emit_schema`` + the real config populate
(``serialize_l2`` -> ``emit_config_populate_sql``, so config-reading
detectors see the true chain / cap declarations — the ds33a lesson:
an empty ``l2_json`` makes chain assertions vacuously green) + the
real ``refresh_matviews_sql`` refresh order.

Packing contracts (why cells can share one DB):

- TRANSFER-KEYED (cardinality / threshold / derivation families):
  every cell's account ids, transfer ids and leg ids carry a
  fixed-width per-cell prefix, so cells are row-disjoint and no
  detector JOIN can cross cells. Proven clean by the DS.0 cardinality
  spike (combined-DB == per-family packed == isolated sample).
- WINDOW-ALIGNED (the LOCF family: drift / ledger_drift / overdraft /
  expected_eod): disjoint account keys alone do NOT isolate, because
  ``effective_balances`` builds a FLEET-WIDE calendar-day spine from
  MIN/MAX of every emitted balance day (DS.0 doc §5). The contract:
  every packed LOCF domain carries ANCHOR balance rows pinning the
  spine to the domain window's first + last day, and the residual
  side evaluates every cell over exactly that window. The anchors
  ride along in the isolated per-cell DBs too, so packed and
  isolated see the identical spine by construction.

Statement timeout: every DB call goes through ``_guarded`` — a
daemon ``threading.Timer`` watchdog that calls ``conn.interrupt()``
after ``STATEMENT_TIMEOUT_SECONDS``. DuckDB raises out of the
interrupted call; the guard re-raises as ``EnumerationTimeout`` with
the call label, so a hang (the money_trail divergence class) fails
LOUDLY instead of eating the tier. Chosen over a subprocess wrapper
because it keeps the connection object (and the packed engine reads)
in-process with zero serialization cost; ``interrupt()`` is DuckDB's
documented cross-thread cancellation seam.
"""
from __future__ import annotations

import datetime as dt
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import duckdb
import pyarrow as pa

from recon_gen.common.db import execute_script
from recon_gen.common.env_keys import RECON_GEN_ENUM_TIER
from recon_gen.common.l2.config_table import emit_config_populate_sql
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.primitives import CREDIT, DEBIT, SCOPE_INTERNAL
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.l2.serializer import serialize_l2
from recon_gen.common.money import Cents
from recon_gen.common.spine.residuals import BalanceRow, LegRow, ResidualState
from recon_gen.common.sql import Dialect

# The owned temporal frame for every enumeration DB: written to the
# config kv ``as_of`` row at populate time and passed explicitly to the
# threshold residuals. One day after the 2-day domain window so ages
# land in whole positive seconds.
ENUM_AS_OF: Final = dt.datetime(2030, 1, 3)

STATEMENT_TIMEOUT_SECONDS: Final = 60.0

# Violation keys are plain hashable tuples (string identity components
# first — the packed-vs-isolated lemma matches cells by fixed-width id
# prefix over the string components). Values are the residual payload
# compared alongside membership (int residual, or None when the
# detector's law is pure set-membership).
type ViolationKey = tuple[object, ...]
type ViolationMap = dict[ViolationKey, object]


class EnumerationTimeout(Exception):
    """A guarded DB call exceeded STATEMENT_TIMEOUT_SECONDS and was
    interrupted. Loud by design — a hanging refresh must fail the
    gate, never stall the tier."""


class PackingError(Exception):
    """Two packed cells produced the same expected violation key —
    the disjoint-id packing contract is broken in the domain builder."""


# ---------------------------------------------------------------------------
# Row shapes — one definition of the base-table column order.


TX_COLS: Final[tuple[str, ...]] = (
    "id", "account_id", "account_name", "account_role", "account_scope",
    "account_parent_role", "amount_money", "amount_direction", "status",
    "posting", "transfer_id", "transfer_parent_id", "rail_name",
    "template_name", "bundle_id", "origin", "metadata", "supersedes",
)

DB_COLS: Final[tuple[str, ...]] = (
    "account_id", "account_name", "account_role", "account_scope",
    "account_parent_role", "expected_eod_balance", "business_day_start",
    "business_day_end", "money", "metadata",
)


@dataclass(frozen=True, slots=True)
class TxRow:
    """One ``<prefix>_transactions`` insert row (``entry`` omitted —
    the sequence default assigns it in insert order, which is the
    supersession order the builder controls)."""

    id: str
    account_id: str
    account_name: str
    account_role: str | None
    account_scope: str
    account_parent_role: str | None
    amount_money: int
    amount_direction: str
    status: str
    posting: dt.datetime
    transfer_id: str
    transfer_parent_id: str | None
    rail_name: str
    template_name: str | None
    bundle_id: str | None
    origin: str
    metadata: str | None
    supersedes: str | None

    def as_tuple(self) -> tuple[object, ...]:
        return (
            self.id, self.account_id, self.account_name, self.account_role,
            self.account_scope, self.account_parent_role, self.amount_money,
            self.amount_direction, self.status, self.posting,
            self.transfer_id, self.transfer_parent_id, self.rail_name,
            self.template_name, self.bundle_id, self.origin, self.metadata,
            self.supersedes,
        )


@dataclass(frozen=True, slots=True)
class BalRow:
    """One ``<prefix>_daily_balances`` insert row (``entry`` omitted,
    same supersession-by-insert-order contract as TxRow)."""

    account_id: str
    account_name: str
    account_role: str | None
    account_scope: str
    account_parent_role: str | None
    expected_eod_balance: int | None
    business_day_start: dt.datetime
    business_day_end: dt.datetime
    money: int
    metadata: str | None

    def as_tuple(self) -> tuple[object, ...]:
        return (
            self.account_id, self.account_name, self.account_role,
            self.account_scope, self.account_parent_role,
            self.expected_eod_balance, self.business_day_start,
            self.business_day_end, self.money, self.metadata,
        )


# ---------------------------------------------------------------------------
# Cell builder — one construction site emitting BOTH the engine rows
# and their residual-domain twins, so the two sides can never drift on
# a field mapping.


class CellBuilder:
    """Accumulates one cell's rows. ``entry`` counters are per-builder;
    only the RELATIVE order within one logical key matters for
    supersession, and the engine's global BIGSERIAL preserves the same
    relative order because rows insert in builder order."""

    def __init__(self) -> None:
        self._tx: list[TxRow] = []
        self._bal: list[BalRow] = []
        self._legs: list[LegRow] = []
        self._bal_states: list[BalanceRow] = []
        self._entry = 0

    def leg(
        self,
        *,
        id: str,
        account: str,
        amount: int,
        status: str,
        posting: dt.datetime,
        transfer: str,
        parent: str | None = None,
        rail: str = "RailX",
        template: str | None = None,
        bundle: str | None = None,
        role: str | None = "CustomerSubledger",
        parent_role: str | None = "CustomerLedger",
        scope: str = SCOPE_INTERNAL,
    ) -> None:
        self._entry += 1
        direction = DEBIT if amount < 0 else CREDIT
        self._tx.append(TxRow(
            id=id, account_id=account, account_name=account, account_role=role,
            account_scope=scope, account_parent_role=parent_role,
            amount_money=amount, amount_direction=direction, status=status,
            posting=posting, transfer_id=transfer, transfer_parent_id=parent,
            rail_name=rail, template_name=template, bundle_id=bundle,
            origin="enum", metadata=None, supersedes=None,
        ))
        self._legs.append(LegRow(
            id=id, entry=self._entry, account_id=account,
            amount=Cents(amount), status=status, posting=posting,
            transfer_id=transfer, transfer_parent_id=parent, rail_name=rail,
            template_name=template, bundle_id=bundle, account_scope=scope,
            account_role=role, account_parent_role=parent_role,
        ))

    def balance(
        self,
        *,
        account: str,
        day: dt.date,
        money: int,
        expected_eod: int | None = None,
        role: str | None = "CustomerSubledger",
        parent_role: str | None = "CustomerLedger",
        scope: str = SCOPE_INTERNAL,
        end_time: dt.time = dt.time(23, 59, 59),
    ) -> None:
        self._entry += 1
        start = dt.datetime.combine(day, dt.time(0, 0, 0))
        end = dt.datetime.combine(day, end_time)
        self._bal.append(BalRow(
            account_id=account, account_name=account, account_role=role,
            account_scope=scope, account_parent_role=parent_role,
            expected_eod_balance=expected_eod, business_day_start=start,
            business_day_end=end, money=money, metadata=None,
        ))
        self._bal_states.append(BalanceRow(
            account_id=account, entry=self._entry, day=day,
            money=Cents(money), day_end=end,
            expected_eod=None if expected_eod is None else Cents(expected_eod),
            account_scope=scope, account_role=role,
            account_parent_role=parent_role,
        ))

    def rows(self) -> tuple[tuple[TxRow, ...], tuple[BalRow, ...]]:
        return tuple(self._tx), tuple(self._bal)

    def state(self) -> ResidualState:
        return ResidualState(
            legs=tuple(self._legs), balances=tuple(self._bal_states),
        )


# ---------------------------------------------------------------------------
# Packed domain shapes.


@dataclass(frozen=True, slots=True)
class PackedCell:
    """One enumerated database state, packaged for packing: its rows,
    its per-detector expected violations (already residual-derived),
    and the fixed-width id prefixes that own every string identity in
    its keys (the packed-vs-isolated restriction predicate)."""

    tx_rows: tuple[TxRow, ...]
    bal_rows: tuple[BalRow, ...]
    prefixes: tuple[str, ...]
    expected: Mapping[str, ViolationMap]


@dataclass(frozen=True, slots=True)
class DetectorCheck:
    """One detector's engine read for a packed domain. ``read_engine``
    returns ``{key: value}`` in the same shape the cells' expected
    maps use."""

    detector: str
    read_engine: Callable[[EnumerationDB], ViolationMap]


@dataclass(frozen=True, slots=True)
class PackedDomain:
    """A packed enumeration domain: N disjoint cells + the anchors the
    packing contract requires + the detector checks it answers for."""

    name: str
    artifacts: InstanceArtifacts
    cells: tuple[PackedCell, ...]
    checks: tuple[DetectorCheck, ...]
    anchor_tx: tuple[TxRow, ...] = ()
    anchor_bal: tuple[BalRow, ...] = ()

    def expected_for(self, detector: str) -> ViolationMap:
        """Merge the per-cell expected maps; a key collision across
        cells is a packing-contract violation in the domain builder,
        not a comparator diff — fail loudly."""
        merged: ViolationMap = {}
        for i, cell in enumerate(self.cells):
            for key, value in cell.expected.get(detector, {}).items():
                if key in merged:
                    raise PackingError(
                        f"{self.name}/{detector}: expected key {key!r} "
                        f"produced by two cells (second: cell {i}) — "
                        f"cell ids are not disjoint",
                    )
                merged[key] = value
        return merged


# ---------------------------------------------------------------------------
# Instance artifacts (schema / refresh / serialized config) — cached
# per (l2 path, prefix) so domain builders share one emit.


@dataclass(frozen=True, slots=True)
class InstanceArtifacts:
    prefix: str
    schema_sql: str
    refresh_sql: str
    config_sql: str


_ARTIFACT_CACHE: dict[tuple[str, str], InstanceArtifacts] = {}


def artifacts_for(l2_path: Path, *, prefix: str) -> InstanceArtifacts:
    """Emit (and cache) the real schema + refresh + config-populate
    scripts for ``l2_path``. The config populate carries the REAL
    serialized instance so ``v_config_*``-reading detectors resolve
    true declarations."""
    key = (str(l2_path.resolve()), prefix)
    cached = _ARTIFACT_CACHE.get(key)
    if cached is not None:
        return cached
    import json  # noqa: PLC0415 — only needed on cache miss

    import yaml  # noqa: PLC0415

    instance = load_instance(l2_path)
    l2_json = json.dumps(
        yaml.safe_load(serialize_l2(instance)), separators=(",", ":"),
    )
    built = InstanceArtifacts(
        prefix=prefix,
        schema_sql=emit_schema(instance, prefix=prefix, dialect=Dialect.DUCKDB),
        refresh_sql=refresh_matviews_sql(
            instance, prefix=prefix, dialect=Dialect.DUCKDB,
        ),
        config_sql=emit_config_populate_sql(
            prefix=prefix, cfg_json="{}", l2_json=l2_json,
            as_of=ENUM_AS_OF, dialect=Dialect.DUCKDB,
        ),
    )
    _ARTIFACT_CACHE[key] = built
    return built


# ---------------------------------------------------------------------------
# Guarded DuckDB wrapper.


class EnumerationDB:
    """One fresh in-memory DuckDB carrying the real emitted schema +
    config. Every DB touch goes through the statement-timeout guard."""

    def __init__(self, artifacts: InstanceArtifacts) -> None:
        self.prefix = artifacts.prefix
        self._conn = duckdb.connect(":memory:")
        self._run_script("schema apply", artifacts.schema_sql)
        self._run_script("config populate", artifacts.config_sql)
        self._refresh_sql = artifacts.refresh_sql

    def _guarded[T](self, label: str, fn: Callable[[], T]) -> T:
        fired = threading.Event()

        def _interrupt() -> None:
            fired.set()
            self._conn.interrupt()

        timer = threading.Timer(STATEMENT_TIMEOUT_SECONDS, _interrupt)
        timer.daemon = True
        timer.start()
        try:
            return fn()
        except duckdb.Error as exc:
            if fired.is_set():
                raise EnumerationTimeout(
                    f"enumeration DB call {label!r} exceeded "
                    f"{STATEMENT_TIMEOUT_SECONDS}s and was interrupted",
                ) from exc
            raise
        finally:
            timer.cancel()

    def _run_script(self, label: str, sql: str) -> None:
        self._guarded(
            label,
            lambda: execute_script(self._conn, sql, dialect=Dialect.DUCKDB),
        )

    def insert(
        self, tx_rows: Iterable[TxRow], bal_rows: Iterable[BalRow],
    ) -> None:
        """Arrow bulk load (the DS.0 spike's proven path — ~40k rows/s;
        ``executemany`` is row-at-a-time on DuckDB and would blow the
        tier budget at domain scale)."""
        tx = [r.as_tuple() for r in tx_rows]
        bal = [r.as_tuple() for r in bal_rows]
        if tx:
            self._bulk_insert(f"{self.prefix}_transactions", TX_COLS, tx)
        if bal:
            self._bulk_insert(f"{self.prefix}_daily_balances", DB_COLS, bal)

    def _bulk_insert(
        self, table: str, cols: tuple[str, ...],
        rows: list[tuple[object, ...]],
    ) -> None:
        tbl = pa.table({c: [r[i] for r in rows] for i, c in enumerate(cols)})

        def _load() -> None:
            self._conn.register("enum_arrow", tbl)
            self._conn.execute(
                f"INSERT INTO {table} ({', '.join(cols)}) "
                f"SELECT * FROM enum_arrow",
            )
            self._conn.unregister("enum_arrow")

        self._guarded(f"bulk insert {table}", _load)

    def refresh(self) -> None:
        """The full real matview refresh chain, in emitted order."""
        self._run_script("matview refresh", self._refresh_sql)

    def fetchall(self, sql: str) -> list[tuple[object, ...]]:
        def _q() -> list[tuple[object, ...]]:
            cur = self._conn.execute(sql)
            return [tuple(row) for row in cur.fetchall()]

        return self._guarded(f"query {sql[:80]}", _q)

    def close(self) -> None:
        self._conn.close()


def build_packed_db(domain: PackedDomain) -> EnumerationDB:
    """Fresh DB + all cells' rows + anchors + one real refresh."""
    db = EnumerationDB(domain.artifacts)
    tx: list[TxRow] = list(domain.anchor_tx)
    bal: list[BalRow] = list(domain.anchor_bal)
    for cell in domain.cells:
        tx.extend(cell.tx_rows)
        bal.extend(cell.bal_rows)
    db.insert(tx, bal)
    db.refresh()
    return db


# ---------------------------------------------------------------------------
# Violation-set comparator.


def diff_violations(
    engine: ViolationMap,
    expected: ViolationMap,
    *,
    label: str,
    limit: int = 6,
) -> str:
    """Readable diff between the engine's violation map and the
    residual-derived expected map. Empty string == exact match."""
    engine_only = sorted(
        (k for k in engine.keys() - expected.keys()), key=repr,
    )
    expected_only = sorted(
        (k for k in expected.keys() - engine.keys()), key=repr,
    )
    value_diff = sorted(
        (k for k in engine.keys() & expected.keys()
         if engine[k] != expected[k]),
        key=repr,
    )
    total = len(engine_only) + len(expected_only) + len(value_diff)
    if total == 0:
        return ""
    lines = [
        f"{label}: {total} divergent keys "
        f"(engine-only={len(engine_only)} residual-only={len(expected_only)} "
        f"value-diff={len(value_diff)}; engine={len(engine)} "
        f"residual={len(expected)})",
    ]
    for name, keys in (
        ("engine-only", engine_only), ("residual-only", expected_only),
    ):
        for k in keys[:limit]:
            side = engine if name == "engine-only" else expected
            lines.append(f"  {name} {k!r}: {side[k]!r}")
    for k in value_diff[:limit]:
        lines.append(
            f"  value-diff {k!r}: engine={engine[k]!r} "
            f"residual={expected[k]!r}",
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Packed-vs-isolated sampled lemma.


def _key_in_cell(key: ViolationKey, prefixes: tuple[str, ...]) -> bool:
    """A violation key belongs to a cell when any of its string
    components carries one of the cell's fixed-width id prefixes.
    Fixed-width per-cell indices make prefix collisions between
    distinct cells unrepresentable."""
    return any(
        isinstance(part, str) and part.startswith(prefix)
        for part in key
        for prefix in prefixes
    )


def isolated_cell_diffs(
    domain: PackedDomain,
    packed_engine: Mapping[str, ViolationMap],
    cell_index: int,
) -> list[str]:
    """Run ONE cell in its own fresh DB (anchors included — they are
    part of the packing contract, not of any cell) and diff its engine
    output against the packed run restricted to the cell's keys.
    Returns human-readable mismatch descriptions (empty == lemma holds
    for this cell)."""
    cell = domain.cells[cell_index]
    db = EnumerationDB(domain.artifacts)
    try:
        db.insert(
            list(domain.anchor_tx) + list(cell.tx_rows),
            list(domain.anchor_bal) + list(cell.bal_rows),
        )
        db.refresh()
        problems: list[str] = []
        for check in domain.checks:
            iso = check.read_engine(db)
            packed_restricted = {
                k: v for k, v in packed_engine[check.detector].items()
                if _key_in_cell(k, cell.prefixes)
            }
            diff = diff_violations(
                iso, packed_restricted,
                label=(
                    f"{domain.name}/{check.detector} cell {cell_index} "
                    f"isolated-vs-packed"
                ),
            )
            if diff:
                problems.append(diff)
        return problems
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Tier knob.


def enum_tier() -> str:
    """``ci`` (default) or ``nightly`` — from RECON_GEN_ENUM_TIER."""
    return RECON_GEN_ENUM_TIER.get_or_none() or "ci"


def is_nightly() -> bool:
    return enum_tier() == "nightly"
