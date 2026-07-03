"""DS.3.5 — per-invariant exhaustive enumeration domains.

One module per detector; this registry maps every gate target onto
the packed domain(s) that answer for it. Three shared packed DBs plus
the fan-in variant:

- ``locf`` — the WINDOW-ALIGNED money family (drift + ledger_drift +
  overdraft + expected_eod share one carry-forward spine).
- ``transfer_keyed`` — cardinality + threshold + limit_breach cells
  (disjoint id prefixes; the DS.0-spike-proven contract).
- ``fan_in_variant`` — a spec_example variant instance declaring a
  fan-in child with UNSET expected_parent_count (the orphan branch is
  unrepresentable on stock spec_example).
- ``money_trail`` — the derivation family (own DB: its law is a
  global edge walk, so edge-mintable rows from other families are
  excluded structurally rather than by convention).

``l1_exceptions`` is the 13th target — a union check over the packed
DBs (see its module). ``anomaly`` is deliberately absent — see
``anomaly.py`` for the written rationale.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Final

from pathlib import Path

from tests.enumeration.domains import (
    _assembly,
    cadence_gap,
    fan_in,
    money_trail,
)
from tests.enumeration.domains._base import SPEC_EXAMPLE
from tests.enumeration.harness import PackedDomain

# Lazy builders — construction deferred to first use so collection
# stays cheap and each xdist worker only builds what its items need.
DOMAIN_BUILDERS: Final[dict[str, Callable[[], PackedDomain]]] = {
    "locf": _assembly.build_locf_domain,
    "transfer_keyed": _assembly.build_transfer_keyed_domain,
    "fan_in_variant": fan_in.build_variant_domain,
    "money_trail": money_trail.build_money_trail_domain,
    "cadence_gap": cadence_gap.build_cadence_gap_domain,
}

# detector -> packed domains that answer for it (the gate asserts
# engine == residual on every listed domain).
DETECTOR_DOMAINS: Final[dict[str, tuple[str, ...]]] = {
    "drift": ("locf",),
    "ledger_drift": ("locf",),
    "overdraft": ("locf",),
    "expected_eod": ("locf",),
    "limit_breach": ("transfer_keyed",),
    "stuck_pending": ("transfer_keyed",),
    "stuck_unbundled": ("transfer_keyed",),
    "chain_parent": ("transfer_keyed",),
    "xor_group": ("transfer_keyed",),
    "fan_in": ("transfer_keyed", "fan_in_variant"),
    "multi_xor": ("transfer_keyed",),
    "money_trail": ("money_trail",),
    "balance_cadence_gap": ("cadence_gap",),
}

# The l1_exceptions union check runs on the packed DBs whose cells
# actually reach its source branches.
L1_EXCEPTIONS_DOMAINS: Final[tuple[str, ...]] = ("locf", "transfer_keyed")


# DS.6 per-dialect lane — the L2 instance each domain emits from, so the
# replay can re-emit the SAME schema at PG / Oracle (the DuckDB builders
# above bake Dialect.DUCKDB into their artifacts; the replay needs the
# path to re-emit at the cfg dialect). Variant domains carry their own
# generated yaml.
DOMAIN_L2_PATHS: Final[dict[str, Callable[[], Path]]] = {
    "locf": lambda: SPEC_EXAMPLE,
    "transfer_keyed": lambda: SPEC_EXAMPLE,
    "fan_in_variant": fan_in.variant_l2_path,
    "money_trail": lambda: SPEC_EXAMPLE,
    "cadence_gap": cadence_gap.variant_l2_path,
}
