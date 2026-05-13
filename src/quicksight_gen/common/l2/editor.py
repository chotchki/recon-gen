"""Editor primitives — server-owned cascade for the X.4.e editor flow.

Three transforms on the in-memory ``L2Instance``:

- ``mutate_l2(instance, kind, id, fields)`` — replace one entity's
  fields with the operator-supplied values, return a new ``L2Instance``.
  Field-level only (no cross-entity ripple — that's rename's job).
- ``rename_identifier(instance, kind, old, new)`` — rewrite every
  reference to ``old`` across the model. Symmetric to the strict
  validator's reference-resolution pass: where the validator says
  "this Rail's source_role MUST resolve to an Account.role", rename
  rewrites those very fields when an Account.role changes.
- ``delete_l2_entity(instance, kind, id)`` — remove one entity + run
  the validator. A structural break (some other entity still
  referenced the deleted one) raises ``L2ValidationError``; the
  caller (Studio's PUT handler) returns 400 with the validator
  message inline.

All three return a new ``L2Instance`` (the ``L2InstanceCache.replace``
contract from X.4.a.6) — the original is never mutated. The cache +
disk-write pair handle persistence; this module is the pure-Python
transform layer.

Severability: pure Python; no DB, no Starlette. Imports the model +
validator only.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any, Literal, TypeAlias

from quicksight_gen.common.l2.primitives import (
    Account,
    AccountTemplate,
    ChainEntry,
    Identifier,
    L2Instance,
    LimitSchedule,
    Rail,
    TransferTemplate,
    TwoLegRail,
)


EntityKind: TypeAlias = Literal[
    "account",
    "account_template",
    "rail",
    "transfer_template",
    "chain",
    "limit_schedule",
]


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def mutate_l2(
    instance: L2Instance,
    kind: EntityKind,
    entity_id: str,
    fields: Mapping[str, Any],  # typing-smell: ignore[explicit-any]: heterogeneous form-submitted field values; per-entity dataclass fields differ
) -> L2Instance:
    """Replace one entity's fields with new values.

    Args:
        instance: The L2 model to mutate (returns a new copy; original
            untouched).
        kind: Which collection the entity lives in.
        entity_id: The entity's identity key — Account.id, Rail.name,
            TransferTemplate.name, AccountTemplate.role,
            ChainEntry's "<parent>::<child>" composite, or
            LimitSchedule's "<parent_role>::<transfer_type>" composite.
        fields: New field values, applied via ``dataclasses.replace``.
            Keys MUST match the dataclass field names; unknown keys
            raise ``ValueError``.

    Returns:
        A new ``L2Instance`` with the matched entity replaced.

    Raises:
        KeyError: no entity with that ``entity_id`` exists in the
            target collection.
        ValueError: ``fields`` contains keys that aren't dataclass
            fields of the target entity.
    """
    matched, idx, collection = _find_entity(instance, kind, entity_id)
    new_entity = dataclasses.replace(matched, **fields)
    new_collection = collection[:idx] + (new_entity,) + collection[idx + 1:]
    return _replace_collection(instance, kind, new_collection)


def rename_identifier(
    instance: L2Instance,
    kind: EntityKind,
    old: Identifier,
    new: Identifier,
) -> L2Instance:
    """Rename an identifier across every L2 reference.

    Per the SPEC's editor cascade rule: "Rename = auto-rewrite refs.
    Renaming an identifier walks the model and replaces every field
    that references the old value." The reference catalog mirrors the
    strict validator's reference-resolution pass — wherever the
    validator says "this field MUST resolve to ``old``", the rename
    rewrites that field to ``new``.

    Per kind:

    - **account / account_template** (ID = role): walks every
      ``role`` / ``parent_role`` / ``source_role`` / ``destination_role``
      / ``leg_role`` field; rewrites RoleExpression tuples element-wise.
    - **rail** (ID = name): rewrites ``leg_rails`` (TransferTemplate),
      ``bundles_activity`` (Rail), ``parent`` / ``child`` (ChainEntry).
    - **transfer_template** (ID = name): rewrites
      ``bundles_activity`` (Rail), ``parent`` / ``child`` (ChainEntry).
    - **chain / limit_schedule**: have no incoming references — rename
      is a no-op (chains/limit_schedules are leaf consumers).

    The Account.id / AccountTemplate (no .id, addressed by .role) /
    LimitSchedule (composite key) are addressing keys, not reference
    targets inside L2 — renaming Account.id walks the Account itself
    only (rename via ``mutate_l2(..., fields={"id": new})``).

    Returns a new ``L2Instance``; original untouched. Does NOT run
    validation — caller composes ``validate(...)`` if cascade resulted
    in an invalid model (e.g., renaming to a value that collides with
    another entity's identifier).
    """
    if kind in ("chain", "limit_schedule"):
        return instance  # no incoming refs to rewrite

    if kind in ("account", "account_template"):
        return _rename_role(instance, old, new)

    if kind == "rail":
        return _rename_rail(instance, old, new)

    # kind == "transfer_template"
    return _rename_transfer_template(instance, old, new)


def delete_l2_entity(
    instance: L2Instance,
    kind: EntityKind,
    entity_id: str,
) -> L2Instance:
    """Remove one entity. Caller composes ``validate()`` to surface
    structural breaks.

    Per the SPEC: "Structural break = reject, don't auto-cascade."
    Deleting a Rail that some TransferTemplate.leg_rails still
    references leaves the model in a state the strict validator
    rejects; the Studio PUT handler catches ``L2ValidationError`` and
    returns 400 with the message inline.

    Returns:
        A new ``L2Instance`` with the matched entity removed.

    Raises:
        KeyError: no entity with that ``entity_id`` exists.
    """
    _matched, idx, collection = _find_entity(instance, kind, entity_id)
    new_collection = collection[:idx] + collection[idx + 1:]
    return _replace_collection(instance, kind, new_collection)


# ---------------------------------------------------------------------------
# Entity lookup + collection swap
# ---------------------------------------------------------------------------


def _find_entity(
    instance: L2Instance,
    kind: EntityKind,
    entity_id: str,
) -> "tuple[Any, int, tuple[Any, ...]]":  # typing-smell: ignore[explicit-any]: per-kind union; the tuple element type narrows on the kind dispatch
    """Locate ``entity_id`` in the right collection. Returns
    ``(entity, index, collection)``. Raises KeyError on miss.
    """
    if kind == "account":
        for i, a in enumerate(instance.accounts):
            if str(a.id) == entity_id:
                return a, i, instance.accounts
    elif kind == "account_template":
        for i, t in enumerate(instance.account_templates):
            if str(t.role) == entity_id:
                return t, i, instance.account_templates
    elif kind == "rail":
        for i, r in enumerate(instance.rails):
            if str(r.name) == entity_id:
                return r, i, instance.rails
    elif kind == "transfer_template":
        for i, tt in enumerate(instance.transfer_templates):
            if str(tt.name) == entity_id:
                return tt, i, instance.transfer_templates
    elif kind == "chain":
        # Composite key: "<parent>::<child>"
        for i, ch in enumerate(instance.chains):
            if f"{ch.parent}::{ch.child}" == entity_id:
                return ch, i, instance.chains
    elif kind == "limit_schedule":
        # Composite key: "<parent_role>::<transfer_type>"
        for i, ls in enumerate(instance.limit_schedules):
            if f"{ls.parent_role}::{ls.transfer_type}" == entity_id:
                return ls, i, instance.limit_schedules
    raise KeyError(f"{kind} {entity_id!r} not found in instance")


def _replace_collection(
    instance: L2Instance,
    kind: EntityKind,
    new_collection: "tuple[Any, ...]",  # typing-smell: ignore[explicit-any]: per-kind union; dataclasses.replace narrows at the call site
) -> L2Instance:
    """Swap one collection on the L2Instance, return a new copy."""
    field_name = {
        "account": "accounts",
        "account_template": "account_templates",
        "rail": "rails",
        "transfer_template": "transfer_templates",
        "chain": "chains",
        "limit_schedule": "limit_schedules",
    }[kind]
    return dataclasses.replace(instance, **{field_name: new_collection})


# ---------------------------------------------------------------------------
# Per-kind rename walkers
# ---------------------------------------------------------------------------


def _rename_role(
    instance: L2Instance, old: Identifier, new: Identifier,
) -> L2Instance:
    """Rewrite every role-typed reference: Account.role / parent_role,
    AccountTemplate.role / parent_role, Rail's source/destination/leg
    roles, LimitSchedule.parent_role.
    """
    accounts = tuple(_rename_account_roles(a, old, new) for a in instance.accounts)
    account_templates = tuple(
        _rename_account_template_roles(t, old, new)
        for t in instance.account_templates
    )
    rails = tuple(_rename_rail_roles(r, old, new) for r in instance.rails)
    limit_schedules = tuple(
        _rename_limit_schedule_role(ls, old, new) for ls in instance.limit_schedules
    )
    return dataclasses.replace(
        instance,
        accounts=accounts,
        account_templates=account_templates,
        rails=rails,
        limit_schedules=limit_schedules,
    )


def _rename_account_roles(
    a: Account, old: Identifier, new: Identifier,
) -> Account:
    role = new if a.role == old else a.role
    parent_role = new if a.parent_role == old else a.parent_role
    if role is a.role and parent_role is a.parent_role:
        return a
    return dataclasses.replace(a, role=role, parent_role=parent_role)


def _rename_account_template_roles(
    t: AccountTemplate, old: Identifier, new: Identifier,
) -> AccountTemplate:
    role = new if t.role == old else t.role
    parent_role = new if t.parent_role == old else t.parent_role
    if role is t.role and parent_role is t.parent_role:
        return t
    return dataclasses.replace(t, role=role, parent_role=parent_role)


def _rename_rail_roles(
    r: Rail, old: Identifier, new: Identifier,
) -> Rail:
    if isinstance(r, TwoLegRail):
        new_src = _rename_role_expression(r.source_role, old, new)
        new_dst = _rename_role_expression(r.destination_role, old, new)
        if new_src is r.source_role and new_dst is r.destination_role:
            return r
        return dataclasses.replace(
            r, source_role=new_src, destination_role=new_dst,
        )
    # SingleLegRail
    new_leg = _rename_role_expression(r.leg_role, old, new)
    if new_leg is r.leg_role:
        return r
    return dataclasses.replace(r, leg_role=new_leg)


def _rename_role_expression(
    re: tuple[Identifier, ...], old: Identifier, new: Identifier,
) -> tuple[Identifier, ...]:
    rewritten = tuple(new if r == old else r for r in re)
    return rewritten if rewritten != re else re


def _rename_limit_schedule_role(
    ls: LimitSchedule, old: Identifier, new: Identifier,
) -> LimitSchedule:
    if ls.parent_role == old:
        return dataclasses.replace(ls, parent_role=new)
    return ls


def _rename_rail(
    instance: L2Instance, old: Identifier, new: Identifier,
) -> L2Instance:
    """Rewrite every Rail-name reference: TransferTemplate.leg_rails,
    Rail.bundles_activity, ChainEntry.parent / .child. Also bumps the
    Rail's own .name (the rename's anchor target).
    """
    rails = tuple(
        dataclasses.replace(r, name=new) if r.name == old
        else _rename_rail_bundles(r, old, new)
        for r in instance.rails
    )
    transfer_templates = tuple(
        _rename_template_leg_rails(tt, old, new)
        for tt in instance.transfer_templates
    )
    chains = tuple(_rename_chain_endpoint(c, old, new) for c in instance.chains)
    return dataclasses.replace(
        instance,
        rails=rails,
        transfer_templates=transfer_templates,
        chains=chains,
    )


def _rename_rail_bundles(
    r: Rail, old: Identifier, new: Identifier,
) -> Rail:
    rewritten = tuple(new if b == old else b for b in r.bundles_activity)
    if rewritten == r.bundles_activity:
        return r
    return dataclasses.replace(r, bundles_activity=rewritten)


def _rename_template_leg_rails(
    tt: TransferTemplate, old: Identifier, new: Identifier,
) -> TransferTemplate:
    rewritten = tuple(new if r == old else r for r in tt.leg_rails)
    if rewritten == tt.leg_rails:
        return tt
    return dataclasses.replace(tt, leg_rails=rewritten)


def _rename_chain_endpoint(
    c: ChainEntry, old: Identifier, new: Identifier,
) -> ChainEntry:
    parent = new if c.parent == old else c.parent
    child = new if c.child == old else c.child
    if parent is c.parent and child is c.child:
        return c
    return dataclasses.replace(c, parent=parent, child=child)


def _rename_transfer_template(
    instance: L2Instance, old: Identifier, new: Identifier,
) -> L2Instance:
    """Rewrite every TransferTemplate-name reference: Rail.bundles_activity,
    ChainEntry.parent / .child. Plus the template's own .name.
    """
    transfer_templates = tuple(
        dataclasses.replace(tt, name=new) if tt.name == old else tt
        for tt in instance.transfer_templates
    )
    rails = tuple(
        _rename_rail_bundles(r, old, new) for r in instance.rails
    )
    chains = tuple(_rename_chain_endpoint(c, old, new) for c in instance.chains)
    return dataclasses.replace(
        instance,
        transfer_templates=transfer_templates,
        rails=rails,
        chains=chains,
    )
