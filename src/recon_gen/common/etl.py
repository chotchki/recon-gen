"""Public ETL-hook helpers for writing into ``<prefix>_*`` base tables.

The Recon Generator validates data; it does not move it. The customer
ETL is responsible for landing rows into ``<prefix>_transactions`` and
``<prefix>_daily_balances`` per the contract documented in
``docs/Schema_v6.md`` (canonical INSERT shapes in
``common/etl_examples.py``).

This module provides typed helpers that take a loaded ``Config`` +
``L2Instance`` and write one base-table row dialect-neutrally. The
helpers build SQL literals via ``common/sql/literals.py`` —
appropriate for trusted ETL data (no untrusted free-text fields) and
sidesteps the per-dialect paramstyle problem (``%s`` vs ``:1`` vs
``?``). For untrusted input, hand-roll parameterized binding through
the connection's native paramstyle.

Money is in dollars (``Decimal`` / ``str``) at the boundary; the
helpers convert to BIGINT integer cents via ``common/money.py::Cents``
(the AO.1 storage convention).
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from typing import Protocol, runtime_checkable

from recon_gen.common.l2.primitives import POSTED_STATUS, AmountDirection
from recon_gen.common.money import Cents
from recon_gen.common.sql.dialect import Dialect
from recon_gen.common.sql.literals import render_sql_literal


@runtime_checkable
class _AccountLike(Protocol):
    """Structural shape for the account columns the schema needs
    denormalized onto every balance / transaction row.

    Any ``L2Instance.accounts`` entry satisfies this (also covers
    template-materialized accounts the integrator constructs from
    ``AccountTemplate`` per the ETL pipeline).
    """

    @property
    def id(self) -> str: ...  # noqa: A003 — `id` matches the column name
    @property
    def name(self) -> str: ...
    @property
    def role(self) -> str: ...
    @property
    def scope(self) -> str: ...
    @property
    def parent_role(self) -> str | None: ...


_DAILY_BALANCES_COLUMNS = (
    "account_id", "account_name", "account_role", "account_scope",
    "account_parent_role", "expected_eod_balance",
    "business_day_start", "business_day_end", "money", "metadata",
    "supersedes",
)
_DAILY_BALANCES_TIMESTAMP_INDEXES = frozenset({
    _DAILY_BALANCES_COLUMNS.index("business_day_start"),
    _DAILY_BALANCES_COLUMNS.index("business_day_end"),
})


def _bod_iso(business_day: date, offset_hours: int) -> str:
    return f"{business_day.isoformat()}T{offset_hours:02d}:00:00"


def _eod_iso(business_day: date, offset_hours: int) -> str:
    return f"{(business_day + timedelta(days=1)).isoformat()}T{offset_hours:02d}:00:00"


def write_daily_balance(
    cursor: object,
    dialect: Dialect,
    *,
    prefix: str,
    account: _AccountLike,
    business_day: date,
    balance_dollars: Decimal | str | int,
    expected_eod_dollars: Decimal | str | int | None = None,
    metadata: dict[str, object] | None = None,
    offset_hours: int = 0,
    supersedes: str | None = None,
) -> None:
    """Insert one row into ``<prefix>_daily_balances`` via SQL literals.

    Args:
      cursor: A DBAPI cursor opened against the demo DB (typically
        ``connect_demo_db(cfg).cursor()``). The helper calls
        ``cursor.execute(sql)`` exactly once.
      dialect: ``Dialect`` enum matching the cursor's underlying DB.
        Drives the timestamp literal syntax.
      prefix: The L2 instance prefix (``cfg.aws.deployment_name`` shape,
        e.g. ``"acme_pr"``). Substituted into the table name.
      account: An ``L2Instance.accounts`` entry or compatible shape
        carrying the denorm fields the schema requires.
      business_day: The calendar date the row applies to. The helper
        derives ``business_day_start`` (BOD) and ``business_day_end``
        (next-day BOD) ISO timestamps with ``offset_hours`` applied.
      balance_dollars: Stored EOD balance in dollars. Converted to
        integer cents via ``Cents.from_dollars`` at the boundary.
      expected_eod_dollars: Optional L1 invariant target in dollars.
        ``None`` (default) ⇒ no EOD invariant on this row.
      metadata: Optional JSON dict (per-day overrides, scenario_id,
        ``limits`` sub-map). ``None`` (default) ⇒ NULL.
      offset_hours: Business-day offset (L2 ``business_day_offset``).
        Defaults to midnight-aligned (0). Accounts using e.g. an
        Eastern-time EOD cutoff at 17:00 UTC pass 17.
      supersedes: Optional logical-id reference for supersession
        (rewriting a previously-Posted row). ``None`` (default) for
        first-write rows.

    Raises:
      TypeError: if a column value isn't None / int / str shaped
        (forwarded from ``render_sql_literal``).
    """
    money_cents = Cents.from_dollars(balance_dollars).value
    expected_cents = (
        Cents.from_dollars(expected_eod_dollars).value
        if expected_eod_dollars is not None else None
    )
    metadata_json = (
        json.dumps(metadata, sort_keys=True, separators=(", ", ": "))
        if metadata else None
    )
    row = (
        account.id, account.name, account.role, account.scope,
        account.parent_role,
        expected_cents,
        _bod_iso(business_day, offset_hours),
        _eod_iso(business_day, offset_hours),
        money_cents,
        metadata_json,
        supersedes,
    )
    values = "(" + ", ".join(
        render_sql_literal(
            v, dialect,
            is_timestamp=(i in _DAILY_BALANCES_TIMESTAMP_INDEXES),
        )
        for i, v in enumerate(row)
    ) + ")"
    cols = ", ".join(_DAILY_BALANCES_COLUMNS)
    sql = f"INSERT INTO {prefix}_daily_balances ({cols}) VALUES {values}"
    cursor.execute(sql)  # type: ignore[attr-defined]: structural DBAPI cursor — Protocol lives in common/db.py::SyncCursor but importing here adds a circular dep


_TRANSACTIONS_COLUMNS = (
    "id", "account_id", "account_name", "account_role", "account_scope",
    "account_parent_role", "amount_money", "amount_direction", "status",
    "posting", "transfer_id", "rail_name", "origin", "metadata",
)
_TRANSACTIONS_TIMESTAMP_INDEXES = frozenset({
    _TRANSACTIONS_COLUMNS.index("posting"),
})


def write_transaction(
    cursor: object,
    dialect: Dialect,
    *,
    prefix: str,
    transaction_id: str,
    account: _AccountLike,
    amount_dollars: Decimal | str | int,
    transfer_id: str,
    rail_name: str,
    posting: str,
    origin: str,
    status: str = POSTED_STATUS,
    metadata: dict[str, object] | None = None,
) -> None:
    """Insert one row into ``<prefix>_transactions`` via SQL literals.

    Sibling of :func:`write_daily_balance` for the per-leg transaction
    feed. The schema is documented in ``docs/Schema_v6.md``; canonical
    INSERT shapes live in ``common/etl_examples.py``.

    Args:
      cursor: A DBAPI cursor opened against the demo DB.
      dialect: ``Dialect`` enum matching the cursor's underlying DB.
        Drives the timestamp literal syntax (Oracle keeps PL/SQL
        ``TIMESTAMP '...'``; the others take an ISO string).
      prefix: The L2 instance prefix (``cfg.aws.deployment_name`` shape).
      transaction_id: The leg's unique identifier (CR.17 lock — the
        caller ALWAYS provides this; no auto-generation). Becomes the
        ``id`` column.
      account: The account this leg posts on, denormalized onto the
        row (same ``_AccountLike`` Protocol as ``write_daily_balance``).
      amount_dollars: The signed money amount in dollars. Sign
        convention per CLAUDE.md / Schema_v6: ``> 0`` = money IN
        (Credit), ``< 0`` = money OUT (Debit). The helper derives
        ``amount_money`` (signed cents via ``Cents.from_dollars``)
        and ``amount_direction`` (the Debit/Credit accounting entry)
        — CR.17 lock: entry derivation is AUTOMATIC.
      transfer_id: The logical group every leg of one transfer-event
        shares. Legs of an internal transfer carry the SAME
        ``transfer_id`` so the L1 Conservation invariant
        (legs sum to zero) finds them. For single-leg externals,
        any unique identifier works.
      rail_name: The Rail this leg posts via (e.g.
        ``"CustomerFeeAccrual"``). Must match a declared L2 rail OR
        a hygiene-gap kind the L1/L2FT invariants triage.
      posting: ISO timestamp string (``"2030-01-15T10:30:00"``); the
        helper renders it as a dialect-correct timestamp literal.
      origin: One of the canonical origin values
        (``"InternalInitiated"``, ``"ExternalForcePosted"``,
        ``"ExternalAggregated"``) per Schema_v6.
      status: ``"Posted"`` (default) / ``"Pending"`` / ``"Failed"``
        — see Schema_v6 for the canonical enum.
      metadata: Optional JSON dict for per-leg overrides. ``None``
        (default) ⇒ NULL.

    Raises:
      TypeError: if a column value isn't None / int / str shaped
        (forwarded from ``render_sql_literal``).
      ValueError: if ``amount_dollars`` is exactly zero
        (ambiguous — Debit/Credit can't be derived from a zero leg).
    """
    money_cents = Cents.from_dollars(amount_dollars).value
    if money_cents == 0:
        raise ValueError(
            "write_transaction: amount_dollars cannot be zero — "
            "Debit/Credit direction is undefined for a zero-magnitude "
            "leg. Use a non-zero value, or write the row directly via "
            "SQL if a zero leg is genuinely the intended ETL shape."
        )
    # CR.17 lock — entry derivation is AUTOMATIC. Sign convention
    # per CLAUDE.md: positive = money IN (Credit), negative = OUT
    # (Debit). Schema_v6 stores both ``amount_money`` (signed cents)
    # and ``amount_direction`` (the enum); the helper keeps them
    # consistent so the L1 Conservation invariant
    # (SUM(amount_money) over a transfer_id = 0 for paired legs)
    # doesn't get fooled by a sign / direction mismatch.
    amount_direction: AmountDirection = "Credit" if money_cents > 0 else "Debit"
    metadata_json = (
        json.dumps(metadata, sort_keys=True, separators=(", ", ": "))
        if metadata else None
    )
    row = (
        transaction_id,
        account.id, account.name, account.role, account.scope,
        account.parent_role,
        money_cents, amount_direction, status,
        posting,
        transfer_id, rail_name, origin,
        metadata_json,
    )
    values = "(" + ", ".join(
        render_sql_literal(
            v, dialect,
            is_timestamp=(i in _TRANSACTIONS_TIMESTAMP_INDEXES),
        )
        for i, v in enumerate(row)
    ) + ")"
    cols = ", ".join(_TRANSACTIONS_COLUMNS)
    sql = f"INSERT INTO {prefix}_transactions ({cols}) VALUES {values}"
    cursor.execute(sql)  # type: ignore[attr-defined]: structural DBAPI cursor — Protocol lives in common/db.py::SyncCursor but importing here adds a circular dep
