"""DQ.4.2b — ``contract_from`` derives a contract from the source matview's
emitted columns, so a matview RENAME fails at import (not silently at
live-query).

The headline gate is ``test_..._rename_raises``: the reconciliation gate
(DQ.4.2a) goes GREEN on a renamed source column (the orphaned contract
name reconciles with nothing → skipped as dataset-computed), but a
``keep()`` of that name can't resolve → ``KeyError`` the moment the app
module imports.
"""
from __future__ import annotations

import pytest

from recon_gen.common.contracts import added, contract_from, dollars, keep
from recon_gen.common.dataset_contract import ColumnShape, Storage
from recon_gen.common.db_objects import SCHEMA_GRAPH
from recon_gen.common.ids import DbObjectId


def test_keep_inherits_the_source_columnspec_verbatim() -> None:
    """A ``keep`` column is the source matview's emitted ColumnSpec — same
    name, type, shape, currency, storage."""
    c = contract_from("drift", [keep("account_id"), keep("business_day_start")])
    account_id, day = c.columns
    assert account_id.name == "account_id"
    assert account_id.shape is ColumnShape.ACCOUNT_ID  # inherited from the matview
    assert account_id.currency is False
    day = c.columns[1]
    assert day.type == "DATETIME" and day.shape is ColumnShape.DATETIME_DAY


def test_keep_applies_contract_side_overrides() -> None:
    """``**overrides`` enrich the inherited spec (the DQ.4.2a shape-enrich
    pattern) without touching the source."""
    c = contract_from(
        "stuck_pending",
        [keep("account_name", display_name="Account", hidden=True)],
    )
    (col,) = c.columns
    assert col.name == "account_name"
    assert col.display_name == "Account"
    assert col.hidden is True


def test_dollars_widens_cents_to_dollars() -> None:
    """A ``dollars`` column resolves NAME against the source (money-CENTS)
    but lands DECIMAL / DOLLARS / currency=False — the house pattern (the
    dataset SQL pre-divides; a CENTS leak is the BG.7 100x render bug)."""
    src = SCHEMA_GRAPH.by_id(DbObjectId("drift"))["stored_balance"]
    assert src.currency is True and src.storage is Storage.CENTS  # precondition
    c = contract_from("drift", [dollars("stored_balance")])
    (col,) = c.columns
    assert col.name == "stored_balance"
    assert col.type == "DECIMAL"
    assert col.currency is False
    assert col.storage is Storage.DOLLARS


def test_dollars_on_a_non_money_column_raises() -> None:
    """``dollars`` is ONLY the money widen — pointing it at a non-money
    source column is an authoring error, caught loud."""
    with pytest.raises(ValueError, match="cents.dollars widen"):
        contract_from("drift", [dollars("account_id")])


def test_added_passes_through_uncchecked() -> None:
    """A dataset-computed column has no source and isn't name-resolved."""
    c = contract_from(
        "drift",
        [added("account_display", "STRING", shape=ColumnShape.ACCOUNT_DISPLAY)],
    )
    (col,) = c.columns
    assert col.name == "account_display"
    assert col.shape is ColumnShape.ACCOUNT_DISPLAY


def test_rename_raises_at_construction() -> None:
    """THE gate: keep() a name the source no longer emits → KeyError at
    import (a matview-column rename can't slip through green as DQ.4.2a's
    'dataset-computed' skip would let it)."""
    with pytest.raises(KeyError, match="stored_balance_renamed"):
        contract_from("drift", [keep("stored_balance_renamed")])


def test_unknown_source_raises() -> None:
    """A typo'd / dropped source matview id fails loud at build."""
    with pytest.raises(KeyError):
        contract_from("drfit", [keep("account_id")])  # typo'd source
