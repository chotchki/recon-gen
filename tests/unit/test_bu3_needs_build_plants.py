"""BU.3 — Happy-path smoke tests for the 5 needs-build plant adapters.

One test per BU.3 registry kind. Each test:

1. Loads the bundled sasquatch_pr L2 (rich enough to satisfy every
   picker — Required chain, aggregating-rail bundles_activity, rails
   with metadata_keys, multi-cell LimitSchedules, internal Accounts
   with diverse roles).
2. Invokes the registry's plant_function with realistic kwargs.
3. Asserts the returned SQL is a non-empty string (the adapter
   constructed the Plant + reached emit / SQL render without raising).

Construction-side only — no DB, no matview refresh, no row-count
assertions. The Lock 9 parameterized e2e in
test_bu2b_registry_anti_drift.py covers the contract surface at
scale; this file is per-kind sanity for "did the picker happen to
find what the adapter needs against the bundled L2".

Mirrors test_bu2b_l1_adapters_happy_path.py's shape — adding a
follow-on plant kind = adding one test here + one entry in
plant_registry.py.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from recon_gen.common.l2 import load_instance
from recon_gen.common.l2.plant_registry import get_entry
from recon_gen.common.sql.dialect import Dialect


_FIXTURES = Path(__file__).resolve().parent.parent / "l2"
_PREFIX = "sasquatch_pr"
_ANCHOR = datetime(2026, 5, 30, 14, 0, 0)


@pytest.fixture(scope="module")
def instance() -> object:
    """sasquatch_pr satisfies every BU.3 picker — see module docstring."""
    return load_instance(_FIXTURES / "sasquatch_pr.yaml")


def _invoke(kind: str, *, instance: object, **kwargs: object) -> str:
    entry = get_entry(kind)
    assert entry is not None, f"registry missing {kind!r}"
    sql = entry.plant_function(
        prefix=_PREFIX,
        dialect=Dialect.SQLITE,
        anchor=_ANCHOR,
        instance=instance,
        **kwargs,
    )
    assert isinstance(sql, str)
    assert sql.strip(), f"empty SQL from {kind!r} adapter"
    return sql


# -- BU.3.1 — L1 Cap ------------------------------------------------------


def test_expected_eod_balance_breach_adapter_happy_path(
    instance: object,
) -> None:
    sql = _invoke(
        "expected_eod_balance_breach", instance=instance,
        days_ago=2, expected="100.00", variance="5.00",
    )
    # Smoke: the plant should emit at least one INSERT INTO ... _daily_balances.
    assert "_daily_balances" in sql.lower()


# -- BU.3.2-5 — L2FT Hygiene ----------------------------------------------


def test_chain_orphan_adapter_happy_path(instance: object) -> None:
    sql = _invoke("chain_orphan", instance=instance, count=3)
    # Plants INSERTs against the chain-parent rail; no child planted.
    assert "INSERT INTO" in sql
    assert sql.count("INSERT INTO") == 3


def test_dead_bundles_activity_adapter_happy_path(
    instance: object,
) -> None:
    sql = _invoke("dead_bundles_activity", instance=instance)
    # DELETE on the bundle_target rail_name.
    assert "DELETE FROM" in sql
    assert "rail_name" in sql


def test_dead_metadata_adapter_happy_path(instance: object) -> None:
    sql = _invoke("dead_metadata", instance=instance)
    assert "DELETE FROM" in sql
    assert "rail_name" in sql


def test_dead_limit_schedule_adapter_happy_path(instance: object) -> None:
    sql = _invoke("dead_limit_schedule", instance=instance)
    assert "DELETE FROM" in sql
    # Targets parent_role + rail_name + Debit direction.
    assert "account_parent_role" in sql
    assert "Debit" in sql


# -- Negative path: missing instance --------------------------------------


def test_bu3_adapters_reject_missing_instance() -> None:
    """All five BU.3 adapters require an L2Instance to pick targets
    against; ``instance=None`` should surface ``_require_instance``'s
    Trainer-readable error rather than an AttributeError deeper in the
    pickers."""
    for kind, kwargs in (
        (
            "expected_eod_balance_breach",
            {"days_ago": 2, "expected": "100.00", "variance": "5.00"},
        ),
        ("chain_orphan", {"count": 3}),
        ("dead_bundles_activity", {}),
        ("dead_metadata", {}),
        ("dead_limit_schedule", {}),
    ):
        with pytest.raises(ValueError, match="L2Instance"):
            _invoke(kind, instance=None, **kwargs)
