"""Packed-domain assembly — composes the per-detector modules.

Lives apart from ``_locf.py`` / the detector modules so the import
graph stays a DAG: detector modules depend on shared machinery, the
assembly depends on detector modules, never the reverse.
"""
from __future__ import annotations

from tests.enumeration.domains import (
    chain_parent,
    drift,
    expected_eod,
    fan_in,
    ledger_drift,
    limit_breach,
    multi_xor,
    overdraft,
    stuck_pending,
    stuck_unbundled,
    xor_group,
)
from tests.enumeration.domains import _locf
from tests.enumeration.domains._base import (
    SPEC_EXAMPLE,
    SPEC_PREFIX,
    spec_profile,
    window_days,
)
from tests.enumeration.harness import PackedDomain, artifacts_for, is_nightly


def build_locf_domain() -> PackedDomain:
    """The WINDOW-ALIGNED money family: drift's grids + ledger_drift's
    topology cells share one carry-forward spine; overdraft +
    expected_eod ride the same cells."""
    nightly = is_nightly()
    window = window_days(nightly=nightly)
    anchor_tx, anchor_bal = _locf.anchor_rows(window)
    cells = drift.money_cells(window, nightly=nightly)
    cells += ledger_drift.topology_cells(window)
    return PackedDomain(
        name="locf",
        artifacts=artifacts_for(SPEC_EXAMPLE, prefix=SPEC_PREFIX),
        cells=tuple(cells),
        checks=(
            drift.CHECK, ledger_drift.CHECK, overdraft.CHECK,
            expected_eod.CHECK,
        ),
        anchor_tx=anchor_tx,
        anchor_bal=anchor_bal,
    )


def build_transfer_keyed_domain() -> PackedDomain:
    """The transfer-keyed families in one packed DB (the DS.0 spike's
    combined-DB shape): cardinality + threshold + limit cells are
    row-disjoint by id-prefix construction, and every cross-detector
    touch a cell makes is DECLARED in its expected map (see the
    chain_parent exclusion probes' fan_in entries)."""
    profile = spec_profile()
    cells = (
        xor_group.cells(profile)
        + multi_xor.cells(profile)
        + chain_parent.cells(profile)
        + fan_in.cells(profile)
        + stuck_pending.cells(profile)
        + stuck_unbundled.cells(profile)
        + limit_breach.cells(profile)
    )
    return PackedDomain(
        name="transfer_keyed",
        artifacts=artifacts_for(SPEC_EXAMPLE, prefix=SPEC_PREFIX),
        cells=tuple(cells),
        checks=(
            xor_group.CHECK, multi_xor.CHECK, chain_parent.CHECK,
            fan_in.CHECK, stuck_pending.CHECK, stuck_unbundled.CHECK,
            limit_breach.CHECK,
        ),
    )
