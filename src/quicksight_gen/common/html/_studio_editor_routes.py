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
from starlette.responses import HTMLResponse
from starlette.routing import Route

from quicksight_gen.common.l2.cache import L2InstanceCache
from quicksight_gen.common.l2.editor import (
    EntityKind,
    delete_l2_entity,
    mutate_l2,
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
    ``options`` only matters for ``kind="select"``.
    """

    name: str
    label: str
    helper: str
    kind: FieldKind
    options: tuple[str, ...] = ()
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
        kind="text",
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
        kind="text",
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
        kind="text",
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


def _render_field(
    spec: FieldSpec, value: object, error: str | None = None,
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
        opts = "".join(
            f'<option value="{escape(o)}"{" selected" if o == val_str else ""}>'
            f"{escape(o)}</option>"
            for o in spec.options
        )
        input_html = (
            f'<select id="field-{spec.name}" name="{escape(spec.name)}">'
            f'{opts}</select>'
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


def _render_read_card(kind: EntityKind, entity: object) -> str:
    """Read-only card — the post-PUT response + the click-to-expand
    target for the list view.
    """
    specs = _FIELD_SPECS_BY_KIND[kind]
    entity_id = _entity_id(kind, entity)
    rows = "".join(
        f'<dt>{escape(s.label)}</dt><dd>'
        f"{escape(_value_to_input_str(getattr(entity, s.name, None))) or '—'}"
        f"</dd>"
        for s in specs
    )
    return (
        f'<article class="entity-card" id="entity-{kind}-{escape(entity_id)}">'
        f"<header>"
        f'<h3>{escape(entity_id)}</h3>'
        f'<a class="edit-link" hx-get="/l2_shape/{kind}/{escape(entity_id)}/edit" '
        f'hx-target="#entity-{kind}-{escape(entity_id)}" hx-swap="outerHTML">Edit</a>'
        f"</header>"
        f"<dl>{rows}</dl>"
        f"</article>"
    )


def _render_edit_form(
    kind: EntityKind,
    entity: object,
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

    fields_html = "".join(
        _render_field(
            s,
            overrides.get(s.name, getattr(entity, s.name, None)),
            error=field_errors.get(s.name),
        )
        for s in specs
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


def _render_list_page(kind: EntityKind, entities: tuple[object, ...]) -> str:
    """Full HTML page — every entity of the kind rendered as a read card."""
    cards = "\n".join(_render_read_card(kind, e) for e in entities)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Studio editor — {escape(kind)}</title>
  <link rel="stylesheet" href="/studio/static/diagram.css">
  <link rel="stylesheet" href="/studio/static/editor.css">
  <script src="https://unpkg.com/htmx.org@1.9.10"></script>
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
        entities = _entities_for_kind(cache.get(), kind)
        return HTMLResponse(_render_list_page(kind, entities))

    async def read_card(request: Request) -> HTMLResponse:
        kind = _kind_from_path(request.path_params["kind"])
        entity_id = request.path_params["entity_id"]
        if kind is None or kind not in _FIELD_SPECS_BY_KIND:
            return HTMLResponse("not editable", status_code=404)
        entity = _find_entity_or_none(cache.get(), kind, entity_id)
        if entity is None:
            return HTMLResponse("not found", status_code=404)
        return HTMLResponse(_render_read_card(kind, entity))

    async def edit_form(request: Request) -> HTMLResponse:
        kind = _kind_from_path(request.path_params["kind"])
        entity_id = request.path_params["entity_id"]
        if kind is None or kind not in _FIELD_SPECS_BY_KIND:
            return HTMLResponse("not editable", status_code=404)
        entity = _find_entity_or_none(cache.get(), kind, entity_id)
        if entity is None:
            return HTMLResponse("not found", status_code=404)
        return HTMLResponse(_render_edit_form(kind, entity))

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
            entity = _find_entity_or_none(cache.get(), kind, entity_id)
            return HTMLResponse(
                _render_edit_form(
                    kind, entity if entity is not None else _placeholder(kind),
                    form_overrides=coerced,
                    global_error=f"Field coercion failed: {exc}",
                ),
                status_code=400,
            )

        try:
            new_inst = mutate_l2(cache.get(), kind, entity_id, new_fields)
        except KeyError:
            return HTMLResponse("not found", status_code=404)

        try:
            validate(new_inst)
        except L2ValidationError as exc:
            entity = _find_entity_or_none(cache.get(), kind, entity_id)
            return HTMLResponse(
                _render_edit_form(
                    kind, entity if entity is not None else _placeholder(kind),
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
            _render_read_card(kind, new_entity)
            if new_entity is not None else "saved",
        )
        # X.4.e.7 — diagram + entity list listen for this trigger and
        # hx-get themselves to pick up the cascade.
        resp.headers["HX-Trigger"] = "l2-cascade-reload"
        return resp

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
