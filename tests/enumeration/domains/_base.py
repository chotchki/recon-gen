"""Shared domain scaffolding: the BoundaryProfile + window constants.

The BoundaryProfile is the DS.0 attack-finding fix made structural: a
SQL-text lint is VACUOUS for the four config-cap invariants
(limit_breach / stuck_pending / stuck_unbundled / fan_in) because
their comparison constants are CONFIG DATA resolved through
``v_config_*`` views, not SQL literals. Every threshold domain derives
its value grid from the L2-RESOLVED instance via this profile, and the
gate's coverage lint asserts each resolved cap's {c-1, c, c+1}
neighborhood actually appears in the domain that claims to cover it.
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Final

from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.primitives import SCOPE_INTERNAL, L2Instance

SPEC_EXAMPLE: Final = (
    Path(__file__).resolve().parent.parent.parent / "l2" / "spec_example.yaml"
)
SPEC_PREFIX: Final = "spec_example"

# The 2-day CI enumeration window (the full tier uses 3 — the domain
# builders read the tier). Anchored at the repo's canonical semantic
# anchor date.
WINDOW_START: Final = dt.date(2030, 1, 1)


def window_days(*, wide: bool) -> tuple[dt.date, ...]:
    n = 3 if wide else 2
    return tuple(WINDOW_START + dt.timedelta(days=i) for i in range(n))


@dataclass(frozen=True, slots=True)
class BoundaryProfile:
    """Every L2-RESOLVED comparison value the detectors read from
    config, in the residuals' native units (cents / whole seconds /
    counts). Derived from the loaded instance — never from SQL text."""

    #: (parent_role, rail, direction) -> cap in CENTS (the x100
    #: dollars->cents shift applied here, mirroring schema.py's
    #: ``ls.cap * 100`` join projection).
    limit_caps_cents: Mapping[tuple[str, str, str], int]
    #: rail -> max_pending_age in whole seconds.
    pending_age_caps: Mapping[str, int]
    #: rail -> max_unbundled_age in whole seconds.
    unbundled_age_caps: Mapping[str, int]
    #: (chain parent name, child name) -> expected_parent_count
    #: (None == declared fan_in with unset expectation: the >=2 rule).
    fan_in_expected: Mapping[tuple[str, str], int | None]
    #: template name -> xor groups (each a frozenset of member rails).
    xor_groups: Mapping[str, tuple[frozenset[str], ...]]
    #: multi-xor chain parent name -> declared sibling names
    #: (non-fan_in children of parents with >=2 non-fan_in children).
    multi_xor_children: Mapping[str, frozenset[str]]
    #: fan_in child template names — excluded from chain_parent scope.
    chain_parent_excluded: frozenset[str]
    #: DS.5.1 — declared balance cadences, INTERNAL entities only,
    #: same precedence shape the emitter's CASE arms use (CL.0 Lock 3:
    #: singleton account_id wins over template role; undeclared falls
    #: through to sparse). Keys mirror ``expected_cadence_gaps``'s
    #: params.
    singleton_cadences: Mapping[str, str]
    role_cadences: Mapping[str, str]

    @classmethod
    def from_instance(cls, instance: L2Instance) -> "BoundaryProfile":
        limit_caps: dict[tuple[str, str, str], int] = {}
        for sched in instance.limit_schedules:
            limit_caps[(str(sched.parent_role), str(sched.rail), str(sched.direction))] = (
                int(sched.cap * 100)
            )
        pending: dict[str, int] = {}
        unbundled: dict[str, int] = {}
        for rail in instance.rails:
            if rail.max_pending_age is not None:
                pending[str(rail.name)] = int(
                    rail.max_pending_age.total_seconds(),
                )
            if rail.max_unbundled_age is not None:
                unbundled[str(rail.name)] = int(
                    rail.max_unbundled_age.total_seconds(),
                )
        fan_in: dict[tuple[str, str], int | None] = {}
        excluded: set[str] = set()
        non_fan_in_children: dict[str, list[str]] = {}
        for chain in instance.chains:
            for child in chain.children:
                if child.fan_in:
                    fan_in[(str(chain.parent), str(child.name))] = (
                        child.expected_parent_count
                    )
                    excluded.add(str(child.name))
                else:
                    non_fan_in_children.setdefault(
                        str(chain.parent), [],
                    ).append(str(child.name))
        multi_xor = {
            parent: frozenset(children)
            for parent, children in non_fan_in_children.items()
            if len(children) >= 2
        }
        xor: dict[str, tuple[frozenset[str], ...]] = {}
        for template in instance.transfer_templates:
            if template.leg_rail_xor_groups:
                xor[str(template.name)] = tuple(
                    frozenset(str(m) for m in group)
                    for group in template.leg_rail_xor_groups
                )
        singleton_cadences: dict[str, str] = {}
        for account in instance.accounts:
            if (
                account.balance_cadence is not None
                and account.scope == SCOPE_INTERNAL
            ):
                singleton_cadences[str(account.id)] = str(
                    account.balance_cadence,
                )
        role_cadences: dict[str, str] = {}
        for tmpl in instance.account_templates:
            if tmpl.balance_cadence is not None:
                role_cadences[str(tmpl.role)] = str(tmpl.balance_cadence)
        return cls(
            limit_caps_cents=limit_caps,
            pending_age_caps=pending,
            unbundled_age_caps=unbundled,
            fan_in_expected=fan_in,
            xor_groups=xor,
            multi_xor_children=multi_xor,
            chain_parent_excluded=frozenset(excluded),
            singleton_cadences=singleton_cadences,
            role_cadences=role_cadences,
        )


_PROFILE_CACHE: dict[str, BoundaryProfile] = {}


def profile_for(l2_path: Path) -> BoundaryProfile:
    key = str(l2_path.resolve())
    cached = _PROFILE_CACHE.get(key)
    if cached is None:
        cached = BoundaryProfile.from_instance(load_instance(l2_path))
        _PROFILE_CACHE[key] = cached
    return cached


def spec_profile() -> BoundaryProfile:
    return profile_for(SPEC_EXAMPLE)


def as_date(value: object) -> dt.date:
    """Engine rows carry business days as TIMESTAMP; residual keys use
    ``date``. One normalization site."""
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    raise TypeError(f"not a date-like value: {value!r}")


def as_int(value: object) -> int:
    """Engine numeric columns land as int (BIGINT) or occasionally a
    float/Decimal wrapper (EXTRACT results). Normalize to exact int;
    a non-integral value is a reader bug, not a rounding case."""
    if isinstance(value, bool):
        raise TypeError(f"bool is not an engine numeric: {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, Decimal) and value == int(value):
        return int(value)
    raise TypeError(f"not an integral engine value: {value!r}")


def as_str(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"expected str engine value, got {value!r}")
    return value


def as_opt_str(value: object) -> str | None:
    if value is None:
        return None
    return as_str(value)
