"""Studio editor routes (X.4.e + X.4.f.1 — Account form pilot).

Implements the SPEC's "server-owned cascade" pattern:

- ``GET /l2_shape/<kind>/`` — list view (HTML page, all entities of
  that kind, click to expand into a card).
- ``GET /l2_shape/<kind>/<id>`` — read-only card fragment.
- ``GET /l2_shape/<kind>/<id>/edit`` — editable form fragment.
- ``PUT /l2_shape/<kind>/<id>`` — save flow:
  ``mutate_l2 → validate → cache.save → respond with the read fragment
  + HX-Trigger: l2-cascade-reload``.
- ``POST /l2_shape/<kind>/`` — create.
- ``DELETE /l2_shape/<kind>/<id>`` — remove (validator catches
  structural breaks; PUT handler returns 400 + inline error).

Validation-failure UX (X.4.e.5): a bad PUT returns 400 + the
validator's error rendered inline in the form fragment ONLY (targeted
HTMX swap). The user's typed-but-invalid content is preserved in the
form so they can fix it. The diagram + the rest of the entity cards
are untouched.

X.4.f.1 (Account form) ships in this module as the pilot per-entity
form. X.4.f.2-6 (Rail / Theme / Chain / TransferTemplate forms)
follow the same shape — extend ``_FIELD_SPECS_BY_KIND`` + the
per-kind ``mutate``/``create`` helpers.

Severability: this module is Studio-only; ``cli.dashboards`` doesn't
mount it. Routes are spliced into ``make_studio_routes`` when the
factory is called.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from html import escape
from typing import Any, Literal, TypeAlias

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

from quicksight_gen.common.html._studio_routes import asset_url
from quicksight_gen.common.l2.cache import L2InstanceCache
from quicksight_gen.common.l2.editor import (
    EntityKind,
    create_l2_entity,
    delete_l2_entity,
    mutate_l2,
    rename_identifier,
)
from quicksight_gen.common.l2.primitives import (
    Account,
    Identifier,
    Money,
    Name,
)
from quicksight_gen.common.l2.validate import L2ValidationError, validate


# ---------------------------------------------------------------------------
# Field-spec dispatch — per-entity form layout
# ---------------------------------------------------------------------------


FieldKind: TypeAlias = Literal["text", "select", "money", "textarea"]


@dataclasses.dataclass(frozen=True, slots=True)
class FieldSpec:
    """One form field's render instructions.

    ``name`` is the dataclass field name (matches mutate_l2's
    ``fields`` dict key). ``label`` is what the operator sees;
    ``helper`` is a one-line hint shown under the input. ``kind``
    drives the input type — text / select / money / textarea.
    ``options`` is the static option list for ``kind="select"``.
    ``select_from`` is the dynamic alternative — names a well-known
    cross-entity collection (``"roles"``) that the renderer resolves
    from the current L2 instance. Mutually exclusive with ``options``;
    pick the right one for the field's source-of-truth.
    """

    name: str
    label: str
    helper: str
    kind: FieldKind
    options: tuple[str, ...] = ()
    select_from: str | None = None
    required: bool = False


_ACCOUNT_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        name="id",
        label="ID",
        helper="Unique identifier within this L2 instance.",
        kind="text",
        required=True,
    ),
    FieldSpec(
        name="scope",
        label="Scope",
        helper="`internal` = institution-side; `external` = counterparty.",
        kind="select",
        options=("internal", "external"),
        required=True,
    ),
    FieldSpec(
        name="name",
        label="Display name",
        helper="Human-readable label rendered in dashboards + the audit PDF.",
        kind="text",
    ),
    FieldSpec(
        name="role",
        label="Role",
        helper=(
            "The Role this account plays. Required if any Rail references "
            "this account by Role (validator F1)."
        ),
        kind="text",
    ),
    FieldSpec(
        name="parent_role",
        label="Parent role",
        helper=(
            "When this is a subledger account, names its singleton parent's "
            "Role. Used by L1 limit-breach views."
        ),
        kind="select",
        select_from="roles",
    ),
    FieldSpec(
        name="expected_eod_balance",
        label="Expected EOD balance",
        helper="Numeric — empty means no EOD invariant on this account.",
        kind="money",
    ),
    FieldSpec(
        name="description",
        label="Description",
        helper="Free-form prose; markdown OK. Read by handbook templates.",
        kind="textarea",
    ),
)


_ACCOUNT_TEMPLATE_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        name="role",
        label="Role",
        helper="Role this template's instances will play (e.g. CustomerSubledger).",
        kind="text",
        required=True,
    ),
    FieldSpec(
        name="scope",
        label="Scope",
        helper="`internal` or `external`.",
        kind="select",
        options=("internal", "external"),
        required=True,
    ),
    FieldSpec(
        name="parent_role",
        label="Parent role",
        helper="Singleton parent's Role (e.g. CustomerLedger).",
        kind="select",
        select_from="roles",
    ),
    FieldSpec(
        name="expected_eod_balance",
        label="Expected EOD balance",
        helper="Numeric — empty means no EOD invariant.",
        kind="money",
    ),
    FieldSpec(
        name="description",
        label="Description",
        helper="Free-form prose; markdown OK.",
        kind="textarea",
    ),
    FieldSpec(
        name="instance_id_template",
        label="Instance ID template",
        helper='Optional Python str.format template (placeholders: {role}, {n}).',
        kind="text",
    ),
    FieldSpec(
        name="instance_name_template",
        label="Instance name template",
        helper='Optional Python str.format template (placeholders: {role}, {n}).',
        kind="text",
    ),
)


# X.4.f.2/f.3 — Rail form. Single FieldSpec list covers BOTH TwoLegRail
# and SingleLegRail; the dataclasses share most fields and the
# editor's mutate_l2 dispatches on `dataclasses.replace`. Subtype-only
# fields (source_role/destination_role vs leg_role/leg_direction)
# render only when present on the entity.
_RAIL_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        name="name",
        label="Name",
        helper="Unique rail identifier; referenced by chains + templates.",
        kind="text",
        required=True,
    ),
    FieldSpec(
        name="transfer_type",
        label="Transfer type",
        helper="e.g. ach / wire / charge / settlement.",
        kind="text",
        required=True,
    ),
    FieldSpec(
        name="origin",
        label="Origin",
        helper="ExternalForcePosted / InternalInitiated. See SPEC's Origin table.",
        kind="text",
    ),
    FieldSpec(
        name="cadence",
        label="Cadence",
        helper="For aggregating rails (e.g. intraday-2h / daily-eod).",
        kind="text",
    ),
    FieldSpec(
        name="description",
        label="Description",
        helper="Free-form prose; markdown OK.",
        kind="textarea",
    ),
)


# X.4.f.5 — Chain form (sub-list editor for required/xor children
# is X.4.f.5b; this first cut just edits the per-entry fields).
_CHAIN_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        name="parent",
        label="Parent",
        helper="Rail or TransferTemplate name that this chain entry's parent is.",
        kind="text",
        required=True,
    ),
    FieldSpec(
        name="child",
        label="Child",
        helper="Rail or TransferTemplate name expected to follow the parent.",
        kind="text",
        required=True,
    ),
    FieldSpec(
        name="required",
        label="Required",
        helper="`true` (every parent firing MUST have a child) or `false`.",
        kind="select",
        options=("true", "false"),
        required=True,
    ),
    FieldSpec(
        name="xor_group",
        label="XOR group",
        helper="When several entries share parent + xor_group, exactly one MUST fire.",
        kind="text",
    ),
    FieldSpec(
        name="description",
        label="Description",
        helper="Free-form prose.",
        kind="textarea",
    ),
)


# X.4.f.6 — TransferTemplate form (sub-list editor for leg_rails is
# X.4.f.6b; first cut takes a comma-separated string).
_TRANSFER_TEMPLATE_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        name="name",
        label="Name",
        helper="Unique template identifier.",
        kind="text",
        required=True,
    ),
    FieldSpec(
        name="transfer_type",
        label="Transfer type",
        helper="e.g. settlement_cycle / recon_cycle.",
        kind="text",
        required=True,
    ),
    FieldSpec(
        name="expected_net",
        label="Expected net",
        helper="L1 Conservation flags any firing whose legs don't sum to this.",
        kind="money",
        required=True,
    ),
    FieldSpec(
        name="completion",
        label="Completion expression",
        helper="e.g. business_day_end+1d. Drives L1 Timeliness.",
        kind="text",
        required=True,
    ),
    FieldSpec(
        name="description",
        label="Description",
        helper="Free-form prose.",
        kind="textarea",
    ),
)


# X.4.f LimitSchedule form — small + flat.
_LIMIT_SCHEDULE_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        name="parent_role",
        label="Parent role",
        helper="The role whose outbound flow is capped.",
        kind="select",
        select_from="roles",
        required=True,
    ),
    FieldSpec(
        name="transfer_type",
        label="Transfer type",
        helper="The transfer type the cap applies to.",
        kind="text",
        required=True,
    ),
    FieldSpec(
        name="cap",
        label="Cap",
        helper="Daily $ cap. L1 Limit Breach flags any day exceeding this.",
        kind="money",
        required=True,
    ),
    FieldSpec(
        name="description",
        label="Description",
        helper="Free-form prose.",
        kind="textarea",
    ),
)


# Per-kind dispatch — which fields to render, and how to coerce the
# raw form-data dict back into typed dataclass fields.
_FIELD_SPECS_BY_KIND: Mapping[EntityKind, tuple[FieldSpec, ...]] = {
    "account": _ACCOUNT_FIELDS,
    "account_template": _ACCOUNT_TEMPLATE_FIELDS,
    "rail": _RAIL_FIELDS,
    "transfer_template": _TRANSFER_TEMPLATE_FIELDS,
    "chain": _CHAIN_FIELDS,
    "limit_schedule": _LIMIT_SCHEDULE_FIELDS,
}


# ---------------------------------------------------------------------------
# Form-data coercion (form POST/PUT body → typed dataclass fields)
# ---------------------------------------------------------------------------


def _coerce_field(spec: FieldSpec, raw: str, kind: EntityKind) -> object:
    """Coerce one form-submitted string back to its dataclass-field type.

    Empty string → ``None`` for optional fields (preserves the
    "field cleared" intent on the model). NewType-of-str fields
    (Identifier / Name) are runtime str, so plain str passes through
    cleanly via ``dataclasses.replace`` — we only branch where the
    field type is non-trivial (Decimal for money; bool for the chain
    required select).
    """
    raw = raw.strip()
    if raw == "":
        return None
    if spec.kind == "money":
        from decimal import Decimal
        return Money(Decimal(raw))
    # Booleans coming from a select dropdown of "true"/"false".
    if spec.name == "required" and kind == "chain":
        return raw.lower() == "true"
    if spec.name in ("id", "role", "parent_role", "parent", "child", "xor_group", "name"):
        # Account.name is Name; everything else identifier-shaped is Identifier.
        # Both are runtime str, so the choice is annotation-only.
        if kind == "account" and spec.name == "name":
            return Name(raw)
        return Identifier(raw)
    return raw


def _coerce_form(
    kind: EntityKind, form: Mapping[str, str],
) -> dict[str, object]:
    """Walk the kind's FieldSpec list, coerce each submitted value.

    Skips fields not present in the form (treats them as "no change").
    The PUT handler hands the result to ``mutate_l2(... fields=...)``.
    """
    specs = _FIELD_SPECS_BY_KIND[kind]
    out: dict[str, object] = {}
    for spec in specs:
        if spec.name not in form:
            continue
        out[spec.name] = _coerce_field(spec, form[spec.name], kind)
    return out


# ---------------------------------------------------------------------------
# HTML render helpers
# ---------------------------------------------------------------------------


def _role_is_used_as_parent(
    instance: Any,  # typing-smell: ignore[explicit-any]: L2Instance — Any to dodge import-cycle pyright noise
    role: str,
) -> bool:
    """Is this role referenced as some entity's parent_role?

    Two-layer rule (X.4.f): an entity whose role is already someone's
    parent shouldn't itself carry a parent_role. Walks Account /
    AccountTemplate / LimitSchedule for parent_role references.
    """
    if not role:
        return False
    for a in getattr(instance, "accounts", ()):
        if str(getattr(a, "parent_role", "") or "") == role:
            return True
    for t in getattr(instance, "account_templates", ()):
        if str(getattr(t, "parent_role", "") or "") == role:
            return True
    for ls in getattr(instance, "limit_schedules", ()):
        if str(getattr(ls, "parent_role", "") or "") == role:
            return True
    return False


def _hidden_fields_for_entity(
    kind: EntityKind,
    entity: object,
    instance: Any,  # typing-smell: ignore[explicit-any]: L2Instance — Any to dodge import-cycle pyright noise
) -> frozenset[str]:
    """Which FieldSpec names should be omitted from this entity's form
    + read card given the current L2 state.

    Currently only one rule: ``parent_role`` is omitted on Account /
    AccountTemplate when this entity's own role is already used as
    someone's parent_role (two-layer rule).
    """
    if kind not in ("account", "account_template"):
        return frozenset()
    role = str(getattr(entity, "role", "") or "")
    if role and _role_is_used_as_parent(instance, role):
        return frozenset({"parent_role"})
    return frozenset()


def _resolve_select_options(
    select_from: str,
    instance: Any,  # typing-smell: ignore[explicit-any]: L2Instance — Any to dodge import-cycle pyright noise
    current_value: str,
) -> tuple[tuple[str, ...], bool]:
    """Resolve dynamic dropdown options against the current L2 instance.

    Returns (options, allow_empty). ``current_value`` is appended as a
    stale option (and ``allow_empty`` falls through) when it would
    otherwise be missing — the user can see + correct an out-of-sync
    field instead of having it silently swap to the first option.
    """
    if select_from == "roles":
        # Union of Account.role + AccountTemplate.role; sorted, deduped,
        # blanks dropped. The empty option is always offered because
        # Account.parent_role is optional (subledger marker).
        roles: set[str] = set()
        for a in getattr(instance, "accounts", ()):
            r = getattr(a, "role", None)
            if r is not None and str(r):
                roles.add(str(r))
        for t in getattr(instance, "account_templates", ()):
            r = getattr(t, "role", None)
            if r is not None and str(r):
                roles.add(str(r))
        opts = tuple(sorted(roles))
        if current_value and current_value not in opts:
            opts = (*opts, current_value)
        return opts, True
    raise ValueError(f"Unknown select_from source: {select_from!r}")


def _render_field(
    spec: FieldSpec, value: object,
    instance: Any,  # typing-smell: ignore[explicit-any]: L2Instance — needed to resolve select_from at render time
    error: str | None = None,
) -> str:
    """One form-field <div> with label + input + helper + (optional) error.

    The error fragment slot lets the X.4.e.5 validation-failure path
    render per-field validator errors inline without losing the
    user's typed content.
    """
    val_str = _value_to_input_str(value)
    label = (
        f'<label for="field-{spec.name}">{escape(spec.label)}'
        f'{"<span class=\"required\"> *</span>" if spec.required else ""}'
        f"</label>"
    )
    helper = (
        f'<small class="field-helper">{escape(spec.helper)}</small>'
        if spec.helper else ""
    )
    err_html = (
        f'<div class="field-error">{escape(error)}</div>' if error else ""
    )

    if spec.kind == "select":
        if spec.select_from is not None:
            options, allow_empty = _resolve_select_options(
                spec.select_from, instance, val_str,
            )
        else:
            options, allow_empty = spec.options, False
        opt_blocks: list[str] = []
        if allow_empty:
            opt_blocks.append(
                f'<option value=""{" selected" if val_str == "" else ""}>'
                f"— none —</option>"
            )
        opt_blocks.extend(
            f'<option value="{escape(o)}"{" selected" if o == val_str else ""}>'
            f"{escape(o)}</option>"
            for o in options
        )
        input_html = (
            f'<select id="field-{spec.name}" name="{escape(spec.name)}">'
            f'{"".join(opt_blocks)}</select>'
        )
    elif spec.kind == "textarea":
        input_html = (
            f'<textarea id="field-{spec.name}" name="{escape(spec.name)}" '
            f'rows="3">{escape(val_str)}</textarea>'
        )
    else:
        # text + money both render as <input type="text"> — the loader's
        # _load_money handles numeric strings either way.
        input_html = (
            f'<input id="field-{spec.name}" name="{escape(spec.name)}" '
            f'type="text" value="{escape(val_str)}">'
        )

    return (
        f'<div class="field-row">{label}{input_html}{helper}{err_html}</div>'
    )


def _value_to_input_str(value: object) -> str:
    """Stringify a dataclass field value for the form input's `value=`."""
    if value is None:
        return ""
    return str(value)


def _render_read_card(
    kind: EntityKind, entity: object,
    instance: Any,  # typing-smell: ignore[explicit-any]: L2Instance — needed to suppress fields hidden by the two-layer rule
) -> str:
    """Read-only card — the post-PUT response + the click-to-expand
    target for the list view.
    """
    specs = _FIELD_SPECS_BY_KIND[kind]
    entity_id = _entity_id(kind, entity)
    hidden = _hidden_fields_for_entity(kind, entity, instance)
    rows = "".join(
        f'<dt>{escape(s.label)}</dt><dd>'
        f"{escape(_value_to_input_str(getattr(entity, s.name, None))) or '—'}"
        f"</dd>"
        for s in specs
        if s.name not in hidden
    )
    # X.4.f.8.reverse — card title becomes a click target that navigates
    # the diagram iframe to ?focus=<node_id>. The home page's iframe-load
    # listener then fans out the existing filter pipeline. data-focus-node
    # on the card carries the right node-id prefix per kind; absent when
    # the entity has no natural diagram target (e.g., an Account with no
    # role) so the JS falls through to a plain-text title.
    focus_node = _focus_node_for_entity(kind, entity, instance)
    if focus_node is None:
        title_html = f"<h3>{escape(entity_id)}</h3>"
    else:
        title_html = (
            f'<h3 class="entity-card-title" tabindex="0" role="button" '
            f'data-focus-node="{escape(focus_node)}" '
            f'title="Focus the diagram on this entity">'
            f"{escape(entity_id)}</h3>"
        )
    return (
        f'<article class="entity-card" id="entity-{kind}-{escape(entity_id)}" '
        f'data-kind="{escape(kind)}" data-entity-id="{escape(entity_id)}">'
        f"<header>"
        f"{title_html}"
        f'<div class="entity-card-actions">'
        f'<a class="edit-link" hx-get="/l2_shape/{kind}/{escape(entity_id)}/edit" '
        f'hx-target="#entity-{kind}-{escape(entity_id)}" hx-swap="outerHTML">Edit</a>'
        # X.4.f.9.delete — DELETE on success returns empty (card disappears
        # via outerHTML swap); on validator-rejected structural break
        # returns 400 + the error fragment which swaps in place. No
        # cascade — the operator clears the dependent reference first.
        f'<a class="delete-link" hx-delete="/l2_shape/{kind}/{escape(entity_id)}" '
        f'hx-target="#entity-{kind}-{escape(entity_id)}" hx-swap="outerHTML" '
        f'hx-confirm="Delete this entity? References that block deletion '
        f'will be reported inline.">Delete</a>'
        f"</div>"
        f"</header>"
        f"<dl>{rows}</dl>"
        f"</article>"
    )


def _focus_node_for_entity(
    kind: EntityKind,
    entity: object,
    instance: Any,  # typing-smell: ignore[explicit-any]: L2Instance — needed to disambiguate chain.parent shape (rail vs template)
) -> str | None:
    """Map an entity to its natural diagram-node id (prefix-encoded
    per the topology helpers — ``role__X`` / ``rail__X`` / ``tmpl__X``).

    Used by the home page's reverse filter (X.4.f.8.reverse): clicking
    the card title focuses the diagram on this node, which fans out
    the existing /diagram/visible pipeline.

    Returns None when there's no meaningful target — Account without a
    role, AccountTemplate (impossible — role is required), etc. The
    renderer then drops the click affordance and shows plain text.
    """
    if kind in ("account", "account_template"):
        role = getattr(entity, "role", None)
        if role is None or not str(role):
            return None
        return f"role__{role}"
    if kind in ("rail", "transfer_template"):
        name = getattr(entity, "name", None)
        if name is None or not str(name):
            return None
        prefix = "rail__" if kind == "rail" else "tmpl__"
        return f"{prefix}{name}"
    if kind == "chain":
        # parent could be a rail name OR a template name — pick the
        # right prefix by checking which collection it belongs to.
        parent = getattr(entity, "parent", None)
        if parent is None or not str(parent):
            return None
        template_names = {
            t.name for t in getattr(instance, "transfer_templates", ())
        }
        prefix = "tmpl__" if parent in template_names else "rail__"
        return f"{prefix}{parent}"
    if kind == "limit_schedule":
        # Anchor on the parent_role node.
        parent_role = getattr(entity, "parent_role", None)
        if parent_role is None or not str(parent_role):
            return None
        return f"role__{parent_role}"
    return None


def _render_edit_form(
    kind: EntityKind,
    entity: object,
    instance: Any,  # typing-smell: ignore[explicit-any]: L2Instance — needed for dynamic select_from resolution
    form_overrides: Mapping[str, str] | None = None,
    field_errors: Mapping[str, str] | None = None,
    global_error: str | None = None,
) -> str:
    """Editable form fragment. ``form_overrides`` lets the validation-
    failure path re-render with the user's typed-but-invalid values
    (X.4.e.5)."""
    specs = _FIELD_SPECS_BY_KIND[kind]
    entity_id = _entity_id(kind, entity)
    field_errors = field_errors or {}
    overrides = form_overrides or {}

    hidden = _hidden_fields_for_entity(kind, entity, instance)
    fields_html = "".join(
        _render_field(
            s,
            overrides.get(s.name, getattr(entity, s.name, None)),
            instance,
            error=field_errors.get(s.name),
        )
        for s in specs
        if s.name not in hidden
    )
    global_err_html = (
        f'<div class="form-global-error">{escape(global_error)}</div>'
        if global_error else ""
    )
    return (
        f'<article class="entity-card editing" '
        f'id="entity-{kind}-{escape(entity_id)}">'
        f"<header><h3>Editing: {escape(entity_id)}</h3></header>"
        f'<form hx-put="/l2_shape/{kind}/{escape(entity_id)}" '
        f'hx-target="#entity-{kind}-{escape(entity_id)}" '
        f'hx-swap="outerHTML">'
        f"{global_err_html}"
        f"{fields_html}"
        f'<div class="form-actions">'
        f'<button type="submit">Save</button>'
        f'<a class="cancel-link" hx-get="/l2_shape/{kind}/{escape(entity_id)}" '
        f'hx-target="#entity-{kind}-{escape(entity_id)}" hx-swap="outerHTML">'
        f"Cancel</a>"
        f"</div>"
        f"</form>"
        f"</article>"
    )


_CREATE_INTRO_BY_KIND: Mapping[EntityKind, str] = {
    "account": (
        "<p><strong>An Account</strong> is one row in the institution's "
        "chart of accounts — a singleton ledger position the institution "
        "either owns (<em>internal</em>) or counterparty-owns "
        "(<em>external</em>). Every money-movement leg posts to one Account "
        "by ID. Accounts that share a <em>role</em> are interchangeable "
        "from the rest of the L2 model's perspective; rails / templates / "
        "limit-schedules reference accounts by role, not by id.</p>"
        "<p>Required: <code>id</code> (the addressing key — used in "
        "URLs and on every transaction row's <code>account_id</code>). "
        "Strongly recommended: <code>role</code> (without it the account "
        "isn't reachable by any rail) and <code>name</code> (what shows "
        "up in dashboards).</p>"
    ),
    "account_template": (
        "<p><strong>An AccountTemplate</strong> declares a Role that "
        "exists as <em>many instances</em> rather than as a singleton. "
        "It's the L2 model's way of saying: <em>this role isn't one "
        "specific account — it's a class of accounts</em>. The canonical "
        "example is the customer subledger: every customer gets their "
        "own Account row (<code>cust-001</code>, <code>cust-002</code>, "
        "<code>cust-003</code>…) but they all carry "
        "<code>role: CustomerSubledger</code>, and rails / chains / "
        "limit-schedules reference the role — never the individual "
        "account ids.</p>"
        "<p><strong>Why declare it at all?</strong> Two reasons:</p>"
        "<ul>"
        "<li><strong>Intent signal.</strong> Without a template, a "
        "second Account sharing a role looks like a mistake. With one, "
        "the validator + dashboards know the role is <em>expected</em> "
        "to fan out across many instances.</li>"
        "<li><strong>Shared structural defaults.</strong> The template's "
        "<code>parent_role</code> wires every instance up to the same "
        "control account for L1 limit-breach roll-ups (e.g. all "
        "<code>CustomerSubledger</code>s roll up under "
        "<code>CustomerLedger</code>). Setting it once on the template "
        "saves repeating it on every individual account.</li>"
        "</ul>"
        "<p><strong>How instances get created:</strong> the operator "
        "either hand-writes each Account with the matching "
        "<code>role:</code>, or uses the template's "
        "<code>instance_id_template</code> / "
        "<code>instance_name_template</code> str.format strings "
        "(placeholders <code>{role}</code> and <code>{n}</code>) for "
        "programmatic synthesis — the demo seed pipeline reads those "
        "templates to materialize realistic counts of subledgers / "
        "merchant DDAs / per-counterparty accounts.</p>"
        "<p><strong>Common patterns:</strong> customer subledgers (one "
        "per customer), merchant DDAs (one per merchant), per-product "
        "fee accumulation accounts, per-counterparty external accounts. "
        "Anything where the institution holds many ledger positions "
        "that play the same role in flow.</p>"
        "<p><strong>Required:</strong> <code>role</code> (the role name "
        "every instance will carry — also the AccountTemplate's "
        "addressing key) and <code>scope</code> "
        "(<code>internal</code> for institution-side, "
        "<code>external</code> for counterparty-side). "
        "<code>parent_role</code> is strongly recommended whenever "
        "instances should be subject to a daily-cap LimitSchedule "
        "anchored on a control role.</p>"
    ),
    "rail": (
        "<p><strong>A Rail</strong> is a money-movement contract — one "
        "well-known way value flows between roles. ACH origination, wire "
        "settlement, intra-day pool balancing, fee debits all live as "
        "Rails. Every transaction must match a rail by "
        "<code>(transfer_type, source_role, destination_role)</code>.</p>"
        "<p>Required: <code>name</code> (unique identifier; chains and "
        "templates reference rails by name) and <code>transfer_type</code> "
        "(e.g. <code>ach</code>, <code>wire</code>, <code>charge</code>, "
        "<code>settlement</code>). Endpoint roles "
        "(<code>source_role</code> / <code>destination_role</code>) are "
        "edited on the rail itself after it's created — required for the "
        "validator to accept the rail as connected.</p>"
    ),
    "transfer_template": (
        "<p><strong>A TransferTemplate</strong> is a multi-leg event — "
        "several Rail firings that the L1 layer expects to balance to "
        "<code>expected_net</code> by <code>completion</code>. Settlement "
        "cycles, return-bundle reconciliations, anything that's not just "
        "one rail firing on its own.</p>"
        "<p>Required: <code>name</code>, <code>transfer_type</code>, "
        "<code>expected_net</code> (often 0 for fully-balanced cycles; "
        "fees may sum to a non-zero target), <code>completion</code> "
        "(the deadline expression like <code>business_day_end+1d</code>). "
        "<code>leg_rails</code> is edited after creation.</p>"
    ),
    "chain": (
        "<p><strong>A ChainEntry</strong> says: when this <em>parent</em> "
        "rail or template fires, the L1 layer expects this <em>child</em> "
        "to follow within the SLA. A required chain whose child doesn't "
        "fire surfaces as a stuck-pending invariant violation.</p>"
        "<p>Required: <code>parent</code> + <code>child</code> "
        "(rail or template names) and <code>required</code> (true = MUST "
        "follow; false = optional). <code>xor_group</code> wires "
        "exactly-one-of branching (e.g. ACH return reasons).</p>"
    ),
    "limit_schedule": (
        "<p><strong>A LimitSchedule</strong> is a daily $-cap on outbound "
        "flow from a parent role for a given transfer_type. Any day "
        "exceeding the cap surfaces as an L1 limit-breach violation.</p>"
        "<p>Required: <code>parent_role</code> (the role whose outbound "
        "flow is capped), <code>transfer_type</code>, and <code>cap</code> "
        "(the $ ceiling).</p>"
    ),
}


def _render_create_page(
    kind: EntityKind,
    instance: Any,  # typing-smell: ignore[explicit-any]: L2Instance — needed for select_from option resolution
    form_overrides: Mapping[str, str] | None = None,
    global_error: str | None = None,
) -> str:
    """X.4.f.9.create-page — full HTML page for creating a new entity.

    Wraps the field form in chrome (header + back link) and a per-kind
    intro paragraph that explains what this entity kind IS — the
    operator landing here for the first time gets the "what" + "why"
    before the "how".

    Form is a plain HTML POST to ``/l2_shape/<kind>/`` (no HTMX);
    success → 303 redirect to the home page; validation failure →
    re-render this same page with the operator's typed values + the
    error inline so they can fix it without losing input.
    """
    specs = _FIELD_SPECS_BY_KIND[kind]
    overrides = form_overrides or {}
    fields_html = "".join(
        _render_field(s, overrides.get(s.name, ""), instance)
        for s in specs
    )
    global_err_html = (
        f'<div class="form-global-error">{escape(global_error)}</div>'
        if global_error else ""
    )
    intro_html = _CREATE_INTRO_BY_KIND.get(kind, "")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Create new {escape(kind)} — Studio</title>
  <link rel="stylesheet" href="{asset_url("diagram.css")}">
  <link rel="stylesheet" href="{asset_url("editor.css")}">
</head>
<body class="create-page">
  <header class="studio-header">
    <h1>Create new {escape(kind)}</h1>
    <a class="nav-link" href="/">← back to Studio</a>
    <a class="nav-link" href="/l2_shape/{escape(kind)}/">→ list all {escape(kind)}s</a>
  </header>
  <main class="create-page-main">
    <section class="create-intro">{intro_html}</section>
    <section class="create-form-wrap">
      <form method="post" action="/l2_shape/{escape(kind)}/" class="create-form">
        {global_err_html}
        {fields_html}
        <div class="form-actions">
          <button type="submit">Create</button>
          <a class="cancel-link" href="/">Cancel</a>
        </div>
      </form>
    </section>
  </main>
</body>
</html>
"""


def _render_list_page(
    kind: EntityKind, entities: tuple[object, ...],
    instance: Any,  # typing-smell: ignore[explicit-any]: L2Instance — passed through to per-card hide logic
    *,
    embed: bool = False,
) -> str:
    """Full HTML page — every entity of the kind rendered as a read card.

    ``embed=True`` returns just the cards container (no html/head/body)
    so the X.4.f.7 home page can ``hx-get`` it into a section without
    nesting full documents. The home page's own <head> already loads
    htmx + the editor CSS + the htmx:beforeSwap fix, so the embed
    fragment doesn't need to redeclare them.
    """
    cards = "\n".join(_render_read_card(kind, e, instance) for e in entities)
    if embed:
        return f'<div class="entity-list" data-kind="{escape(kind)}">{cards}</div>'
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Studio editor — {escape(kind)}</title>
  <link rel="stylesheet" href="{asset_url("diagram.css")}">
  <link rel="stylesheet" href="{asset_url("editor.css")}">
  <script src="https://unpkg.com/htmx.org@1.9.10"></script>
  <script>
    // X.4.e.5 fix — HTMX defaults to NOT swapping 4xx response bodies
    // (treats them as errors). The validator returns 400 + an inline
    // error fragment; we WANT that fragment swapped in so the user
    // sees the error + their typed-but-invalid form content. Enable
    // 4xx swaps explicitly. (5xx still treated as errors.)
    // Attach to `document`, not `document.body` — this script runs in
    // <head> before <body> is parsed, so document.body is null and
    // .addEventListener would throw a TypeError. HTMX events bubble
    // all the way up to document, so this catches them just the same.
    document.addEventListener('htmx:beforeSwap', function(evt) {{
      var status = evt.detail.xhr.status;
      if (status >= 400 && status < 500) {{
        evt.detail.shouldSwap = true;
        evt.detail.isError = false;
      }}
    }});
  </script>
</head>
<body>
  <header class="studio-header">
    <h1>Studio · editor · {escape(kind)}</h1>
    <a class="nav-link" href="/">← landing</a>
    <a class="nav-link" href="/diagram">→ diagram</a>
  </header>
  <main id="entity-list">
    {cards}
  </main>
</body>
</html>
"""


def _entity_id(kind: EntityKind, entity: object) -> str:
    """Read the addressing key off an entity — symmetric with editor.py's
    ``_find_entity`` lookup."""
    if kind == "account":
        return str(getattr(entity, "id"))
    if kind == "account_template":
        return str(getattr(entity, "role"))
    if kind in ("rail", "transfer_template"):
        return str(getattr(entity, "name"))
    if kind == "chain":
        return f"{getattr(entity, 'parent')}::{getattr(entity, 'child')}"
    # limit_schedule
    return f"{getattr(entity, 'parent_role')}::{getattr(entity, 'transfer_type')}"


def _entities_for_kind(
    instance: Any, kind: EntityKind,  # typing-smell: ignore[explicit-any]: L2Instance type — Any to dodge import-cycle pyright noise
) -> tuple[object, ...]:
    return getattr(instance, {
        "account": "accounts",
        "account_template": "account_templates",
        "rail": "rails",
        "transfer_template": "transfer_templates",
        "chain": "chains",
        "limit_schedule": "limit_schedules",
    }[kind])


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


def _make_handlers(cache: L2InstanceCache) -> dict[str, Any]:  # typing-smell: ignore[explicit-any]: per-handler ASGI callables; uniform shape but per-route closure
    """Build closures over the cache for each route handler.

    Returned as a dict keyed by route name so ``make_editor_routes``
    can register them all in one pass.
    """

    async def list_view(request: Request) -> HTMLResponse:
        kind = _kind_from_path(request.path_params["kind"])
        if kind is None or kind not in _FIELD_SPECS_BY_KIND:
            return HTMLResponse(
                f"<h1>404</h1><p>{escape(request.path_params['kind'])} "
                f"is not an editable entity kind (yet).</p>",
                status_code=404,
            )
        inst = cache.get()
        entities = _entities_for_kind(inst, kind)
        # X.4.f.7 — ?embed=1 returns the cards fragment only (no html/head/
        # body) so the home page can hx-get it into a <details> section
        # without nesting full documents.
        embed = request.query_params.get("embed") == "1"
        return HTMLResponse(
            _render_list_page(kind, entities, inst, embed=embed),
        )

    async def read_card(request: Request) -> HTMLResponse:
        kind = _kind_from_path(request.path_params["kind"])
        entity_id = request.path_params["entity_id"]
        if kind is None or kind not in _FIELD_SPECS_BY_KIND:
            return HTMLResponse("not editable", status_code=404)
        inst = cache.get()
        entity = _find_entity_or_none(inst, kind, entity_id)
        if entity is None:
            return HTMLResponse("not found", status_code=404)
        return HTMLResponse(_render_read_card(kind, entity, inst))

    async def edit_form(request: Request) -> HTMLResponse:
        kind = _kind_from_path(request.path_params["kind"])
        entity_id = request.path_params["entity_id"]
        if kind is None or kind not in _FIELD_SPECS_BY_KIND:
            return HTMLResponse("not editable", status_code=404)
        inst = cache.get()
        entity = _find_entity_or_none(inst, kind, entity_id)
        if entity is None:
            return HTMLResponse("not found", status_code=404)
        return HTMLResponse(_render_edit_form(kind, entity, inst))

    async def save(request: Request) -> HTMLResponse:
        """X.4.e.4 — the cascade flow: validate → mutate → save → respond.

        Validation failure → 400 + edit-form fragment with the error
        rendered inline (preserves user's typed content). Success →
        200 + read-card fragment + ``HX-Trigger: l2-cascade-reload``.
        """
        kind = _kind_from_path(request.path_params["kind"])
        entity_id = request.path_params["entity_id"]
        if kind is None or kind not in _FIELD_SPECS_BY_KIND:
            return HTMLResponse("not editable", status_code=404)

        form = await request.form()
        coerced: dict[str, str] = {
            str(k): str(v) for k, v in form.items()
        }
        try:
            new_fields = _coerce_form(kind, coerced)
        except (ValueError, TypeError) as exc:
            inst = cache.get()
            entity = _find_entity_or_none(inst, kind, entity_id)
            return HTMLResponse(
                _render_edit_form(
                    kind, entity if entity is not None else _placeholder(kind),
                    inst,
                    form_overrides=coerced,
                    global_error=f"Field coercion failed: {exc}",
                ),
                status_code=400,
            )

        old_inst = cache.get()
        # Capture rename-trigger value BEFORE mutate so we can detect
        # whether the operator renamed an identifier this PUT.
        trigger = _rename_trigger_field(kind)
        old_entity = _find_entity_or_none(old_inst, kind, entity_id)
        old_trigger_val = (
            str(getattr(old_entity, trigger, "") or "")
            if old_entity is not None and trigger is not None
            else ""
        )

        try:
            new_inst = mutate_l2(old_inst, kind, entity_id, new_fields)
        except KeyError:
            return HTMLResponse("not found", status_code=404)

        # X.4.f.7.cascade — if the trigger field changed, walk the L2
        # and rewrite every reference to the old value (Rail roles,
        # AccountTemplate.parent_role, LimitSchedule.parent_role for a
        # role rename; TransferTemplate.leg_rails / Rail.bundles_activity
        # / ChainEntry.parent|child for a rail/template name rename).
        # Skip when emptying a value or the trigger didn't change —
        # cascading an empty value would wipe references.
        if trigger is not None and trigger in new_fields:
            raw_new = new_fields[trigger]
            new_trigger_val = str(raw_new or "") if raw_new is not None else ""
            if (
                old_trigger_val
                and new_trigger_val
                and old_trigger_val != new_trigger_val
            ):
                from quicksight_gen.common.l2.primitives import (  # noqa: PLC0415
                    Identifier,
                )
                new_inst = rename_identifier(
                    new_inst, kind,
                    Identifier(old_trigger_val),
                    Identifier(new_trigger_val),
                )

        try:
            validate(new_inst)
        except L2ValidationError as exc:
            inst = cache.get()
            entity = _find_entity_or_none(inst, kind, entity_id)
            return HTMLResponse(
                _render_edit_form(
                    kind, entity if entity is not None else _placeholder(kind),
                    inst,
                    form_overrides=coerced,
                    global_error=str(exc),
                ),
                status_code=400,
            )

        cache.save(new_inst)
        new_entity = _find_entity_or_none(
            new_inst, kind,
            # If the form changed the addressing key (id/name/role),
            # the entity is now keyed under the NEW value.
            str(new_fields.get(_addressing_field(kind), entity_id)),
        )
        resp = HTMLResponse(
            _render_read_card(kind, new_entity, new_inst)
            if new_entity is not None else "saved",
        )
        # X.4.e.7 — diagram + entity list listen for this trigger and
        # hx-get themselves to pick up the cascade.
        resp.headers["HX-Trigger"] = "l2-cascade-reload"
        return resp

    async def new_form(request: Request) -> HTMLResponse:
        kind = _kind_from_path(request.path_params["kind"])
        if kind is None or kind not in _FIELD_SPECS_BY_KIND:
            return HTMLResponse("not editable", status_code=404)
        return HTMLResponse(_render_create_page(kind, cache.get()))

    async def create(request: Request) -> Response:
        """X.4.f.9.create — POST a new entity into the kind's collection.

        Coerce → construct (catches required-field errors) → validate
        → save → 303-redirect back to home. Failure re-renders the
        create page with the error inline + the operator's typed
        values preserved.
        """
        kind = _kind_from_path(request.path_params["kind"])
        if kind is None or kind not in _FIELD_SPECS_BY_KIND:
            return HTMLResponse("not editable", status_code=404)

        form = await request.form()
        coerced: dict[str, str] = {str(k): str(v) for k, v in form.items()}
        try:
            new_fields = _coerce_form(kind, coerced)
        except (ValueError, TypeError) as exc:
            return HTMLResponse(
                _render_create_page(
                    kind, cache.get(),
                    form_overrides=coerced,
                    global_error=f"Field coercion failed: {exc}",
                ),
                status_code=400,
            )

        try:
            new_inst = create_l2_entity(cache.get(), kind, new_fields)
        except ValueError as exc:
            return HTMLResponse(
                _render_create_page(
                    kind, cache.get(),
                    form_overrides=coerced,
                    global_error=str(exc),
                ),
                status_code=400,
            )

        try:
            validate(new_inst)
        except L2ValidationError as exc:
            return HTMLResponse(
                _render_create_page(
                    kind, cache.get(),
                    form_overrides=coerced,
                    global_error=str(exc),
                ),
                status_code=400,
            )

        cache.save(new_inst)
        # Plain-form POST → 303 redirect back to home. Browser navigates;
        # the operator sees the new entity in its section. No HTMX
        # involvement here (the create page is full-page nav, not an
        # in-place swap).
        return RedirectResponse("/", status_code=303)

    async def delete_handler(request: Request) -> HTMLResponse:
        kind = _kind_from_path(request.path_params["kind"])
        entity_id = request.path_params["entity_id"]
        if kind is None or kind not in _FIELD_SPECS_BY_KIND:
            return HTMLResponse("not editable", status_code=404)

        try:
            new_inst = delete_l2_entity(cache.get(), kind, entity_id)
        except KeyError:
            return HTMLResponse("not found", status_code=404)

        try:
            validate(new_inst)
        except L2ValidationError as exc:
            return HTMLResponse(
                f'<div class="form-global-error">'
                f"Cannot delete: {escape(str(exc))}</div>",
                status_code=400,
            )

        cache.save(new_inst)
        # Empty body — the chrome's HX-Swap removes the card.
        resp = HTMLResponse("")
        resp.headers["HX-Trigger"] = "l2-cascade-reload"
        return resp

    return {
        "list_view": list_view,
        "read_card": read_card,
        "edit_form": edit_form,
        "save": save,
        "delete": delete_handler,
        "new_form": new_form,
        "create": create,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_VALID_KINDS: frozenset[str] = frozenset(
    ("account", "account_template", "rail", "transfer_template", "chain",
     "limit_schedule"),
)


def _kind_from_path(raw: str) -> EntityKind | None:
    """Coerce the URL path slug to a typed EntityKind. None if invalid."""
    if raw in _VALID_KINDS:
        return raw  # type: ignore[return-value]: validated against the typed Literal set
    return None


def _addressing_field(kind: EntityKind) -> str:
    """Which dataclass field is the addressing key for this kind."""
    return {
        "account": "id",
        "account_template": "role",
        "rail": "name",
        "transfer_template": "name",
        # chains + limit_schedules use composite keys → this is the
        # FIRST half (the second is fixed in the URL).
        "chain": "parent",
        "limit_schedule": "parent_role",
    }[kind]


def _rename_trigger_field(kind: EntityKind) -> str | None:
    """Which field's change should cascade across L2 references.

    Per ``editor.rename_identifier``:

    - account / account_template — ``role`` is the cross-cutting
      identifier (Rail.source_role, parent_role, LimitSchedule.parent_role,
      …). Account.id is addressing-only — no incoming references.
    - rail / transfer_template — ``name`` is both the addressing key
      AND the reference target (TransferTemplate.leg_rails,
      Rail.bundles_activity, ChainEntry.parent/child).
    - chain / limit_schedule — leaf consumers; nothing references
      them. Returns None → no cascade.
    """
    return {
        "account": "role",
        "account_template": "role",
        "rail": "name",
        "transfer_template": "name",
    }.get(kind)


def _find_entity_or_none(
    instance: Any, kind: EntityKind, entity_id: str,  # typing-smell: ignore[explicit-any]: L2Instance — read-only field access; not in pyright strict scope yet
) -> object | None:
    for e in _entities_for_kind(instance, kind):
        if _entity_id(kind, e) == entity_id:
            return e
    return None


def _placeholder(kind: EntityKind) -> object:
    """Return a blank entity for the form re-render path when the
    original was deleted mid-flight (rare race; defensive fallback).
    """
    if kind == "account":
        return Account(id=Identifier("(unknown)"), scope="internal")
    # Other kinds: stub similar; for X.4.f.1 only Account is wired.
    raise NotImplementedError(f"placeholder for {kind} not yet defined")


# ---------------------------------------------------------------------------
# Public route-list factory
# ---------------------------------------------------------------------------


def make_editor_routes(cache: L2InstanceCache) -> list[Route]:
    """Build the editor route list bound to ``cache``.

    Spliced into ``make_studio_routes`` (X.4.e.7) so the cache + the
    diagram routes share one in-memory instance per server.
    """
    h = _make_handlers(cache)
    return [
        Route(
            "/l2_shape/{kind}/", h["list_view"], methods=["GET"],
        ),
        Route(
            "/l2_shape/{kind}/", h["create"], methods=["POST"],
            name="l2_shape_create",
        ),
        # ``/new`` MUST be declared before ``/{entity_id}`` so Starlette's
        # path matcher doesn't treat the literal "new" as an entity_id.
        Route(
            "/l2_shape/{kind}/new", h["new_form"], methods=["GET"],
            name="l2_shape_new_form",
        ),
        Route(
            "/l2_shape/{kind}/{entity_id}", h["read_card"],
            methods=["GET"], name="l2_shape_read",
        ),
        Route(
            "/l2_shape/{kind}/{entity_id}/edit", h["edit_form"],
            methods=["GET"], name="l2_shape_edit",
        ),
        Route(
            "/l2_shape/{kind}/{entity_id}", h["save"],
            methods=["PUT"], name="l2_shape_save",
        ),
        Route(
            "/l2_shape/{kind}/{entity_id}", h["delete"],
            methods=["DELETE"], name="l2_shape_delete",
        ),
    ]
