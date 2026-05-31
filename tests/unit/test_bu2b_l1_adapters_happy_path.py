"""BU.2b stage 2 — L1 adapter happy-path smoke tests.

One test per L1 registry kind. Each test:

1. Loads the bundled sasquatch_pr L2 (rich enough to satisfy every
   picker — fan_in chains, XOR groups, inbound + outbound
   LimitSchedules, max_pending_age / max_unbundled_age rails).
2. Invokes the registry's plant_function with realistic kwargs.
3. Asserts the returned SQL is a non-empty string (the adapter
   constructed the Plant dataclass + reached emit_seed without
   raising).

Construction-side only — no DB, no matview refresh, no row-count
assertions. The Lock 9 parameterized e2e in
test_bu2b_registry_anti_drift.py covers the contract surface at
scale; this file is per-kind sanity for "did the picker happen to
find what the adapter needs against the bundled L2."

Anti-drift property: when an adapter's primitive set changes, this
file's kwarg dict needs the same change — surfaces signature drift
faster than the registry-walking anti-drift tests (which only check
inspect.signature, not call success).
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
    """Load sasquatch_pr once per module — rich L2 satisfies every
    L1 picker (fan_in chains, XOR groups, both directions of
    LimitSchedule, aging-cap rails)."""
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


# -- L1 Conservation -------------------------------------------------------


def test_drift_adapter_happy_path(instance: object) -> None:
    _invoke("drift", instance=instance, days_ago=5, delta_money="75.00")


def test_ledger_drift_adapter_happy_path(instance: object) -> None:
    _invoke(
        "ledger_drift", instance=instance, days_ago=5, delta_money="75.00",
    )


def test_overdraft_adapter_happy_path(instance: object) -> None:
    _invoke("overdraft", instance=instance, days_ago=6, money="-1500.00")


def test_overdraft_adapter_rejects_positive_money(instance: object) -> None:
    """The SHOULD only fires on negative balance — positive input is a
    misconfiguration; loud-fail beats silently planting nothing."""
    with pytest.raises(ValueError, match="MUST be negative"):
        _invoke("overdraft", instance=instance, days_ago=6, money="100.00")


# -- L1 Cap ----------------------------------------------------------------


def test_limit_breach_outbound_adapter_happy_path(instance: object) -> None:
    _invoke(
        "limit_breach_outbound", instance=instance,
        days_ago=4, cap_breach_amount="15000.00",
    )


def test_limit_breach_inbound_adapter_happy_path(instance: object) -> None:
    _invoke(
        "limit_breach_inbound", instance=instance,
        days_ago=3, cap_breach_amount="15000.00",
    )


# -- L1 Aging --------------------------------------------------------------


def test_stuck_pending_adapter_happy_path(instance: object) -> None:
    _invoke(
        "stuck_pending", instance=instance,
        days_ago=30, amount_money="450.00",
    )


def test_stuck_unbundled_adapter_happy_path(instance: object) -> None:
    _invoke(
        "stuck_unbundled", instance=instance,
        days_ago=30, amount_money="12.50",
    )


# -- L1 Chain coherence ---------------------------------------------------


def test_chain_parent_disagreement_adapter_happy_path(
    instance: object,
) -> None:
    _invoke("chain_parent_disagreement", instance=instance, days_ago=1)


def test_xor_group_missed_adapter_happy_path(instance: object) -> None:
    _invoke("xor_group_missed", instance=instance, days_ago=0)


def test_xor_group_overlap_adapter_happy_path(instance: object) -> None:
    _invoke("xor_group_overlap", instance=instance, days_ago=1)


def test_fan_in_missing_parent_adapter_happy_path(instance: object) -> None:
    _invoke("fan_in_missing_parent", instance=instance, days_ago=4)


def test_fan_in_extra_parent_adapter_happy_path(instance: object) -> None:
    """sasquatch_pr's fan_in chain declares expected_parent_count — the
    extra-parent plant requires it; the adapter raises a clear error
    when the picked chain leaves it unset."""
    _invoke("fan_in_extra_parent", instance=instance, days_ago=3)


def test_multi_xor_missed_adapter_happy_path(instance: object) -> None:
    _invoke("multi_xor_missed", instance=instance, days_ago=6)


def test_multi_xor_overlap_adapter_happy_path(instance: object) -> None:
    _invoke("multi_xor_overlap", instance=instance, days_ago=5)


# -- L1 Audit --------------------------------------------------------------


def test_supersession_audit_adapter_happy_path(instance: object) -> None:
    _invoke(
        "supersession_audit", instance=instance, days_ago=3,
        original_amount="250.00", corrected_amount="275.00",
    )


# -- Negative path: missing instance --------------------------------------


def test_l1_adapter_rejects_missing_instance() -> None:
    """Every L1 adapter requires an L2Instance; the route must thread
    it through. A None / object-typed instance fires _require_instance's
    loud error rather than a cryptic AttributeError deeper in the
    pickers."""
    with pytest.raises(ValueError, match="L2Instance"):
        _invoke("drift", instance=None, days_ago=5, delta_money="75.00")
