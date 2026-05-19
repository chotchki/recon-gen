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

from recon_gen.common.l2.primitives import (
    Account,
    AccountTemplate,
    Chain,
    Identifier,
    L2Instance,
    LimitSchedule,
    Money,
    Name,
    Rail,
    RailName,
    SingleLegRail,
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
    # X.4.f.12 — singletons (one per L2 instance, not a list).
    # Routes mount at ``/l2_shape/<kind>/`` (no entity_id); each
    # renders a single edit form. v1 ships as a yaml_block escape
    # hatch — operator edits the entire ``theme:`` / ``persona:`` YAML
    # subtree as text. Per-field color pickers / nested GLAccount
    # editor are a polish follow-on.
    "theme",
    "persona",
]


# X.4.f.12 — kinds that exist as a single optional attribute on
# L2Instance rather than a tuple. Routes / handlers branch on this
# to skip list view, create page, delete, and per-id addressing.
SINGLETON_KINDS: frozenset[EntityKind] = frozenset({"theme", "persona"})


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
            Chain's "<parent>::<sorted-children-csv>" composite, or
            LimitSchedule's "<parent_role>::<rail>" composite.
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
      ``bundles_activity`` (Rail), ``parent`` / ``children`` (Chain).
    - **transfer_template** (ID = name): rewrites
      ``bundles_activity`` (Rail), ``parent`` / ``children`` (Chain).
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


def create_l2_entity(
    instance: L2Instance,
    kind: EntityKind,
    fields: Mapping[str, Any],  # typing-smell: ignore[explicit-any]: heterogeneous form-submitted field values; per-entity dataclass fields differ
) -> L2Instance:
    """Append a new entity to the kind's collection.

    Builds the entity from ``fields`` (already coerced to dataclass-
    field types by the caller). Required-but-missing fields raise
    ``ValueError`` from the dataclass constructor; ID collisions
    raise ``ValueError`` here (we'd rather fail loud at construction
    than let a duplicate slip into the collection and have the
    validator's reference-resolution surface it as a confusing
    indirect error).

    Returns a new ``L2Instance``. The caller composes ``validate(...)``
    afterward to catch L2-graph break (e.g., a Rail referencing roles
    that don't exist yet).
    """
    if kind == "account":
        new_id = fields.get("id")
        if not new_id:
            raise ValueError("Account.id is required")
        if any(str(a.id) == str(new_id) for a in instance.accounts):
            raise ValueError(f"Account id {new_id!r} already exists")
        new_acc = Account(
            id=Identifier(str(new_id)),
            scope=fields.get("scope") or "internal",
            name=Name(str(fields["name"])) if fields.get("name") else None,
            role=Identifier(str(fields["role"])) if fields.get("role") else None,
            parent_role=(
                Identifier(str(fields["parent_role"]))
                if fields.get("parent_role") else None
            ),
            expected_eod_balance=fields.get("expected_eod_balance"),
            description=fields.get("description"),
        )
        return dataclasses.replace(
            instance, accounts=(*instance.accounts, new_acc),
        )
    if kind == "account_template":
        new_role = fields.get("role")
        if not new_role:
            raise ValueError("AccountTemplate.role is required")
        if any(
            str(t.role) == str(new_role) for t in instance.account_templates
        ):
            raise ValueError(
                f"AccountTemplate role {new_role!r} already exists",
            )
        new_t = AccountTemplate(
            role=Identifier(str(new_role)),
            scope=fields.get("scope") or "internal",
            parent_role=(
                Identifier(str(fields["parent_role"]))
                if fields.get("parent_role") else None
            ),
            expected_eod_balance=fields.get("expected_eod_balance"),
            description=fields.get("description"),
            instance_id_template=fields.get("instance_id_template"),
            instance_name_template=fields.get("instance_name_template"),
        )
        return dataclasses.replace(
            instance, account_templates=(*instance.account_templates, new_t),
        )
    if kind == "rail":
        new_name = fields.get("name")
        if not new_name:
            raise ValueError("Rail.name is required")
        if any(str(r.name) == str(new_name) for r in instance.rails):
            raise ValueError(f"Rail name {new_name!r} already exists")
        # X.4.f.11.5 — subtype dispatch. The studio editor's create
        # page is 2-step: a picker page routes the operator to
        # ``?subtype=two_leg|single_leg``, the form filters to that
        # subtype's FieldSpecs, and the hidden ``subtype`` form input
        # arrives here as a fields key. Construct the right
        # discriminated-union arm. ``aggregating`` is shared; the
        # form coerces "true"/"false" to bool.
        subtype = fields.get("subtype")
        aggregating = bool(fields.get("aggregating") or False)
        new_r: Rail
        # X.4.f.11.6/.7/.9 — coerce already-typed Tier-2 fields through.
        # metadata_keys / posted_requirements / bundles_activity arrive
        # as tuples (textarea-coerce / multi_select). aging Durations
        # arrive as datetime.timedelta (loader's _load_duration).
        metadata_keys_v = fields.get("metadata_keys") or ()
        posted_requirements_v = fields.get("posted_requirements") or ()
        bundles_activity_v = fields.get("bundles_activity") or ()
        max_pending_age_v = fields.get("max_pending_age")
        max_unbundled_age_v = fields.get("max_unbundled_age")
        metadata_value_examples_v = fields.get("metadata_value_examples") or ()
        if subtype == "single_leg":
            leg_role_raw: object = fields.get("leg_role") or ()
            leg_role: tuple[Identifier, ...] = (
                tuple(
                    Identifier(str(x))  # pyright: ignore[reportUnknownArgumentType]  # WHY: form-data tuple elements are untyped Any per Mapping[str, Any] contract; str() is the safe coerce boundary
                    for x in leg_role_raw  # pyright: ignore[reportUnknownVariableType]  # WHY: same untyped-Any per-element issue
                )
                if isinstance(leg_role_raw, (list, tuple))
                else ()
            )
            leg_direction = fields.get("leg_direction")
            if not leg_direction:
                raise ValueError(
                    "SingleLegRail.leg_direction is required",
                )
            new_r = SingleLegRail(
                name=Identifier(str(new_name)),
                metadata_keys=metadata_keys_v,  # pyright: ignore[reportArgumentType]  # WHY: form-typed tuple[Identifier, ...] from coerce path
                leg_role=leg_role,
                leg_direction=leg_direction,  # pyright: ignore[reportArgumentType]  # WHY: form-coerced str; LegDirection is Literal["Debit","Credit","Variable"] — value validated by FieldSpec options + the enclosing validator
                origin=(
                    str(fields["origin"]) if fields.get("origin") else None
                ),
                posted_requirements=posted_requirements_v,  # pyright: ignore[reportArgumentType]  # WHY: form-typed tuple[Identifier, ...]
                max_pending_age=max_pending_age_v,  # pyright: ignore[reportArgumentType]  # WHY: form-coerced via _load_duration → timedelta or None
                max_unbundled_age=max_unbundled_age_v,  # pyright: ignore[reportArgumentType]  # WHY: form-coerced via _load_duration to timedelta or None
                aggregating=aggregating,
                bundles_activity=bundles_activity_v,  # pyright: ignore[reportArgumentType]  # WHY: form-typed tuple[Identifier, ...]
                description=fields.get("description"),
                metadata_value_examples=metadata_value_examples_v,  # pyright: ignore[reportArgumentType]  # WHY: form-typed nested tuple from yaml_block coerce
            )
        else:
            # Default to two_leg when subtype is missing — caller is
            # expected to set it for new rail creates, but keeping the
            # default lets the legacy code path (no subtype provided)
            # behave identically: an empty TwoLegRail validator rejects.
            src_raw: object = fields.get("source_role") or ()
            dst_raw: object = fields.get("destination_role") or ()
            source_role: tuple[Identifier, ...] = (
                tuple(
                    Identifier(str(x))  # pyright: ignore[reportUnknownArgumentType]  # WHY: form-data tuple elements are untyped Any per Mapping[str, Any] contract
                    for x in src_raw  # pyright: ignore[reportUnknownVariableType]  # WHY: form-data tuple elements are untyped Any per contract
                )
                if isinstance(src_raw, (list, tuple))
                else ()
            )
            destination_role: tuple[Identifier, ...] = (
                tuple(
                    Identifier(str(x))  # pyright: ignore[reportUnknownArgumentType]  # WHY: form-data tuple elements are untyped Any per Mapping[str, Any] contract
                    for x in dst_raw  # pyright: ignore[reportUnknownVariableType]  # WHY: form-data tuple elements are untyped Any per contract
                )
                if isinstance(dst_raw, (list, tuple))
                else ()
            )
            new_r = TwoLegRail(
                name=Identifier(str(new_name)),
                metadata_keys=metadata_keys_v,  # pyright: ignore[reportArgumentType]  # WHY: form-typed tuple[Identifier, ...] from coerce path
                source_role=source_role,
                destination_role=destination_role,
                origin=(
                    str(fields["origin"]) if fields.get("origin") else None
                ),
                source_origin=(
                    str(fields["source_origin"])
                    if fields.get("source_origin") else None
                ),
                destination_origin=(
                    str(fields["destination_origin"])
                    if fields.get("destination_origin") else None
                ),
                expected_net=fields.get("expected_net"),  # pyright: ignore[reportArgumentType]  # WHY: form-coerced Decimal via money kind, or None
                posted_requirements=posted_requirements_v,  # pyright: ignore[reportArgumentType]  # WHY: form-typed tuple[Identifier, ...]
                max_pending_age=max_pending_age_v,  # pyright: ignore[reportArgumentType]  # WHY: form-coerced via _load_duration → timedelta or None
                max_unbundled_age=max_unbundled_age_v,  # pyright: ignore[reportArgumentType]  # WHY: form-coerced via _load_duration to timedelta or None
                aggregating=aggregating,
                bundles_activity=bundles_activity_v,  # pyright: ignore[reportArgumentType]  # WHY: form-typed tuple[Identifier, ...]
                description=fields.get("description"),
                metadata_value_examples=metadata_value_examples_v,  # pyright: ignore[reportArgumentType]  # WHY: form-typed nested tuple from yaml_block coerce
            )
        return dataclasses.replace(instance, rails=(*instance.rails, new_r))
    if kind == "transfer_template":
        new_name = fields.get("name")
        if not new_name:
            raise ValueError("TransferTemplate.name is required")
        if any(
            str(t.name) == str(new_name)
            for t in instance.transfer_templates
        ):
            raise ValueError(
                f"TransferTemplate name {new_name!r} already exists",
            )
        if not fields.get("completion"):
            raise ValueError("TransferTemplate.completion is required")
        if fields.get("expected_net") is None:
            raise ValueError("TransferTemplate.expected_net is required")
        # leg_rails comes from the multi_select form field as a tuple
        # of Identifiers. Empty tuple is allowed at construction here
        # but the caller's validate() rejects it (TransferTemplate must
        # have at least one leg_rail) — surfaces inline on the create
        # page as a 400 + the operator's selection preserved.
        leg_rails_raw: object = fields.get("leg_rails") or ()
        if isinstance(leg_rails_raw, (list, tuple)):
            leg_rails: tuple[Identifier, ...] = tuple(
                Identifier(str(r)) for r in leg_rails_raw  # pyright: ignore[reportUnknownVariableType,reportUnknownArgumentType]  # WHY: form-data values arrive as untyped strings; per-element coercion is the boundary
            )
        else:
            leg_rails = (Identifier(str(leg_rails_raw)),)
        # AB.3.7 — leg_rail_xor_groups arrives from
        # ``multi_select_groups`` as ``tuple[tuple[Identifier, ...], ...]``.
        # Defensive coerce: form-data path normalizes to Identifier
        # before this point, but accept loose nested lists too for
        # API-style POSTs / future call sites.
        xor_raw: object = fields.get("leg_rail_xor_groups") or ()
        if isinstance(xor_raw, (list, tuple)):
            leg_rail_xor_groups: tuple[tuple[Identifier, ...], ...] = tuple(
                tuple(
                    Identifier(str(r))  # pyright: ignore[reportUnknownArgumentType]  # WHY: nested tuple element type isn't narrowed
                    for r in group  # pyright: ignore[reportUnknownVariableType]  # WHY: nested tuple element type isn't narrowed
                )
                for group in xor_raw  # pyright: ignore[reportUnknownVariableType]  # WHY: tuple element type isn't narrowed
                if isinstance(group, (list, tuple))
            )
        else:
            leg_rail_xor_groups = ()
        new_tt = TransferTemplate(
            name=Identifier(str(new_name)),
            expected_net=Money(fields["expected_net"]),
            completion=str(fields["completion"]),
            leg_rails=leg_rails,
            leg_rail_xor_groups=leg_rail_xor_groups,
            transfer_key=(),
            description=fields.get("description"),
        )
        return dataclasses.replace(
            instance,
            transfer_templates=(*instance.transfer_templates, new_tt),
        )
    if kind == "chain":
        # Z.A grammar collapse — a chain row is now (parent, children, description?).
        # No required / xor_group flags. The studio editor's create form
        # supplies a children-checkbox-group → fields["children"] is a list[str].
        parent = fields.get("parent")
        children_raw = fields.get("children")
        if not parent:
            raise ValueError("Chain.parent is required")
        if not isinstance(children_raw, (list, tuple)):
            raise ValueError(
                "Chain.children must be a non-empty list of "
                "rail / template names (singleton ⇒ required, "
                "multi ⇒ XOR per Z.A grammar).",
            )
        children: tuple[Identifier, ...] = tuple(
            Identifier(str(c)) for c in children_raw  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]  # WHY: fields[] dict comes back Any-typed; per-item str() narrows safely
        )
        if not children:
            raise ValueError(
                "Chain.children must be non-empty (singleton ⇒ required, "
                "multi ⇒ XOR per Z.A grammar).",
            )
        # Check for duplicate row by (parent, sorted-children-tuple).
        new_key = (str(parent), tuple(sorted(str(c) for c in children)))
        for c in instance.chains:
            existing_key = (
                str(c.parent),
                tuple(sorted(str(ch) for ch in c.children)),
            )
            if existing_key == new_key:
                raise ValueError(
                    f"Chain row for parent={parent!r} with "
                    f"children={list(children)!r} already exists.",
                )
        new_ce = Chain(
            parent=Identifier(str(parent)),
            children=children,
            description=fields.get("description"),
        )
        return dataclasses.replace(
            instance, chains=(*instance.chains, new_ce),
        )
    if kind == "limit_schedule":
        parent_role = fields.get("parent_role")
        rail = fields.get("rail")
        cap = fields.get("cap")
        # AB.1 — direction defaults to Outbound (preserves legacy
        # behavior + the loader's same default for unset YAML keys).
        direction_raw = fields.get("direction") or "Outbound"
        if direction_raw not in ("Outbound", "Inbound"):
            raise ValueError(
                f"LimitSchedule.direction={direction_raw!r}: must be "
                f"'Outbound' or 'Inbound'",
            )
        if not parent_role or not rail or cap is None:
            raise ValueError(
                "LimitSchedule.parent_role / .rail / .cap "
                "are required",
            )
        if any(
            str(ls.parent_role) == str(parent_role)
            and str(ls.rail) == str(rail)
            and str(ls.direction) == str(direction_raw)
            for ls in instance.limit_schedules
        ):
            raise ValueError(
                f"LimitSchedule {parent_role}::{rail}::{direction_raw} "
                f"already exists",
            )
        new_ls = LimitSchedule(
            parent_role=Identifier(str(parent_role)),
            rail=RailName(str(rail)),
            cap=Money(cap),
            direction=direction_raw,  # pyright: ignore[reportArgumentType]: validated against the LimitDirection Literal set just above
            description=fields.get("description"),
        )
        return dataclasses.replace(
            instance, limit_schedules=(*instance.limit_schedules, new_ls),
        )
    raise ValueError(f"Unknown entity kind: {kind!r}")


def singleton_save_l2(
    instance: L2Instance,
    kind: EntityKind,
    yaml_text: str,
) -> L2Instance:
    """X.4.f.12 — write a singleton attribute (``theme`` / ``persona``)
    from a raw YAML block.

    The studio's singleton form is a single ``yaml_block`` field — the
    operator types/edits the entire ``theme:`` or ``persona:`` subtree
    as text. We parse with ``yaml.safe_load``, then dispatch to the
    loader's per-kind helper to produce the typed dataclass and use
    ``dataclasses.replace`` to swap it in. Empty / blank YAML clears
    the attribute back to ``None`` (silent-fallback contract — N.4.k
    for theme, equivalent for persona).

    Bad YAML / wrong shape raises ``ValueError`` (loader's own
    exceptions inherit ``L2LoaderError`` which carries an actionable
    message). The studio handler catches both and re-renders the form
    with the operator's typed content + the inline error.
    """
    if kind not in SINGLETON_KINDS:
        raise ValueError(
            f"singleton_save_l2: kind {kind!r} is not a singleton kind",
        )
    import yaml  # noqa: PLC0415 — lazy
    from recon_gen.common.l2.loader import (  # noqa: PLC0415 — lazy to dodge cycle
        L2LoaderError,
        _load_persona,
        _load_theme,
    )
    raw = yaml_text.strip()
    if not raw:
        # Empty ⇒ clear the singleton; silent-fallback takes over.
        if kind == "theme":
            return dataclasses.replace(instance, theme=None)
        return dataclasses.replace(instance, persona=None)
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(
            f"Expected a YAML map; got {type(parsed).__name__}",
        )
    try:
        if kind == "theme":
            new_theme = _load_theme(parsed, path=kind)  # pyright: ignore[reportUnknownArgumentType]  # WHY: yaml.safe_load returns Any-typed dict; the loader validates the shape
            return dataclasses.replace(instance, theme=new_theme)
        new_persona = _load_persona(parsed, path=kind)  # pyright: ignore[reportUnknownArgumentType]  # WHY: yaml.safe_load returns Any-typed dict; the loader validates the shape
        return dataclasses.replace(instance, persona=new_persona)
    except L2LoaderError as exc:
        raise ValueError(str(exc)) from exc


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
        # Z.A grammar collapse — composite key now "<parent>::<sorted-children-csv>".
        # Sorted so the address is stable across yaml round-trips even
        # if the children list got re-ordered during an edit.
        for i, ch in enumerate(instance.chains):
            children_csv = ",".join(sorted(str(c) for c in ch.children))
            if f"{ch.parent}::{children_csv}" == entity_id:
                return ch, i, instance.chains
    elif kind == "limit_schedule":
        # Composite key: "<parent_role>::<rail>::<direction>" (AB.1).
        # Backward-compat: a 2-part key (legacy "<parent_role>::<rail>")
        # means direction="Outbound" — that's all pre-AB.1 schedules had.
        parts = entity_id.split("::")
        if len(parts) == 2:
            parts = [*parts, "Outbound"]
        if len(parts) == 3:
            parent_role, rail, direction = parts
            for i, ls in enumerate(instance.limit_schedules):
                if (
                    str(ls.parent_role) == parent_role
                    and str(ls.rail) == rail
                    and str(ls.direction) == direction
                ):
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
    Rail.bundles_activity, Chain.parent / .children[i]. Also bumps the
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
    c: Chain, old: Identifier, new: Identifier,
) -> Chain:
    """Rewrite Chain.parent + each Chain.children[i] when they match
    old. Z.A grammar collapse — children is a tuple now, not a single
    field, so per-item rewrite."""
    parent = new if c.parent == old else c.parent
    children = tuple(new if ch == old else ch for ch in c.children)
    if parent is c.parent and children == c.children:
        return c
    return dataclasses.replace(c, parent=parent, children=children)


def _rename_transfer_template(
    instance: L2Instance, old: Identifier, new: Identifier,
) -> L2Instance:
    """Rewrite every TransferTemplate-name reference: Rail.bundles_activity,
    Chain.parent / .children[i]. Plus the template's own .name.
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
