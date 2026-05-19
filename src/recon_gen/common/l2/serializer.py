"""``serialize_l2(instance) → str`` — round-trip-stable YAML emit (X.4.d.3).

Inverse of ``loader.py::load_instance``. Every field in the
``L2Instance`` model is written back to YAML in a shape the loader
accepts; the contract is *model* equivalence under round-trip
(load → serialize → load → identical), NOT byte-equivalence to the
original YAML (per the SPEC: "drops freeform `# comments`, preserves
`description:`").

The Studio editor (X.4.e+) calls this every PUT, so the cost matters
but only weakly — Studio is one user iterating, not a hot path; the
heaviest fixture (sasquatch_pr) emits in low-ms.

Severability: pure Python; no DB, no async, no Starlette. Imports
only the model + theme + persona dataclasses.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta
from decimal import Decimal
from typing import Any

import yaml

from recon_gen.common.l2.primitives import (
    Account,
    AccountTemplate,
    Chain,
    L2Instance,
    LimitSchedule,
    Rail,
    RoleExpression,
    SingleLegRail,
    TransferTemplate,
    TwoLegRail,
)
from recon_gen.common.l2.theme import ThemePreset
from recon_gen.common.persona import DemoPersona, GLAccount


def serialize_l2(instance: L2Instance) -> str:
    """Serialize an ``L2Instance`` back to YAML text.

    Round-trip contract: ``load_instance(write(serialize_l2(x)))`` is
    field-equal to ``x`` (every primitive's dataclass __eq__ holds).
    Original YAML's freeform comments are dropped (per SPEC); the
    loader's required field set is honored, optional fields with their
    declared default are omitted to keep emitted YAML compact.

    Field order in the emitted YAML mirrors the loader's read order so
    a `git diff` against the original is a clean per-field move when
    fields shift, not a wholesale re-sort.
    """
    # Z.C — the legacy ``instance:`` field is gone; the deployment
    # identifier lives on cfg.yaml (``deployment_name`` /
    # ``db_table_prefix``), not on the L2 yaml.
    out: dict[str, Any] = {}  # typing-smell: ignore[explicit-any]: heterogeneous YAML payload — every value is something safe_dump can write
    if instance.description is not None:
        out["description"] = instance.description
    if instance.accounts:
        out["accounts"] = [_dump_account(a) for a in instance.accounts]
    if instance.account_templates:
        out["account_templates"] = [
            _dump_account_template(t) for t in instance.account_templates
        ]
    if instance.rails:
        out["rails"] = [_dump_rail(r) for r in instance.rails]
    if instance.transfer_templates:
        out["transfer_templates"] = [
            _dump_transfer_template(t) for t in instance.transfer_templates
        ]
    if instance.chains:
        out["chains"] = [_dump_chain(c) for c in instance.chains]
    if instance.limit_schedules:
        out["limit_schedules"] = [
            _dump_limit_schedule(ls) for ls in instance.limit_schedules
        ]
    if instance.role_business_day_offsets:
        out["role_business_day_offsets"] = dict(instance.role_business_day_offsets)
    if instance.theme is not None:
        out["theme"] = _dump_theme(instance.theme)
    if instance.persona is not None:
        out["persona"] = _dump_persona(instance.persona)

    return yaml.safe_dump(
        out,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=10_000,  # avoid line-wrapping long descriptions / hex-list literals
    )


# ---------------------------------------------------------------------------
# Per-entity dumpers — symmetric with loader._load_X
# ---------------------------------------------------------------------------


def _dump_account(a: Account) -> dict[str, Any]:  # typing-smell: ignore[explicit-any]: per-field heterogeneous YAML row
    out: dict[str, Any] = {"id": str(a.id), "scope": a.scope}  # typing-smell: ignore[explicit-any]: per-field heterogeneous YAML row
    if a.name is not None:
        out["name"] = str(a.name)
    if a.role is not None:
        out["role"] = str(a.role)
    if a.parent_role is not None:
        out["parent_role"] = str(a.parent_role)
    if a.expected_eod_balance is not None:
        out["expected_eod_balance"] = _dump_money(a.expected_eod_balance)
    if a.description is not None:
        out["description"] = a.description
    return out


def _dump_account_template(t: AccountTemplate) -> dict[str, Any]:  # typing-smell: ignore[explicit-any]: per-field heterogeneous YAML row
    out: dict[str, Any] = {"role": str(t.role), "scope": t.scope}  # typing-smell: ignore[explicit-any]: per-field heterogeneous YAML row
    if t.parent_role is not None:
        out["parent_role"] = str(t.parent_role)
    if t.expected_eod_balance is not None:
        out["expected_eod_balance"] = _dump_money(t.expected_eod_balance)
    if t.description is not None:
        out["description"] = t.description
    if t.instance_id_template is not None:
        out["instance_id_template"] = t.instance_id_template
    if t.instance_name_template is not None:
        out["instance_name_template"] = t.instance_name_template
    return out


def _dump_rail(r: Rail) -> dict[str, Any]:  # typing-smell: ignore[explicit-any]: per-field heterogeneous YAML row
    if isinstance(r, TwoLegRail):
        return _dump_two_leg_rail(r)
    return _dump_single_leg_rail(r)


def _dump_two_leg_rail(r: TwoLegRail) -> dict[str, Any]:  # typing-smell: ignore[explicit-any]: per-field heterogeneous YAML row
    out: dict[str, Any] = {  # typing-smell: ignore[explicit-any]: per-field heterogeneous YAML row
        "name": str(r.name),
        "source_role": _dump_role_expression(r.source_role),
        "destination_role": _dump_role_expression(r.destination_role),
    }
    if r.expected_net is not None:
        out["expected_net"] = _dump_money(r.expected_net)
    if r.origin is not None:
        out["origin"] = r.origin
    if r.source_origin is not None:
        out["source_origin"] = r.source_origin
    if r.destination_origin is not None:
        out["destination_origin"] = r.destination_origin
    if r.metadata_keys:
        out["metadata_keys"] = [str(k) for k in r.metadata_keys]
    if r.posted_requirements:
        out["posted_requirements"] = [str(k) for k in r.posted_requirements]
    if r.max_pending_age is not None:
        out["max_pending_age"] = _dump_duration(r.max_pending_age)
    if r.max_unbundled_age is not None:
        out["max_unbundled_age"] = _dump_duration(r.max_unbundled_age)
    if r.aggregating:
        out["aggregating"] = True
    if r.bundles_activity:
        out["bundles_activity"] = [str(b) for b in r.bundles_activity]
    if r.cadence is not None:
        out["cadence"] = r.cadence
    if r.description is not None:
        out["description"] = r.description
    if r.metadata_value_examples:
        out["metadata_value_examples"] = {
            str(k): list(vs) for k, vs in r.metadata_value_examples
        }
    return out


def _dump_single_leg_rail(r: SingleLegRail) -> dict[str, Any]:  # typing-smell: ignore[explicit-any]: per-field heterogeneous YAML row
    out: dict[str, Any] = {  # typing-smell: ignore[explicit-any]: per-field heterogeneous YAML row
        "name": str(r.name),
        "leg_role": _dump_role_expression(r.leg_role),
        "leg_direction": r.leg_direction,
    }
    if r.origin is not None:
        out["origin"] = r.origin
    if r.metadata_keys:
        out["metadata_keys"] = [str(k) for k in r.metadata_keys]
    if r.posted_requirements:
        out["posted_requirements"] = [str(k) for k in r.posted_requirements]
    if r.max_pending_age is not None:
        out["max_pending_age"] = _dump_duration(r.max_pending_age)
    if r.max_unbundled_age is not None:
        out["max_unbundled_age"] = _dump_duration(r.max_unbundled_age)
    if r.aggregating:
        out["aggregating"] = True
    if r.bundles_activity:
        out["bundles_activity"] = [str(b) for b in r.bundles_activity]
    if r.cadence is not None:
        out["cadence"] = r.cadence
    if r.description is not None:
        out["description"] = r.description
    if r.metadata_value_examples:
        out["metadata_value_examples"] = {
            str(k): list(vs) for k, vs in r.metadata_value_examples
        }
    return out


def _dump_transfer_template(t: TransferTemplate) -> dict[str, Any]:  # typing-smell: ignore[explicit-any]: per-field heterogeneous YAML row
    out: dict[str, Any] = {  # typing-smell: ignore[explicit-any]: per-field heterogeneous YAML row
        "name": str(t.name),
        "expected_net": _dump_money(t.expected_net),
        "transfer_key": [str(k) for k in t.transfer_key],
        "completion": t.completion,
        "leg_rails": [str(r) for r in t.leg_rails],
    }
    # AB.3: emit `leg_rail_xor_groups` only when non-empty so every
    # pre-AB.3 template round-trips byte-equivalent (mirrors AB.1's
    # `direction` non-default omit pattern).
    if t.leg_rail_xor_groups:
        out["leg_rail_xor_groups"] = [
            [str(r) for r in group] for group in t.leg_rail_xor_groups
        ]
    if t.description is not None:
        out["description"] = t.description
    return out


def _dump_chain(c: Chain) -> dict[str, Any]:  # typing-smell: ignore[explicit-any]: per-field heterogeneous YAML row
    out: dict[str, Any] = {  # typing-smell: ignore[explicit-any]: per-field heterogeneous YAML row
        "parent": str(c.parent),
        "children": [str(child) for child in c.children],
    }
    # AB.4: emit fan_in + expected_parent_count only when non-default
    # so every pre-AB.4 chain round-trips byte-equivalent (mirrors
    # AB.1's direction non-default omit pattern).
    if c.fan_in:
        out["fan_in"] = True
    if c.expected_parent_count is not None:
        out["expected_parent_count"] = c.expected_parent_count
    if c.description is not None:
        out["description"] = c.description
    return out


def _dump_limit_schedule(ls: LimitSchedule) -> dict[str, Any]:  # typing-smell: ignore[explicit-any]: per-field heterogeneous YAML row
    out: dict[str, Any] = {  # typing-smell: ignore[explicit-any]: per-field heterogeneous YAML row
        "parent_role": str(ls.parent_role),
        "rail": str(ls.rail),
        "cap": _dump_money(ls.cap),
    }
    # AB.1 (2026-05-19): only emit direction when non-default — keeps every
    # pre-AB.1 YAML byte-equivalent through a load+dump round-trip.
    if ls.direction != "Outbound":
        out["direction"] = ls.direction
    if ls.description is not None:
        out["description"] = ls.description
    return out


def _dump_theme(theme: ThemePreset) -> dict[str, Any]:  # typing-smell: ignore[explicit-any]: per-field heterogeneous YAML row
    """Serialize a ThemePreset back to YAML.

    Theme is a flat dataclass — ``asdict`` is sufficient. We preserve
    field order by walking the dataclass fields explicitly so the
    emitted YAML matches the loader's read order.
    """
    return asdict(theme)


def _dump_persona(persona: DemoPersona) -> dict[str, Any]:  # typing-smell: ignore[explicit-any]: per-field heterogeneous YAML row
    """Serialize a DemoPersona back to YAML.

    The persona's tuple-of-tuples for gl_accounts becomes a list of
    ``{role, label}`` dicts to match the loader's expected shape.
    """
    out: dict[str, Any] = {}  # typing-smell: ignore[explicit-any]: per-field heterogeneous YAML row
    if persona.institution:
        out["institution"] = list(persona.institution)
    if persona.stakeholders:
        out["stakeholders"] = list(persona.stakeholders)
    if persona.gl_accounts:
        out["gl_accounts"] = [_dump_gl_account(g) for g in persona.gl_accounts]
    if persona.merchants:
        out["merchants"] = list(persona.merchants)
    if persona.flavor:
        out["flavor"] = list(persona.flavor)
    return out


def _dump_gl_account(g: GLAccount) -> dict[str, Any]:  # typing-smell: ignore[explicit-any]: per-field heterogeneous YAML row
    return asdict(g)


# ---------------------------------------------------------------------------
# Scalar helpers — Decimal, timedelta, RoleExpression
# ---------------------------------------------------------------------------


def _dump_money(value: Decimal) -> int | float:
    """Decimal → numeric YAML scalar.

    Uses ``int`` when the value is integer-valued, ``float`` otherwise.
    The loader accepts both; integer emit keeps small balances readable
    (``0`` instead of ``0.0``).
    """
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def _dump_duration(td: timedelta) -> str:
    """timedelta → ISO 8601 duration literal (the format the loader reads).

    Emits ``P<n>D[T<h>H<m>M<s>S]`` — only days / hours / minutes /
    seconds, matching the loader's _ISO_DURATION_RE grammar. Skips
    sub-second precision (the L2 model never declares it; aging windows
    are coarse).
    """
    total = int(td.total_seconds())
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    out = "P"
    if days:
        out += f"{days}D"
    if hours or minutes or seconds:
        out += "T"
        if hours:
            out += f"{hours}H"
        if minutes:
            out += f"{minutes}M"
        if seconds:
            out += f"{seconds}S"
    if out == "P":
        # Defensive: a zero-duration shouldn't exist (loader rejects empty)
        # but if it does, emit ``PT0S`` so the loader can re-read it.
        return "PT0S"
    return out


def _dump_role_expression(re: RoleExpression) -> str | list[str]:
    """Tuple[Identifier, ...] → single string (1-tuple) or list (many).

    Mirrors the loader's `_load_role_expression` normalization — single-
    role YAML lands as a 1-tuple in the model; the inverse drops back to
    a plain string so the emitted YAML is the canonical form.
    """
    if len(re) == 1:
        return str(re[0])
    return [str(r) for r in re]
