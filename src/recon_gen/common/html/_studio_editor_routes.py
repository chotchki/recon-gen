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
from collections.abc import Callable, Iterable, Mapping
from datetime import timedelta
from html import escape
from typing import Any, Literal, TypeAlias, cast

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

from recon_gen.common.html._studio_assets.tw_classes import (
    chrome_button_classes,
    destructive_button_classes,
    entity_card_classes,
    field_input_classes,
    field_row_classes,
    primary_button_classes,
)
from recon_gen.common.html._components import (
    SortAxis,
    kind_label_plural,
    kind_label_singular,
    parse_toolbar_state,
    render_list_pager,
    render_list_search,
)
from recon_gen.common.html import _delete_confirm
from recon_gen.common.html._delete_confirm import (
    find_references as _find_references,
    render_delete_confirm_banner,
    verify_confirm_token,
)
from recon_gen.common.html._studio_routes import (
    asset_url,
    render_mini_diagram_html,
    studio_theme_head,
)
from recon_gen.common.html._side_panel import render_side_panel_trigger
from recon_gen.common.ids import GlossaryAnchor, HandbookPath, SurfaceAnchor
from recon_gen.common.l2.cache import L2InstanceCache
from recon_gen.common.l2.editor import (
    SINGLETON_KINDS,
    EntityKind,
    attach_rail_to_reconciler,
    create_l2_entity,
    delete_l2_entity,
    mutate_l2,
    rename_identifier,
    singleton_save_l2,
)
from recon_gen.common.l2.primitives import (
    FiringsTypicalPerPeriod,
    Identifier,
    Money,
    Name,
    Period,
    TwoLegRail,
)
from recon_gen.common.l2.validate import L2ValidationError, validate


# ---------------------------------------------------------------------------
# Field-spec dispatch — per-entity form layout
# ---------------------------------------------------------------------------


FieldKind: TypeAlias = Literal[
    "text", "select", "money", "textarea", "multi_select", "yaml_block",
    "multi_select_groups", "chain_children",
    "metadata_value_examples",  # BF.4 — inline-edit picker per locked P2
]

# X.4.f.11 — Rail is a discriminated union (TwoLegRail | SingleLegRail).
# A FieldSpec marked with one of these subtypes only renders / coerces
# when the entity matches; cross-subtype fields stay None.
RailSubtype: TypeAlias = Literal["two_leg", "single_leg"]


# CF.4.g — typed read-view rendering tag. Drives how
# `_render_read_value` formats a field's value on the read card. The
# Literal is closed; adding a new variant is a typed change that
# pyright catches at every `_render_read_value` branch (per
# `[feedback_invariants_in_types]`). Defaults to "text" — the safe
# escape-into-plain-text path that worked pre-CF.4.g.
RenderAs: TypeAlias = Literal[
    "text",        # plain escape (default; current behavior)
    "chip_list",   # tuple-of-identifiers → flex-wrap of <span> pills
                   # with `break-keep` so underscored ids don't wrap
                   # mid-token (operator complaint root cause)
    "monospace",   # render in `font-mono` (id-like values)
    "markdown",    # description-style prose, current `_render_read_value`
                   # markdown path (kept for completeness)
]


@dataclasses.dataclass(frozen=True, slots=True)
class FieldSpec:
    """One form field's render instructions.

    ``name`` is the dataclass field name (matches mutate_l2's
    ``fields`` dict key). ``label`` is what the operator sees;
    ``helper`` is a one-line hint shown under the input. ``kind``
    drives the input type — text / select / money / textarea /
    multi_select. ``options`` is the static option list for
    ``kind="select"``. ``select_from`` is the dynamic alternative —
    names a well-known cross-entity collection (``"roles"``,
    ``"rails"``, ``"rails_or_templates"``) that the renderer resolves
    from the current L2 instance. Mutually exclusive with
    ``options``; pick the right one for the field's source-of-truth.

    ``multi_select`` renders ``<select multiple>`` and submits as a
    repeated form key — used for tuple-typed dataclass fields like
    ``TransferTemplate.leg_rails``. The operator's selection IS the
    new value; an empty selection clears the field (and the validator
    decides whether that's acceptable per the L2 invariants).

    ``subtype_only`` (X.4.f.11) gates Rail fields that only exist on
    one arm of the discriminated union — e.g., TwoLegRail's
    ``source_role`` / ``destination_role`` vs SingleLegRail's
    ``leg_role`` / ``leg_direction``. The renderer skips fields whose
    ``subtype_only`` doesn't match the rail's actual subtype at edit
    time; on the create page, the subtype picker (X.4.f.11.5)
    determines which fields to show.
    """

    name: str
    label: str
    helper: str
    kind: FieldKind
    options: tuple[str, ...] = ()
    select_from: str | None = None
    required: bool = False
    subtype_only: RailSubtype | None = None
    # AB.3.7 — fields whose option universe references a sibling
    # dataclass field on the same entity (``leg_rail_xor_groups`` reads
    # the template's own ``leg_rails``). The entity must already exist
    # for this to make sense; the create page filters these out so the
    # operator authors the sibling field first, then edits to add the
    # group-shaped layer.
    edit_only: bool = False
    # BF.9 (2026-05-25) — textarea fields with markdown content get
    # an Edit / Preview tab affordance. The Preview tab HTMX-fetches
    # ``/preview/markdown`` with the current textarea value + swaps
    # in the rendered HTML. Limit to ``kind="textarea"`` per BF.0 L2
    # (free-form prose; description fields are the canonical use).
    preview_markdown: bool = False
    # CF.4.g — typed read-view rendering tag (closed Literal). See
    # ``RenderAs`` above. Default "text" preserves the pre-CF.4.g
    # behavior; per-kind ``_FIELD_SPECS_BY_KIND`` opts list-of-id
    # fields into "chip_list" so underscored identifiers stop
    # wrapping mid-token in the value column (operator complaint).
    render_as: RenderAs = "text"
    # CO.3 polish (2026-06-06) — optional placeholder attribute.
    # Useful for showing example syntax (e.g. "ach_trace_number,
    # wire_imad") AND for enabling :placeholder-shown CSS reactivity
    # on textarea fields whose downstream siblings hide when empty.
    placeholder: str | None = None
    # v13.6.1 finding 2 — when an option's display label should
    # differ from its raw value (e.g. the empty option labeled
    # "Sparse (default)" for `balance_cadence`'s unset state), the
    # spec carries a value→label mapping consulted by the select
    # renderer. Keys not present in the dict fall back to escaping
    # the raw value, preserving the pre-fix shape for FieldSpecs
    # that don't need relabeling.
    option_labels: Mapping[str, str] | None = None
    # CN.5a — optional pointer to a handbook page under
    # ``docs/handbook/`` (same shape as ``Sheet.handbook_path``;
    # `<area>/<topic>` without `.md`). When set, the renderer emits
    # a small `?` button next to the field label that opens the
    # handbook side panel via the same JS hook the dashboard `?`
    # buttons use. Reach for it when the field's helper line can't
    # carry the context the operator needs (validator semantics,
    # cross-entity dependencies, L1 invariant the field drives).
    handbook_path: HandbookPath | None = None
    # BX.12 — optional pointer to a GLOSSARY key in
    # ``common/html/_side_panel.py``. When set, the renderer emits a
    # small ``[?]`` button next to the field label that opens the
    # side-panel drawer with that single glossary entry pre-loaded.
    # Use this for vocabulary the field's helper line can't expand on
    # without ballooning ("XOR", "fan-in", "bundles activity") — the
    # helper stays one-line; the drawer carries the prose. An anti-
    # drift test (``test_side_panel_field_anchors``) pins every
    # anchor used here against the GLOSSARY dict so a typo breaks at
    # sessionstart, not at the first click.
    glossary_anchor: GlossaryAnchor | None = None


def _handbook_chip_html(spec: FieldSpec) -> str:
    """CN.5a — inline `?` chip emitted next to a field's label when
    its FieldSpec carries a ``handbook_path``. Reuses the dashboard
    `?` JS hook (matches by ``.handbook-help-button`` +
    ``data-handbook-path=...``) so one event-delegated handler in
    ``bootstrap.js`` services both surfaces."""
    if not spec.handbook_path:
        return ""
    href = escape(str(spec.handbook_path))
    return (
        f' <a href="/handbook/{href}" '
        f'class="handbook-help-button ml-1 inline-flex items-center '
        f'justify-center w-4 h-4 rounded-full border border-current '
        f'text-secondary-fg hover:text-accent hover:border-accent '
        f'text-[10px] font-semibold align-middle no-underline" '
        f'title="Open handbook for this field" '
        f'data-handbook-path="{href}">?</a>'
    )


def _glossary_chip_html(spec: FieldSpec) -> str:
    """BX.12 — inline ``[?]`` chip emitted next to a field's label when
    its FieldSpec carries a ``glossary_anchor``. Opens the side-panel
    drawer with the single matching glossary entry pre-loaded via the
    shared ``data-side-panel-trigger`` hook from ``_side_panel.py``.

    Separate from ``_handbook_chip_html`` because the two surfaces
    serve different audiences:

    - ``handbook_path`` (handbook) — deep prose for the L2 author /
      operator working through a topic-area.
    - ``glossary_anchor`` (side panel) — terse banker-readable
      definition of one vocabulary term, surfaced inline so the
      operator doesn't have to leave the form.

    A field can carry both (rare) — they render side-by-side. The
    anti-drift test pins every anchor against the GLOSSARY dict.
    """
    if not spec.glossary_anchor:
        return ""
    anchor = str(spec.glossary_anchor)
    target = f"/studio/side-panel/glossary/{anchor}"
    return " " + render_side_panel_trigger(
        target,
        label="?",
        aria_label=f"Open glossary entry for {spec.label}",
        extra_classes=(
            "ml-1 w-4 h-4 rounded-full border border-current "
            "text-secondary-fg hover:text-accent hover:border-accent "
            "text-[10px] font-semibold align-middle no-underline"
        ),
    )


def _surfaces_as_chip_html(
    anchor: SurfaceAnchor | None,
    *,
    field_label: str,
) -> str:
    """BX.13 — inline ``where?`` chip emitted next to a field's label when
    a ``SurfaceAnchor`` is wired for the field. Opens the side-panel
    drawer with the surfaces-as block (where the value will appear in
    user-facing surfaces — audit PDF / dashboard headers / chart
    accents / etc.).

    Sibling to ``_glossary_chip_html`` — separate visual chip + label
    so the operator can tell the two surfaces apart at a glance:

    - ``glossary_anchor`` → ``?`` chip — "what does this term mean?"
    - ``SurfaceAnchor`` → ``where?`` chip — "where does this value end up?"

    Takes ``anchor`` directly (not a FieldSpec) because the BXa singleton
    forms (instance / theme) are hand-rolled and don't route through the
    standard ``FieldSpec`` dispatcher; the chip needs to plug into those
    forms by anchor name + label. The anti-drift test pins every wired
    anchor against the ``SURFACES_AS`` dict.
    """
    if not anchor:
        return ""
    target = f"/studio/side-panel/surfaces-as/{escape(str(anchor))}"
    return " " + render_side_panel_trigger(
        target,
        label="where?",
        aria_label=f"See where {field_label} surfaces in dashboards / audit PDF",
        extra_classes=(
            "ml-1 px-1.5 py-0 rounded-sm border border-current "
            "text-secondary-fg hover:text-accent hover:border-accent "
            "text-[10px] font-semibold align-middle no-underline"
        ),
    )


def _prefixed_field_spec(spec: FieldSpec, prefix: str) -> FieldSpec:
    """BB.2 helper — clone a FieldSpec with the form name prefixed.

    The BB.2 create-new sub-form embeds a second copy of a TT or
    Rail's FieldSpec list inside the rail-create form, with every
    name prefixed by ``reconciler_new_`` so the outer rail form's
    fields and the inner reconciler-being-created fields don't
    collide on the wire. ``dataclasses.replace`` preserves every
    other FieldSpec attribute (kind, options, select_from, helper,
    subtype_only, etc.) so the rendered widget shape matches the
    edit page.
    """
    return dataclasses.replace(spec, name=f"{prefix}{spec.name}")


_ACCOUNT_FIELDS: tuple[FieldSpec, ...] = (
    # CO.3 polish (2026-06-06) — identity + display name + description
    # promoted to the top so the operator sees what they're editing
    # before scrolling into structural fields.
    FieldSpec(
        name="id",
        label="ID",
        helper="Unique identifier within this L2 instance.",
        kind="text",
        required=True,
    ),
    FieldSpec(
        name="name",
        label="Display name",
        helper="Human-readable label rendered in dashboards + the audit PDF.",
        kind="text",
    ),
    FieldSpec(
        name="description",
        label="Description",
        helper="Free-form prose; markdown OK. Read by handbook templates.",
        kind="textarea",
        preview_markdown=True,
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
        name="role",
        label="Role",
        helper=(
            "Classifier this account plays. Rails reference accounts by "
            "role — without one, this account can't participate in any "
            "flow. Multiple accounts can share a role (N:1 grouping)."
        ),
        kind="text",
        required=True,
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
    # CP — signed integer hours from midnight UTC for this account's
    # EOD cutoff. Empty / unset ⇒ midnight-aligned (default). Hidden via
    # CSS when scope=external (M.4.4.14a — external accounts have no
    # EOD balance, so the offset has no consumer).
    FieldSpec(
        name="business_day_offset",
        label="Business-day offset (hours)",
        helper=(
            "Hours from midnight UTC for this account's EOD cutoff. "
            "Positive = later (e.g., +9 for a Tokyo-style EOD); "
            "negative = earlier; range [-23, 23]. Leave blank for "
            "midnight-aligned. External accounts ignore this — we "
            "don't track their EOD balance."
        ),
        kind="text",
        placeholder="0",
    ),
    # CL — balance-reporting cadence; sparse (default) means the ETL feed
    # emits balance rows only on activity days; explicit_daily means
    # every business day MUST have a row (missing rows surface as L1
    # balance_cadence_gap violations). See CL.0 audit § 7.
    FieldSpec(
        name="balance_cadence",
        label="Balance cadence",
        helper=(
            "Sparse (default): balance rows arrive only on activity "
            "days; intervening days carry the prior balance forward. "
            "Explicit-daily: balance rows MUST arrive every business "
            "day (missing day = gap violation on L1 Exceptions)."
        ),
        kind="select",
        options=("", "sparse", "explicit_daily"),
        # v13.6.1 finding 2 — relabel the empty option so the
        # form makes the default explicit. Pre-fix the empty
        # `<option value="">` rendered with no display text +
        # the help line said "Sparse (default)" — the operator
        # had no way to tell the empty state resolved to sparse.
        option_labels={"": "Sparse (default)"},
    ),
)


_ACCOUNT_TEMPLATE_FIELDS: tuple[FieldSpec, ...] = (
    # CO.3 polish — role (identity) + description first.
    FieldSpec(
        name="role",
        label="Role",
        helper="Role this template's instances will play (e.g. CustomerSubledger).",
        kind="text",
        required=True,
    ),
    FieldSpec(
        name="description",
        label="Description",
        helper="Free-form prose; markdown OK.",
        kind="textarea",
        preview_markdown=True,
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
    # CP — same field as on the singleton Account form. Applies to every
    # instance the template materializes (Lock 4). Hidden via CSS when
    # scope=external for the same reason (M.4.4.14a).
    FieldSpec(
        name="business_day_offset",
        label="Business-day offset (hours)",
        helper=(
            "Hours from midnight UTC for instances of this template's "
            "EOD cutoff. Positive = later (e.g., +9 for Tokyo-style "
            "EOD); negative = earlier; range [-23, 23]. Leave blank "
            "for midnight-aligned."
        ),
        kind="text",
        placeholder="0",
    ),
    # CL — template-level cadence applies to every materialized instance.
    FieldSpec(
        name="balance_cadence",
        label="Balance cadence",
        helper=(
            "Sparse (default): instances emit balance rows only on "
            "activity days. Explicit-daily: instances MUST emit a "
            "row every business day (gaps surface on L1 Exceptions)."
        ),
        kind="select",
        options=("", "sparse", "explicit_daily"),
        # v13.6.1 finding 2 — relabel the empty option so the
        # form makes the default explicit. Pre-fix the empty
        # `<option value="">` rendered with no display text +
        # the help line said "Sparse (default)" — the operator
        # had no way to tell the empty state resolved to sparse.
        option_labels={"": "Sparse (default)"},
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
# editor's mutate_l2 dispatches on `dataclasses.replace`. X.4.f.11
# adds the load-bearing subtype-discriminating fields (source_role /
# destination_role on TwoLeg; leg_role / leg_direction on Single)
# gated by FieldSpec.subtype_only — the renderer + read card filter
# them based on the rail entity's actual subtype at edit time.
_RAIL_FIELDS: tuple[FieldSpec, ...] = (
    # CO.3 polish — name (identity) + description first.
    FieldSpec(
        name="name",
        label="Name",
        helper="Unique rail identifier; referenced by chains + templates.",
        kind="text",
        required=True,
    ),
    FieldSpec(
        name="description",
        label="Description",
        helper="Free-form prose; markdown OK.",
        kind="textarea",
        preview_markdown=True,
    ),
    # X.4.f.11.2 — TwoLegRail per-leg roles. RoleExpression is
    # tuple[Identifier, ...]; multi-select renders the list as a
    # union ("any of these roles is admissible at posting time").
    # Single-role rails select one option; the loader normalizes.
    FieldSpec(
        name="source_role",
        label="Source role",
        helper=(
            "Role of the account the debit leg posts to. Add one or "
            "more roles (union: any matches at posting time). Required "
            "on TwoLegRail."
        ),
        kind="multi_select",
        select_from="roles",
        required=True,
        subtype_only="two_leg",
    ),
    FieldSpec(
        name="destination_role",
        label="Destination role",
        helper=(
            "Role of the account the credit leg posts to. Add one or "
            "more roles (union: any matches at posting time). Required "
            "on TwoLegRail."
        ),
        kind="multi_select",
        select_from="roles",
        required=True,
        subtype_only="two_leg",
    ),
    # X.4.f.11.3 — SingleLegRail leg fields. leg_role is the same
    # RoleExpression shape; leg_direction picks the static enum.
    FieldSpec(
        name="leg_role",
        label="Leg role",
        helper=(
            "Role of the account the single leg posts to. Required "
            "on SingleLegRail."
        ),
        kind="multi_select",
        select_from="roles",
        required=True,
        subtype_only="single_leg",
    ),
    FieldSpec(
        name="leg_direction",
        label="Leg direction",
        helper=(
            "Debit (money out) / Credit (money in) / Variable "
            "(direction + amount determined by enclosing template's "
            "ExpectedNet at posting time). Required on SingleLegRail."
        ),
        kind="select",
        options=("Debit", "Credit", "Variable"),
        required=True,
        subtype_only="single_leg",
    ),
    FieldSpec(
        name="origin",
        label="Origin",
        helper="ExternalForcePosted / InternalInitiated. See SPEC's Origin table.",
        kind="text",
    ),
    # X.4.f.11.8 — TwoLeg per-leg Origin overrides + expected_net.
    # When the rail's two legs touch different Origin classes (e.g.,
    # external counterparty leg is ExternalForcePosted while internal
    # leg is InternalInitiated), set per-leg overrides. expected_net
    # is the standalone-firing balance contract (typically 0); leave
    # blank when this rail is only used as a TransferTemplate leg.
    FieldSpec(
        name="source_origin",
        label="Source origin (override)",
        helper="Per-leg override. Blank ⇒ use the rail-level Origin for both legs.",
        kind="text",
        subtype_only="two_leg",
        glossary_anchor=GlossaryAnchor("origin-overrides"),
    ),
    FieldSpec(
        name="destination_origin",
        label="Destination origin (override)",
        helper="Per-leg override. Blank ⇒ use the rail-level Origin for both legs.",
        kind="text",
        subtype_only="two_leg",
        glossary_anchor=GlossaryAnchor("origin-overrides"),
    ),
    FieldSpec(
        name="expected_net",
        label="Expected net (standalone firing)",
        helper=(
            "L1 Conservation contract for standalone firings (typically 0). "
            "Leave blank when this rail is only used as a TransferTemplate leg "
            "— the template owns the bundle's ExpectedNet."
        ),
        kind="money",
        subtype_only="two_leg",
    ),
    # CO.3 polish (2026-06-06) — reorder: aggregating + cadence +
    # bundles_activity grouped (the aggregating trio); metadata_keys +
    # metadata_value_examples grouped (keys + per-key examples).
    # X.4.f.11.4 — aggregating gate flag. When true, rail sweeps on
    # cadence and bundles_activity matters; when false (default), it
    # fires per-Transfer.
    FieldSpec(
        name="aggregating",
        label="Aggregating",
        helper=(
            "true ⇒ rail fires on cadence (sweep / batch) and the "
            "bundles_activity / cadence fields apply. false ⇒ fires "
            "per-Transfer."
        ),
        kind="select",
        options=("false", "true"),
    ),
    FieldSpec(
        name="cadence",
        label="Cadence",
        helper="For aggregating rails (e.g. intraday-2h / daily-eod).",
        kind="text",
        glossary_anchor=GlossaryAnchor("cadence"),
    ),
    # X.4.f.11.9 — bundles_activity (aggregating rails only).
    # tuple[BundlesActivityRef = Identifier, ...] — multi-select from
    # rails + templates; matches by Rail.name or TransferTemplate.name.
    FieldSpec(
        name="bundles_activity",
        label="Bundles activity",
        helper=(
            "For aggregating rails only. Names the rails / templates "
            "whose Transactions this rail bundles. Type below to add; "
            "drag to reorder; × removes."
        ),
        kind="multi_select",
        select_from="rails_or_templates",
        render_as="chip_list",  # CF.4.g — list of identifiers
        handbook_path=HandbookPath("l2-editor/bundles-activity"),
        glossary_anchor=GlossaryAnchor("bundles-activity"),
    ),
    # X.4.f.11.6 — metadata_keys + metadata_value_examples (paired).
    # BF.4 (2026-06-07) — chip-list picker; option universe is the
    # L2-wide union of declared metadata_keys + a canonical fallback
    # (ach_trace_number, wire_imad, swift_uetr, ...). Operator picks
    # from autocomplete or adds free-form text; the typeahead datalist
    # serves both shapes. tuple[Identifier, ...].
    FieldSpec(
        name="metadata_keys",
        label="Metadata keys",
        helper=(
            "Metadata key names this rail's transactions carry. Pick "
            "from the L2-wide list or type a new one (e.g. "
            "ach_trace_number, wire_imad)."
        ),
        kind="multi_select",
        select_from="metadata_keys",
        render_as="chip_list",  # CF.4.g — list of identifiers
        handbook_path=HandbookPath("l2-editor/metadata-keys"),
    ),
    # X.4.f.11.6.5 — Tier-3 metadata_value_examples.
    # BF.4 (2026-06-07, P2 lock) — inline-edit picker: one row per
    # sibling `metadata_keys` entry; values for that key entered as a
    # comma-separated list. ``edit_only`` because the picker needs
    # metadata_keys saved first (chicken-egg — same staged-edit
    # pattern as leg_rail_xor_groups).
    FieldSpec(
        name="metadata_value_examples",
        label="Metadata value examples",
        helper=(
            "Per-key example values the demo seed cycles through. "
            "One row per metadata key; enter values as a comma-"
            "separated list. Empty ⇒ uses synthetic per-rail "
            "fallback (e.g. `<rail>-firing-<seq>`)."
        ),
        kind="metadata_value_examples",
        edit_only=True,
    ),
    FieldSpec(
        name="posted_requirements",
        label="Posted requirements",
        helper=(
            "One per line. Rail-specific fields the L1 PostedRequirements "
            "view requires beyond the auto-derived TransferKey + chain-Required "
            "fields (see derived.posted_requirements_for)."
        ),
        kind="textarea",
        glossary_anchor=GlossaryAnchor("posted-requirements"),
    ),
    # X.4.f.11.7 — aging windows (Duration | None). ISO 8601 literal;
    # empty ⇒ None (no aging watch).
    FieldSpec(
        name="max_pending_age",
        label="Max pending age",
        helper=(
            "ISO 8601 duration (e.g. PT24H, PT4H, P1D). L1 Pending Aging "
            "flags any pending Transaction older than this. Empty ⇒ no watch."
        ),
        kind="text",
        placeholder="PT24H",
    ),
    FieldSpec(
        name="max_unbundled_age",
        label="Max unbundled age",
        helper=(
            "ISO 8601 duration (e.g. P3D). L1 Unbundled Aging flags any "
            "Transaction older than this without a bundling parent. "
            "Empty ⇒ no watch."
        ),
        kind="text",
        placeholder="P3D",
        handbook_path=HandbookPath("l2-editor/max-unbundled-age"),
    ),
    # AB.5 (E7) — soft per-firing magnitude bound. Operator types a
    # ``min, max`` shape (comma-separated decimals); coerce parses to
    # ``tuple[Money, Money] | None``. Validator V1a-c (min<max, both>0,
    # aggregating=false) surfaces inline.
    FieldSpec(
        name="amount_typical_range",
        label="Typical amount range (min, max)",
        helper=(
            "Optional soft bound on per-firing abs(amount). Format: "
            "`min, max` (e.g. `5.00, 500.00`). Generator samples "
            "log-uniformly within this range, producing realistic "
            "demo amounts. Validator V1a-c rejects min≥max, "
            "non-positive values, and aggregating rails. Empty ⇒ "
            "falls back to per-kind lognormal heuristic."
        ),
        kind="text",
        placeholder="5.00, 500.00",
    ),
    # AF (E8) — soft per-period firing-COUNT bound. Single composite
    # text input: `min, max` (period defaults business_day) OR
    # `period: min, max` (period ∈ business_day|pay_period|week|month).
    # Coerce parses to FiringsTypicalPerPeriod | None; validator W1a-c
    # (min≤max, both≥0, aggregating=false) surfaces inline.
    FieldSpec(
        name="firings_typical_per_period",
        label="Typical firings per period (min, max)",
        helper=(
            "Optional soft bound on how many times this rail fires per "
            "period. Format: `min, max` (defaults to per business day, "
            "e.g. `50, 500`) OR `period: min, max` where period is "
            "business_day | pay_period | week | month (e.g. "
            "`month: 80, 120`). Generator samples uniformly per period. "
            "Validator W1a-c rejects min>max, negatives, and aggregating "
            "rails. Empty ⇒ falls back to per-kind heuristic."
        ),
        kind="text",
        placeholder="50, 500",
    ),
)


# X.4.f.5 — Chain form (sub-list editor for required/xor children
# is X.4.f.5b; this first cut just edits the per-entry fields).
# X.4.f.10 — parent + child are now dropdowns of valid rail/template
# names (was free text; typo'd values reached the validator only).
_CHAIN_FIELDS: tuple[FieldSpec, ...] = (
    # CO.3 polish — parent (identity) + description first.
    FieldSpec(
        name="parent",
        label="Parent",
        helper=(
            "Rail or TransferTemplate this chain row attaches to. When the "
            "parent fires, the L1 layer expects one of the children below "
            "to follow."
        ),
        kind="select",
        select_from="rails_or_templates",
        required=True,
    ),
    FieldSpec(
        name="description",
        label="Description",
        helper="Free-form prose.",
        kind="textarea",
        preview_markdown=True,
    ),
    # AB.6.7 (2026-05-19) — per-child fan_in shape. The chain card
    # renders the children checkbox group with per-child fan_in +
    # expected_parent_count sub-inputs that submit only when the
    # corresponding child is checked. Coerce produces
    # tuple[ChainChildSpec, ...] directly. Replaces the AB.4.9
    # chain-level fan_in / expected_parent_count fields (removed at
    # AB.6.0 Lock 2 hard cut).
    FieldSpec(
        name="children",
        label="Children",
        helper=(
            "Rails / templates that may follow the parent. Z.A grammar: "
            "one selected = required (every parent firing MUST invoke "
            "it). Two+ selected = XOR alternation (exactly one fires "
            "per parent firing). For each selected child, the fan-in "
            "checkbox + expected-parent-count input let you opt that "
            "child into N:1 fan-in (validator C8a requires fan_in "
            "children to be TransferTemplates). Mixed-cardinality is "
            "supported: one child fan_in while siblings stay 1:1 XOR "
            "(AB.6 shape; sasquatch's MerchantSettlementCycle chain "
            "is the canonical demo). Empty selection is rejected."
        ),
        kind="chain_children",
        select_from="rails_or_templates",
        required=True,
        handbook_path=HandbookPath("l2-editor/chain-children"),
        # BX.12 — anchor to "xor" as the primary banker-facing concept
        # the children field expresses. fan-in + expected-parent-count
        # terms live in GLOSSARY and surface via the top-nav glossary
        # (the per-child fan-in checkbox lives inside the children
        # editor — operators see the term while editing, then can pop
        # the glossary for the definition).
        glossary_anchor=GlossaryAnchor("xor"),
    ),
)


# X.4.f.10 — TransferTemplate form, including the multi_select sub-list
# editor for ``leg_rails`` (Cmd/Ctrl-click to add or remove rails). The
# operator's submitted selection IS the new tuple; clearing all rails
# leaves an empty tuple which the validator rejects with "TransferTemplate
# must declare at least one leg_rail" — surface it inline.
_TRANSFER_TEMPLATE_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        name="name",
        label="Name",
        helper="Unique template identifier.",
        kind="text",
        required=True,
    ),
    # CO.3 polish — description right after name.
    FieldSpec(
        name="description",
        label="Description",
        helper="Free-form prose.",
        kind="textarea",
        preview_markdown=True,
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
        handbook_path=HandbookPath("l2-editor/completion-expression"),
    ),
    FieldSpec(
        name="leg_rails",
        label="Leg rails",
        helper=(
            "The Rails this template owns. Type below to add; drag to "
            "reorder; × removes. At least one rail required — add a "
            "replacement before removing the last, or delete the whole "
            "template instead."
        ),
        kind="multi_select",
        select_from="rails",
        required=True,
        render_as="chip_list",  # CF.4.g — list of identifiers
    ),
    # AI.2.b — transfer_key: the metadata-key field names that group leg
    # firings into one shared Transfer. tuple[Identifier, ...], same
    # textarea one-per-line shape as Rail.metadata_keys. NOT required:
    # the validator (R12) skips empty transfer_key and the loader only
    # requires the YAML key present (empty list is structurally valid),
    # so the asterisk would lie about the constraint — unlike leg_rails,
    # which the validator rejects when empty. Almost every real template
    # declares a non-empty one, and R12 requires each field to also
    # appear in every leg_rail's metadata_keys.
    FieldSpec(
        name="transfer_key",
        label="Transfer key",
        helper=(
            "Metadata-key field names whose matching values group the "
            "leg rails' firings into one shared Transfer (one per line, "
            "or comma-separated; e.g. disbursement_id). Each key MUST "
            "also be declared in every leg rail's metadata_keys "
            "(validator R12) — the library auto-derives them as "
            "PostedRequirements. Usually non-empty; blank ⇒ all firings "
            "of the leg rails join one Transfer."
        ),
        kind="textarea",
        handbook_path=HandbookPath("l2-editor/transfer-key"),
    ),
    # AB.3.7 — Variable-rail XOR groups. Each group is a multi-select
    # whose option universe is this template's own ``leg_rails``. The
    # operator gets one row per existing group plus a trailing blank
    # row for adding a new group; unchecking every box in a group
    # removes it on save. Hidden on the create page (the operator
    # authors ``leg_rails`` first, then edits to add the group layer)
    # via ``edit_only=True``.
    FieldSpec(
        name="leg_rail_xor_groups",
        label="Variable rail XOR groups",
        helper=(
            "Groups of Variable-direction leg rails that are mutually "
            "exclusive per template firing — exactly ONE member of "
            "each group fires per cycle (per-firing pick is "
            "deterministic). Each group needs ≥2 members, all members "
            "must be in this template's leg_rails, all must be "
            "Variable-direction SingleLegRails, and no rail may appear "
            "in two groups (validator C1a-d). Uncheck every box in a "
            "group to drop it on save."
        ),
        kind="multi_select_groups",
        select_from="self_leg_rails",
        edit_only=True,
        handbook_path=HandbookPath("l2-editor/leg-rail-xor-groups"),
    ),
    # AF (E8) — soft per-period firing-COUNT bound for the template's
    # shared Transfer (honored when the template is a chain parent —
    # see _emit_baseline_template_firings). Same composite text shape as
    # the Rail field. Validator W1a-b (no aggregating exclusion —
    # templates aren't aggregating rails).
    FieldSpec(
        name="firings_typical_per_period",
        label="Typical firings per period (min, max)",
        helper=(
            "Optional soft bound on how many times this template's "
            "shared Transfer fires per period (honored when the template "
            "is a chain parent). Format: `min, max` (defaults per "
            "business day) OR `period: min, max` where period is "
            "business_day | pay_period | week | month. Validator W1a-b "
            "rejects min>max and negatives. Empty ⇒ one firing per "
            "business day when this template is a chain parent."
        ),
        kind="text",
        placeholder="50, 500",
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
    # CO.3 polish — description right after the lead identity field.
    FieldSpec(
        name="description",
        label="Description",
        helper="Free-form prose.",
        kind="textarea",
        preview_markdown=True,
    ),
    FieldSpec(
        name="rail",
        label="Rail",
        helper="The rail the cap applies to.",
        kind="select",
        select_from="rails",
        required=True,
    ),
    FieldSpec(
        name="cap",
        label="Cap",
        helper="Daily $ cap. L1 Limit Breach flags any day exceeding this.",
        kind="money",
        required=True,
    ),
    # AB.1 (2026-05-19) — per-direction cap. Outbound is the default
    # (classic per-rail send cap); Inbound is the AML / structuring
    # threshold on inbound volume. The same (parent_role, rail) pair
    # may carry both — duplicate detection broadened to the triple.
    FieldSpec(
        name="direction",
        label="Direction",
        helper=(
            "Which flow side the cap watches. Outbound = money leaving "
            "the parent's children (classic send cap). Inbound = money "
            "arriving (AML / structuring threshold)."
        ),
        kind="select",
        options=("Outbound", "Inbound"),
        required=True,
        glossary_anchor=GlossaryAnchor("direction"),
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
    field type is non-trivial (Decimal for money; bool for true/false
    selects).
    """
    raw = raw.strip()
    if raw == "":
        return None
    if spec.kind == "money":
        from decimal import Decimal
        return Money(Decimal(raw))
    # X.4.f.11.4 — Rail.aggregating gate flag.
    if spec.name == "aggregating" and kind == "rail":
        return raw.lower() == "true"
    # AB.4.9 — Chain.fan_in gate flag.
    if spec.name == "fan_in" and kind == "chain":
        return raw.lower() == "true"
    # AB.4.9 — Chain.expected_parent_count: int | None. Empty → None
    # handled above; non-empty parses as int (rejects non-numeric).
    if spec.name == "expected_parent_count" and kind == "chain":
        try:
            return int(raw)
        except ValueError as exc:
            raise ValueError(
                f"expected_parent_count must be an integer (got "
                f"{raw!r})",
            ) from exc
    # CP — Account / AccountTemplate.business_day_offset: signed int
    # in [-23, 23]. Range guard fires at the dataclass __post_init__;
    # this coercion just narrows string → int and rejects non-numeric.
    if spec.name == "business_day_offset" and kind in ("account", "account_template"):
        try:
            return int(raw)
        except ValueError as exc:
            raise ValueError(
                f"business_day_offset must be an integer (got {raw!r})",
            ) from exc
    # CL — Account / AccountTemplate.balance_cadence: closed Literal.
    # Loader validates the value range; this coercion just rejects
    # off-Literal strings before they reach the dataclass constructor.
    if spec.name == "balance_cadence" and kind in ("account", "account_template"):
        if raw not in ("sparse", "explicit_daily"):
            raise ValueError(
                f"balance_cadence must be 'sparse' or 'explicit_daily' "
                f"(got {raw!r})"
            )
        return raw
    # X.4.f.11.6 — Rail.posted_requirements: textarea one-per-line
    # (or comma-separated). tuple[Identifier, ...]. BF.4 narrowed
    # this from also covering `metadata_keys`, which moved to a
    # multi_select chip-list; the multi_select branch of
    # `_coerce_form` handles its coercion directly via `getlist`.
    if spec.name == "posted_requirements" and kind == "rail":
        parts = [
            p.strip()
            for line in raw.splitlines()
            for p in line.split(",")
            if p.strip()
        ]
        return tuple(Identifier(p) for p in parts)
    # AI.2.b — TransferTemplate.transfer_key: same textarea one-per-line
    # (or comma-separated) shape as Rail.metadata_keys. tuple[Identifier,
    # ...]. Empty handled above by the early return (⇒ None → () in the
    # create path); non-empty splits + coerces here.
    if spec.name == "transfer_key" and kind == "transfer_template":
        parts = [
            p.strip()
            for line in raw.splitlines()
            for p in line.split(",")
            if p.strip()
        ]
        return tuple(Identifier(p) for p in parts)
    # AB.5 (E7) — Rail.amount_typical_range: tuple[Money, Money] | None.
    # Operator types `min, max` as a comma-separated pair of decimals.
    # Empty handled above by early return.
    if spec.name == "amount_typical_range" and kind == "rail":
        from decimal import Decimal, InvalidOperation  # noqa: PLC0415 — lazy
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) != 2:
            raise ValueError(
                f"amount_typical_range expects `min, max` "
                f"(comma-separated); got {raw!r}",
            )
        try:
            return (Money(Decimal(parts[0])), Money(Decimal(parts[1])))
        except InvalidOperation as exc:
            raise ValueError(
                f"amount_typical_range expects numeric values; "
                f"got {raw!r}",
            ) from exc
    # AF (E8) — firings_typical_per_period: FiringsTypicalPerPeriod | None
    # on both Rail and TransferTemplate. Composite text shape:
    #   `min, max`            → period defaults business_day
    #   `period: min, max`    → explicit period
    # Empty handled above by the early return. Validator W1a-c fires on
    # the coerced value via the PUT handler's validate() pass.
    if spec.name == "firings_typical_per_period":
        from recon_gen.common.l2.loader import _load_period  # noqa: PLC0415 — lazy to dodge cycle
        period: Period = "business_day"
        range_part = raw
        if ":" in raw:
            period_str, range_part = raw.split(":", 1)
            # _load_period validates against the bounded enum + raises
            # L2LoaderError on an unknown period; surface as ValueError
            # so the form re-renders with the inline message.
            try:
                period = _load_period(
                    period_str.strip(), path="firings_typical_per_period.period",
                )
            except ValueError as exc:
                raise ValueError(str(exc)) from exc
        parts = [p.strip() for p in range_part.split(",")]
        if len(parts) != 2:
            raise ValueError(
                f"firings_typical_per_period expects `min, max` or "
                f"`period: min, max`; got {raw!r}",
            )
        try:
            lo, hi = int(parts[0]), int(parts[1])
        except ValueError as exc:
            raise ValueError(
                f"firings_typical_per_period counts must be integers; "
                f"got {raw!r}",
            ) from exc
        return FiringsTypicalPerPeriod(period=period, count_range=(lo, hi))
    # X.4.f.11.7 — Rail aging windows: Duration | None. Reuse the
    # loader's ISO 8601 parser; empty handled above by the early return.
    if spec.name in ("max_pending_age", "max_unbundled_age") and kind == "rail":
        from recon_gen.common.l2.loader import (  # noqa: PLC0415 — lazy to dodge cycle
            _load_duration,
        )
        return _load_duration(raw, path=spec.name)
    # X.4.f.11.6.5 — yaml_block coerce. Parse the operator's YAML,
    # validate the shape (dict[str, list[str]]), wrap to the nested
    # tuple-of-tuples. Bad YAML / wrong shape → ValueError → form
    # re-renders with operator's typed content + inline error.
    if spec.kind == "yaml_block":
        import yaml  # noqa: PLC0415 — lazy
        try:
            parsed = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise ValueError(f"Invalid YAML: {exc}") from exc
        if parsed is None or parsed == {}:
            return ()
        if not isinstance(parsed, dict):
            raise ValueError(
                f"Expected a YAML map (key → [list of strings]); "
                f"got {type(parsed).__name__}",
            )
        result: list[tuple[Identifier, tuple[str, ...]]] = []
        # WHY suppress: yaml.safe_load returns Any-typed dict; the
        # per-line cascade hits the inner str() + type().__name__ calls too.
        for k, v in parsed.items():  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]: yaml.safe_load dict carries Any-typed entries
            if not isinstance(v, list):
                raise ValueError(
                    f"Key {k!r}: expected a list of strings, "
                    f"got {type(v).__name__}",  # pyright: ignore[reportUnknownArgumentType]: yaml-derived v has Any type
                )
            result.append((
                Identifier(str(k)),  # pyright: ignore[reportUnknownArgumentType]: yaml-derived k stringifies safely
                tuple(str(item) for item in v),  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]: list element type from yaml is Any
            ))
        return tuple(result)
    if spec.name in ("id", "role", "parent_role", "parent", "name"):
        # Account.name is Name; everything else identifier-shaped is Identifier.
        # Both are runtime str, so the choice is annotation-only.
        if kind == "account" and spec.name == "name":
            return Name(raw)
        return Identifier(raw)
    return raw


def _coerce_form(
    kind: EntityKind,
    form: Any,  # typing-smell: ignore[explicit-any]: starlette FormData; structural - has __contains__/getlist/__getitem__ but the stub type pulls in deps
) -> tuple[dict[str, object], dict[str, str | tuple[str, ...]]]:
    """Walk the kind's FieldSpec list, coerce each submitted value.

    Returns ``(typed_fields, raw_overrides)``. The typed dict is what
    mutate_l2 / create_l2_entity consume; the overrides dict preserves
    raw form values (string for scalar fields, tuple-of-strings for
    multi_select) so the validation-failure path can re-render with
    the operator's typed-but-invalid input intact.

    multi_select fields use the form's ``getlist`` to grab repeated
    keys; a hidden ``<name>__present`` marker lets us distinguish
    "field rendered with empty selection" (clear leg_rails) from
    "field absent" (no change). Scalar fields skip on absence.
    """
    specs = _FIELD_SPECS_BY_KIND[kind]
    fields: dict[str, object] = {}
    overrides: dict[str, str | tuple[str, ...]] = {}
    for spec in specs:
        if spec.kind == "chain_children":
            # AB.6.7 — chain children submit as: `children=<name>` per
            # checked box + `fan_in_<name>=true` per checked fan-in +
            # `epc_<name>=<int>` per filled epc input. Build the
            # ChainChildSpec tuple by joining the three streams on name.
            if f"{spec.name}__present" not in form and spec.name not in form:
                continue
            from recon_gen.common.l2.primitives import (  # noqa: PLC0415
                ChainChildSpec,
                Identifier,
            )
            # CO.3 polish — chain_children is now also chip-list-as-primary.
            # Each chip carries its hidden `<input name=children value=X>`
            # along with the fan_in checkbox + epc number input, so chip
            # DOM order = canonical order and getlist preserves it. No
            # `__order` override needed.
            selected_names = tuple(
                str(v) for v in form.getlist("children") if str(v).strip()
            )
            overrides[spec.name] = selected_names
            child_specs: list[ChainChildSpec] = []
            for name in selected_names:
                fan_in_raw = form.get(f"fan_in_{name}")
                fan_in = (
                    str(fan_in_raw).lower() == "true"
                    if fan_in_raw is not None else False
                )
                epc_raw = form.get(f"epc_{name}", "")
                epc: int | None = None
                if str(epc_raw).strip():
                    try:
                        epc = int(str(epc_raw))
                    except ValueError:
                        # Surface as a typed L2ValidationError downstream
                        # rather than fail silently — _coerce_field's
                        # ValueError raise pattern but routed via the
                        # chain shape. Keep the raw on the override so
                        # the failure-rerender shows the operator's input.
                        epc = None
                child_specs.append(ChainChildSpec(
                    name=Identifier(name),
                    fan_in=fan_in,
                    expected_parent_count=epc,
                ))
            fields[spec.name] = tuple(child_specs)
        elif spec.kind == "multi_select":
            if f"{spec.name}__present" not in form and spec.name not in form:
                continue
            # CO.3 — chip-list-as-primary widget: hidden chip inputs
            # are emitted in chip DOM order = user intent order, and
            # getlist preserves DOM order, so the sequence is canonical
            # without any `__order` override. The CO.2 `__order` field
            # is gone; the field-level `__present` marker (above the
            # multi_select coercer in `_coerce_form`) still
            # distinguishes empty-from-absent.
            raw_list = tuple(
                str(v) for v in form.getlist(spec.name) if str(v).strip()
            )
            overrides[spec.name] = raw_list
            # Identifier-typed list per FieldSpec convention; the
            # specific dataclass field decides the inner type but
            # leg_rails is the only multi_select today and it's
            # tuple[Identifier, ...].
            from recon_gen.common.l2.primitives import (  # noqa: PLC0415
                Identifier,
            )
            fields[spec.name] = tuple(Identifier(v) for v in raw_list)
        elif spec.kind == "metadata_value_examples":
            # BF.4-followup (2026-06-07) — the picker now lives inline
            # inside each metadata_keys chip. Wire shape:
            #   <name>__present=1     (from the metadata_keys widget)
            #   <name>__for_<key>=<comma-separated values>  (one per chip)
            # The submitted metadata_keys list (same form payload)
            # drives which keys to look up. Empty per-key value list
            # drops that key (matches the locked #2 behavior — only
            # keys with actual values land in the L2 yaml).
            if f"{spec.name}__present" not in form:
                continue
            from recon_gen.common.l2.primitives import (  # noqa: PLC0415
                Identifier,
            )
            # The metadata_keys field's name may be prefixed (BB.2's
            # `reconciler_new_metadata_keys`) — recover by swapping
            # `metadata_value_examples` → `metadata_keys` in this
            # spec's name.
            keys_field_name = spec.name.replace(
                "metadata_value_examples", "metadata_keys",
            )
            keys_submitted: list[str] = [
                str(v).strip()
                for v in form.getlist(keys_field_name)
                if str(v).strip()
            ]
            assembled: list[tuple[Identifier, tuple[str, ...]]] = []
            override_assembled: list[tuple[str, tuple[str, ...]]] = []
            for key in keys_submitted:
                vals_raw = str(
                    form.get(f"{spec.name}__for_{key}") or "",
                )
                parts = tuple(
                    p.strip() for p in vals_raw.split(",") if p.strip()
                )
                if not parts:
                    continue
                assembled.append((Identifier(key), parts))
                override_assembled.append((key, parts))
            fields[spec.name] = tuple(assembled)
            overrides[spec.name] = tuple(  # pyright: ignore[reportArgumentType]: overrides dict stores tuple[(str, tuple[str, ...]), ...] for this kind; outer Mapping isn't nested-tuple-aware
                override_assembled,
            )
        elif spec.kind == "multi_select_groups":
            # AB.3.7 — repeated keys per group: ``<name>_0``, ``<name>_1``,
            # ... A hidden ``<name>__num_groups`` tells the server how
            # many group slots were rendered (operator can author up
            # to N groups + the trailing blank slot). Empty groups
            # (operator unchecked every box) are filtered server-side
            # — that's the "remove this group" UX.
            if f"{spec.name}__present" not in form:
                continue
            num_groups_raw = form.get(
                f"{spec.name}__num_groups", "0",
            )
            try:
                num_groups = int(str(num_groups_raw) or "0")
            except ValueError:
                num_groups = 0
            override_groups: list[tuple[str, ...]] = []
            field_groups: list[tuple[Identifier, ...]] = []
            from recon_gen.common.l2.primitives import (  # noqa: PLC0415
                Identifier,
            )
            for i in range(num_groups):
                raw_group = tuple(
                    str(v)
                    for v in form.getlist(f"{spec.name}_{i}")
                    if str(v).strip()
                )
                if not raw_group:
                    continue
                override_groups.append(raw_group)
                field_groups.append(
                    tuple(Identifier(v) for v in raw_group),
                )
            # _value_to_input_str doesn't know how to render nested
            # tuple-of-tuples; the render path branches on field kind
            # and reads override directly, so we can store the
            # tuple-of-tuples shape here without coercion.
            overrides[spec.name] = tuple(  # pyright: ignore[reportArgumentType]  # WHY: overrides dict stores tuple[tuple[str, ...], ...] for this kind; outer typing.Mapping isn't nested-tuple-aware
                override_groups,
            )
            fields[spec.name] = tuple(field_groups)
        else:
            if spec.name not in form:
                continue
            raw = str(form[spec.name])
            # CO.3 (2026-06-06) — normalize CRLF to LF at the form
            # boundary. Browsers submit `<textarea>` content with
            # `\r\n` line endings per the HTTP form spec; the L2 yaml
            # canonical form uses LF, so the round-trip would diverge
            # on multi-line descriptions. Surfaced by the spec_example
            # dogfood test against Chain.description fields.
            raw = raw.replace("\r\n", "\n").replace("\r", "\n")
            overrides[spec.name] = raw
            fields[spec.name] = _coerce_field(spec, raw, kind)
    return fields, overrides


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


def _rail_subtype_of(entity: object) -> RailSubtype | None:
    """Derive a rail entity's subtype for FieldSpec.subtype_only filtering.

    Returns ``"two_leg"`` for ``TwoLegRail``, ``"single_leg"`` for
    ``SingleLegRail``, ``None`` for any other entity (caller skips the
    subtype filter when None).
    """
    from recon_gen.common.l2.primitives import (  # noqa: PLC0415 — lazy to dodge cycle
        SingleLegRail,
        TwoLegRail,
    )
    if isinstance(entity, TwoLegRail):
        return "two_leg"
    if isinstance(entity, SingleLegRail):
        return "single_leg"
    return None


def _filter_specs_by_subtype(
    specs: tuple[FieldSpec, ...], subtype: RailSubtype | None,
) -> tuple[FieldSpec, ...]:
    """Drop FieldSpecs whose ``subtype_only`` doesn't match the given
    subtype. ``subtype=None`` means "show only subtype-agnostic
    fields" (the safe default for non-rail entities)."""
    if subtype is None:
        return tuple(s for s in specs if s.subtype_only is None)
    return tuple(
        s for s in specs
        if s.subtype_only is None or s.subtype_only == subtype
    )


def _filter_specs_for_entity(
    specs: tuple[FieldSpec, ...], entity: object,
) -> tuple[FieldSpec, ...]:
    """Drop FieldSpecs whose ``subtype_only`` doesn't match this entity's
    actual rail subtype. Non-rail entities pass through untouched."""
    return _filter_specs_by_subtype(specs, _rail_subtype_of(entity))


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
    if select_from == "rails":
        # Rail names — used by TransferTemplate.leg_rails. multi_select
        # so the operator picks one or many. Empty option not needed
        # for multi_select (zero selection IS the "empty" state).
        names: set[str] = set()
        for r in getattr(instance, "rails", ()):
            n = getattr(r, "name", None)
            if n is not None and str(n):
                names.add(str(n))
        opts = tuple(sorted(names))
        if current_value and current_value not in opts:
            opts = (*opts, current_value)
        return opts, False
    if select_from == "metadata_keys":
        # BF.4 (2026-06-07) — L2-wide union of declared Rail.metadata_keys
        # + a small canonical fallback so a first-time operator sees common
        # keys (ach_trace_number, wire_imad, swift_uetr, ...) even when no
        # rail declares any yet. Multi_select widget — empty option not
        # needed (zero selection IS the empty state).
        canonical: tuple[str, ...] = (
            "ach_trace_number",
            "wire_imad",
            "swift_uetr",
            "card_auth_id",
            "check_serial",
            "disbursement_id",
            "settlement_batch_id",
        )
        keys: set[str] = set(canonical)
        for r in getattr(instance, "rails", ()):
            for k in (getattr(r, "metadata_keys", ()) or ()):
                if k is not None and str(k):
                    keys.add(str(k))
        opts = tuple(sorted(keys))
        if current_value and current_value not in opts:
            opts = (*opts, current_value)
        return opts, False
    if select_from == "rails_or_templates":
        # Union of Rail.name + TransferTemplate.name — used by
        # Chain.parent / .children entries. A Chain row references
        # either a rail (e.g. "ACHReturnLeg") or a template (e.g.
        # "ExternalReconciliationCycle") interchangeably; the typed L2
        # graph disambiguates by membership in either collection.
        rails: set[str] = set()
        for r in getattr(instance, "rails", ()):
            n = getattr(r, "name", None)
            if n is not None and str(n):
                rails.add(str(n))
        for t in getattr(instance, "transfer_templates", ()):
            n = getattr(t, "name", None)
            if n is not None and str(n):
                rails.add(str(n))
        opts = tuple(sorted(rails))
        if current_value and current_value not in opts:
            opts = (*opts, current_value)
        return opts, True
    raise ValueError(f"Unknown select_from source: {select_from!r}")


def _resolve_grouped_roles(
    instance: Any,  # typing-smell: ignore[explicit-any]: L2Instance type concrete import creates circular dep with common.l2
    current_value: str,
) -> tuple[tuple[tuple[str, tuple[str, ...]], ...], bool]:
    """CG.22 (2026-06-05) — partition the "roles" option universe
    for the `parent_role` field into operator-meaningful groups so
    a first-time operator can see WHICH roles are parent-eligible
    without reading the SPEC.

    Per SPEC + `common/l2/primitives.py::Account` docstring:
    "Account is a 1-of-1 account that exists exactly once in the
    institution"; `parent_role` MUST resolve to a singleton Account
    (validator rejects template-role parents). So the eligible
    universe is exactly `{a.role for a in instance.accounts}`; the
    template-only roles surface under "Template roles (not eligible)"
    so the operator can SEE what's not valid without losing the
    at-a-glance scan. A current-value not present in either set
    (stale post-delete, hand-edited YAML) lands in a "Stale (review)"
    group so the operator notices + corrects instead of having it
    silently vanish.

    Returns ((group_label, sorted_roles), ...) + allow_empty=True
    (parent_role is always optional — empty = "this is a top-level
    singleton, no parent").
    """
    singleton_roles: set[str] = set()
    for a in getattr(instance, "accounts", ()):
        r = getattr(a, "role", None)
        if r is not None and str(r):
            singleton_roles.add(str(r))
    template_roles: set[str] = set()
    for t in getattr(instance, "account_templates", ()):
        r = getattr(t, "role", None)
        if r is not None and str(r):
            template_roles.add(str(r))
    template_only = template_roles - singleton_roles
    stale: set[str] = set()
    if (
        current_value
        and current_value not in singleton_roles
        and current_value not in template_roles
    ):
        stale.add(current_value)
    groups: list[tuple[str, tuple[str, ...]]] = []
    if singleton_roles:
        groups.append(
            ("Singleton parents (eligible)", tuple(sorted(singleton_roles))),
        )
    if template_only:
        groups.append(
            ("Template roles (not eligible)", tuple(sorted(template_only))),
        )
    if stale:
        groups.append(("Stale (review)", tuple(sorted(stale))))
    return tuple(groups), True


def _render_field(
    spec: FieldSpec,
    value: object,
    instance: Any,  # typing-smell: ignore[explicit-any]: L2Instance — needed to resolve select_from at render time
    error: str | None = None,
    *,
    entity: object | None = None,
) -> str:
    """One form-field <div> with label + input + helper + (optional) error.

    The error fragment slot lets the X.4.e.5 validation-failure path
    render per-field validator errors inline without losing the
    user's typed content. ``entity`` is the dataclass being edited
    (None on the create page); used by AB.3.7's
    ``multi_select_groups`` to read a sibling field for the option
    universe (e.g., ``leg_rail_xor_groups`` reads ``entity.leg_rails``).
    """
    # AM.1 step 4 (2026-05-25) — semantic classes migrated to raw
    # Tailwind utilities per L1+L2; helpers absorb the 30+× repeat
    # patterns (`field_row_classes` / `field_input_classes`) per L2.a.
    input_cls = field_input_classes()
    label = (
        f'<label for="field-{spec.name}" class="font-semibold text-xs text-primary-fg">{escape(spec.label)}'
        f'{"<span class=\"text-danger\"> *</span>" if spec.required else ""}'
        f"{_handbook_chip_html(spec)}"
        f"{_glossary_chip_html(spec)}"
        f"</label>"
    )
    helper = (
        f'<small class="text-xs text-secondary-fg">{escape(spec.helper)}</small>'
        if spec.helper else ""
    )
    err_html = (
        f'<div role="alert" class="text-xs text-danger bg-red-50 px-2 py-1 rounded-sm">{escape(error)}</div>'
        if error else ""
    )

    if spec.kind == "multi_select_groups":
        return _render_multi_select_groups_field(
            spec, value, entity, error,
        )

    if spec.kind == "chain_children":
        return _render_chain_children_field(spec, value, instance, error)

    if spec.kind == "metadata_value_examples":
        return _render_metadata_value_examples_field(
            spec, value, entity, error,
        )

    if spec.kind == "multi_select":
        # CO.3 polish (2026-06-06) — chip-list-as-primary widget.
        # Pre-rewrite: a checkbox grid showed the whole universe + a
        # chip list above showed the selected-in-order set. Operators
        # had to (a) scroll through the grid to find the rail they
        # wanted, (b) realize the chip list was the order-of-record,
        # and (c) un-check below to remove (no signal at the chip).
        # Post-rewrite: the chip list IS the widget. Each chip carries
        # its own hidden `<name>=<value>` input so the form submission
        # contract is unchanged (`getlist(name)` works). Drag-reorder
        # via Sortable.js. Delete via the × button on each chip. Add
        # via the typeahead `<input list>` below, which surfaces only
        # not-yet-selected options from the universe.
        if spec.select_from is None:
            raise ValueError(
                f"multi_select FieldSpec {spec.name!r} requires select_from",
            )
        options, _ = _resolve_select_options(spec.select_from, instance, "")
        selected = _multi_value_as_strs(value)
        # Defensive: any current value not in the option set still
        # shows (stale reference; validator surfaces the broken ref
        # separately).
        for v in selected:
            if v not in options:
                options = (*options, v)
        # Datalist contains only the unselected options — when the
        # operator adds a chip, the corresponding option is removed
        # from the datalist by the bootstrap JS. The initial render
        # pre-filters so the first-time view is correct without JS.
        datalist_id = f"field-{escape(spec.name)}-options"
        datalist_html = (
            f'<datalist id="{datalist_id}">'
            + "".join(
                f'<option value="{escape(o)}"></option>'
                for o in options if o not in selected
            )
            + "</datalist>"
        )
        chip_li_cls = (
            "flex items-center gap-2 bg-link-tint border border-surface-border "
            "rounded-sm px-2 py-1 text-sm"
        )
        chip_remove_cls = (
            "px-2 py-0.5 text-secondary-fg hover:text-danger hover:bg-red-50 "
            "rounded-sm cursor-pointer text-base leading-none"
        )
        # BF.4-followup (2026-06-07) — when this multi_select IS the
        # metadata_keys picker, each chip also carries an inline text
        # input for the value-examples (comma-separated). Collapses
        # the two-field UX (separate metadata_value_examples picker)
        # into one widget. The inline input's name uses the prefix-
        # aware swap `metadata_keys` → `metadata_value_examples`, so
        # BB.2's `reconciler_new_` prefix flows through correctly.
        is_metadata_keys = spec.select_from == "metadata_keys"
        examples_field_name = spec.name.replace(
            "metadata_keys", "metadata_value_examples",
        )
        values_by_key: dict[str, tuple[str, ...]] = {}
        if is_metadata_keys and entity is not None:
            raw_examples = (
                getattr(entity, "metadata_value_examples", ()) or ()
            )
            values_by_key = _metadata_value_examples_as_dict(raw_examples)

        def _chip_html(key: str) -> str:
            if is_metadata_keys:
                existing_vals = ", ".join(values_by_key.get(key, ()))
                inline_input_cls = (
                    "px-1.5 py-0.5 text-xs border border-surface-border "
                    "rounded-sm font-mono bg-white grow min-w-0"
                )
                return (
                    f'<li class="{chip_li_cls}" data-multiselect-order-value="{escape(key)}">'
                    f'<span aria-hidden="true" class="cursor-grab text-secondary-fg select-none">⋮</span>'
                    f'<input type="hidden" name="{escape(spec.name)}" value="{escape(key)}">'
                    f'<span class="font-mono text-xs whitespace-nowrap">{escape(key)}</span>'
                    f'<input type="text" '
                    f'name="{escape(examples_field_name)}__for_{escape(key)}" '
                    f'value="{escape(existing_vals)}" '
                    f'placeholder="example1, example2, ..." '
                    f'aria-label="Example values for {escape(key)}" '
                    f'class="{inline_input_cls}">'
                    f'<button type="button" class="{chip_remove_cls}" '
                    f'aria-label="Remove {escape(key)}" '
                    f'data-multiselect-remove="{escape(key)}">&times;</button>'
                    f'</li>'
                )
            return (
                f'<li class="{chip_li_cls}" data-multiselect-order-value="{escape(key)}">'
                f'<span aria-hidden="true" class="cursor-grab text-secondary-fg select-none">⋮</span>'
                f'<input type="hidden" name="{escape(spec.name)}" value="{escape(key)}">'
                f'<span class="grow">{escape(key)}</span>'
                f'<button type="button" class="{chip_remove_cls}" '
                f'aria-label="Remove {escape(key)}" '
                f'data-multiselect-remove="{escape(key)}">&times;</button>'
                f'</li>'
            )

        chips_html = "".join(_chip_html(s) for s in selected)
        # BF.4-followup — `<template>` element drives JS-built chips
        # for newly-typed keys. `__NAME__` literals get substituted
        # by the existing `buildChipFromTemplate` infrastructure
        # (multi_select bootstrap JS).
        chip_template_html = ""
        chip_template_id = ""
        present_marker_html = ""
        if is_metadata_keys:
            inline_input_cls = (
                "px-1.5 py-0.5 text-xs border border-surface-border "
                "rounded-sm font-mono bg-white grow min-w-0"
            )
            chip_template_id = f"field-{escape(spec.name)}-chip-template"
            chip_template_html = (
                f'<template id="{chip_template_id}">'
                f'<li class="{chip_li_cls}" data-multiselect-order-value="__NAME__">'
                f'<span aria-hidden="true" class="cursor-grab text-secondary-fg select-none">⋮</span>'
                f'<input type="hidden" name="{escape(spec.name)}" value="__NAME__">'
                f'<span class="font-mono text-xs whitespace-nowrap">__NAME__</span>'
                f'<input type="text" '
                f'name="{escape(examples_field_name)}__for___NAME__" '
                f'value="" '
                f'placeholder="example1, example2, ..." '
                f'aria-label="Example values for __NAME__" '
                f'class="{inline_input_cls}">'
                f'<button type="button" class="{chip_remove_cls}" '
                f'aria-label="Remove __NAME__" '
                f'data-multiselect-remove="__NAME__">&times;</button>'
                f'</li>'
                f'</template>'
            )
            # Present marker for the sibling field's coerce branch.
            # When this hidden input lands in the form payload, the
            # server's `_coerce_form` reads `__for_<key>` per key in
            # the submitted metadata_keys list.
            present_marker_html = (
                f'<input type="hidden" '
                f'name="{escape(examples_field_name)}__present" value="1">'
            )
        empty_hint_li = (
            f'<li class="text-xs italic text-secondary-fg px-2 py-1" '
            f'data-multiselect-empty-hint="1">'
            f"No {escape(spec.label.lower())} selected yet — type below to add."
            f"</li>"
        )
        list_cls = (
            "flex flex-col gap-1 px-2 py-2 mb-2 border border-dashed "
            "border-surface-border rounded-sm bg-surface-bg min-h-12"
        )
        input_cls = field_input_classes()
        chip_template_attr = (
            f'data-chip-template-id="{chip_template_id}" '
            if is_metadata_keys else ""
        )
        input_html = (
            # Hidden marker — empty selection ("operator cleared every
            # chip") distinguishable from "field absent". Needed for
            # optional multi_selects; required ones reject empty in the
            # validator anyway. Kept for symmetry across kinds.
            f'<input type="hidden" name="{escape(spec.name)}__present" value="1">'
            # BF.4-followup — sibling metadata_value_examples present
            # marker (empty for non-metadata_keys widgets).
            f"{present_marker_html}"
            # CO.3 — `<name>__order` was needed in the CO.2 widget
            # because the checkbox grid submitted in alphabetical DOM
            # order, divorced from user intent. The chip-list widget
            # makes chip DOM order == user intent order, and the chip
            # hidden inputs are emitted in chip order, so getlist already
            # returns the right sequence. No __order field needed.
            f"{datalist_html}"
            # BF.4-followup — `<template>` for JS-built chips (only
            # rendered for metadata_keys; carries the inline value-
            # examples input per chip).
            f"{chip_template_html}"
            # BF.4 (2026-06-07) — `data-multiselect-allow-freeform` opts
            # the chip-list into accepting operator-typed values not
            # already in the datalist (used by `metadata_keys`, where
            # the datalist is a starter set, not the universe).
            f'<ul data-multiselect-order-list="{escape(spec.name)}" '
            f'data-multiselect-options-id="{datalist_id}" '
            f"{chip_template_attr}"
            f'{"data-multiselect-allow-freeform=\"1\" " if spec.select_from == "metadata_keys" else ""}'
            f'class="{list_cls}" '
            f'aria-label="Selected {escape(spec.label)} in order">'
            f"{chips_html}"
            f"{empty_hint_li if not selected else ''}"
            f"</ul>"
            f'<input type="text" id="field-{escape(spec.name)}" '
            f'data-multiselect-add="{escape(spec.name)}" '
            f'data-multiselect-options-id="{datalist_id}" '
            f'list="{datalist_id}" '
            f'placeholder="Type to add a {escape(spec.label.lower())[:-1] if spec.label.lower().endswith("s") else escape(spec.label.lower())}..." '
            f'class="{input_cls} w-full">'
        )
    elif spec.kind == "select":
        val_str = _value_to_input_str(value)
        # CG.22 (2026-06-05) — `parent_role` fields use the grouped
        # resolver so singleton-eligible roles render in an
        # <optgroup> on top, template-only roles below, and any
        # stale value gets its own group with a "review" hint.
        # Other "roles" select sites (which DON'T have the validator's
        # singleton-only constraint) stay flat.
        use_grouped_roles = (
            spec.name == "parent_role" and spec.select_from == "roles"
        )
        opt_blocks: list[str] = []
        if use_grouped_roles:
            grouped, allow_empty = _resolve_grouped_roles(instance, val_str)
            if allow_empty:
                opt_blocks.append(
                    f'<option value=""{" selected" if val_str == "" else ""}>'
                    f"— none —</option>"
                )
            for group_label, group_opts in grouped:
                opt_blocks.append(
                    f'<optgroup label="{escape(group_label)}">'
                )
                opt_blocks.extend(
                    f'<option value="{escape(o)}"{" selected" if o == val_str else ""}>'
                    f"{escape(o)}</option>"
                    for o in group_opts
                )
                opt_blocks.append("</optgroup>")
        else:
            if spec.select_from is not None:
                options, allow_empty = _resolve_select_options(
                    spec.select_from, instance, val_str,
                )
            else:
                options, allow_empty = spec.options, False
            if allow_empty:
                opt_blocks.append(
                    f'<option value=""{" selected" if val_str == "" else ""}>'
                    f"— none —</option>"
                )
            # v13.6.1 finding 2 — value→label override (e.g.
            # ``balance_cadence`` empty option labeled
            # "Sparse (default)" instead of rendering blank).
            labels = spec.option_labels or {}
            opt_blocks.extend(
                f'<option value="{escape(o)}"{" selected" if o == val_str else ""}>'
                f"{escape(labels.get(o, o))}</option>"
                for o in options
            )
        # CK.4 (2026-06-05) — every dynamic-form <select> ships an
        # aria-label sourced from the FieldSpec.label so axe-core's
        # `select-name` check passes. The existing
        # `<label for="field-...">` provides implicit labelling for
        # sighted operators, but axe-core treats the explicit
        # aria-label as the canonical accessible name.
        input_html = (
            f'<select id="field-{spec.name}" name="{escape(spec.name)}" '
            f'aria-label="{escape(spec.label)}" class="{input_cls}">'
            f'{"".join(opt_blocks)}</select>'
        )
    elif spec.kind == "textarea":
        val_str = _value_to_input_str(value)
        # CO.3 polish (2026-06-06) — bump default rows from 3 → 8 and
        # add `w-full` so multi-line descriptions get a usable edit
        # area instead of a ~20-column box. Chotchki spotted on TT
        # edit page (`/l2_shape/transfer_template/<n>/edit`).
        # Placeholder (also CO.3) enables :placeholder-shown CSS
        # reactivity for downstream-hide rules (see input.css for the
        # metadata_keys → metadata_value_examples pattern).
        placeholder_attr = (
            f' placeholder="{escape(spec.placeholder)}"'
            if spec.placeholder else ""
        )
        textarea_html = (
            f'<textarea id="field-{spec.name}" name="{escape(spec.name)}"'
            f'{placeholder_attr} '
            f'rows="8" class="{input_cls} resize-y min-h-32 w-full">{escape(val_str)}</textarea>'
        )
        if spec.preview_markdown:
            # BF.9 (2026-05-25) — Edit / Preview tabs. Pure HTMX:
            # the Preview tab POSTs the textarea text to
            # /preview/markdown which returns rendered HTML; swap
            # into the preview pane. Toggle visibility via JS
            # (5-line `oninput` — minimal-JS posture). Edit tab
            # stays the source of truth; Preview is read-only.
            tab_btn_active = (
                "border-b-2 border-accent text-accent font-semibold "
                "px-3 py-1 text-sm cursor-pointer"
            )
            tab_btn_inactive = (
                "border-b-2 border-transparent text-secondary-fg "
                "px-3 py-1 text-sm cursor-pointer hover:text-primary-fg"
            )
            preview_pane_cls = (
                "hidden prose-sm max-w-none px-3 py-2 border "
                "border-surface-border rounded-sm bg-white min-h-16"
            )
            preview_id = f"field-{spec.name}-preview"
            edit_id = f"field-{spec.name}-edit"
            tab_edit_id = f"field-{spec.name}-tab-edit"
            tab_preview_id = f"field-{spec.name}-tab-preview"
            # JS that swaps tab+pane visibility. Inlined per
            # AM.0 minimal-JS posture.
            input_html = (
                f'<div class="flex items-center gap-2 border-b border-surface-border">'
                f'<button type="button" id="{tab_edit_id}" '
                f'class="{tab_btn_active}" '
                f'onclick="document.getElementById(&quot;{edit_id}&quot;).classList.remove(&quot;hidden&quot;);'
                f'document.getElementById(&quot;{preview_id}&quot;).classList.add(&quot;hidden&quot;);'
                f'this.className=&quot;{tab_btn_active}&quot;;'
                f'document.getElementById(&quot;{tab_preview_id}&quot;).className=&quot;{tab_btn_inactive}&quot;;">Edit</button>'
                f'<button type="button" id="{tab_preview_id}" '
                f'class="{tab_btn_inactive}" '
                f'hx-post="/preview/markdown" '
                # CG-bug.1 (2026-06-05) — `hx-params` restricts the
                # POST body to ONLY this field. The button sits
                # inside the edit `<form>`, and HTMX's default
                # behavior when the trigger is inside a form is to
                # serialize the WHOLE form's data + still pull
                # `hx-include` extras. The server's
                # "first non-meta form value" loop then picked the
                # `id` field, not `description`, so Preview rendered
                # the kebab id instead of the markdown. `hx-params`
                # filters the body down to just this field. Surfaced
                # in dogfood post-CG sweep.
                f'hx-params="{escape(spec.name)}" '
                f'hx-include="#field-{spec.name}" '
                f'hx-target="#{preview_id}" '
                f'hx-swap="innerHTML" '
                f'onclick="document.getElementById(&quot;{edit_id}&quot;).classList.add(&quot;hidden&quot;);'
                f'document.getElementById(&quot;{preview_id}&quot;).classList.remove(&quot;hidden&quot;);'
                f'this.className=&quot;{tab_btn_active}&quot;;'
                f'document.getElementById(&quot;{tab_edit_id}&quot;).className=&quot;{tab_btn_inactive}&quot;;">Preview</button>'
                f'</div>'
                f'<div id="{edit_id}">{textarea_html}</div>'
                f'<div id="{preview_id}" class="{preview_pane_cls}"></div>'
            )
        else:
            input_html = textarea_html
    elif spec.kind == "yaml_block":
        # X.4.f.11.6.5 — Tier-3 YAML escape hatch for the
        # nested-shape field (metadata_value_examples). Same wire as
        # textarea but mono-font + tall + wraps disabled, matching
        # the operator's mental model (they already know the L2 yaml
        # shape). Coerce in _coerce_field parses with yaml.safe_load
        # and validates dict[str, list[str]]; display via
        # _value_to_input_str dumps the tuple-of-tuples back to YAML.
        val_str = _value_to_input_str(value)
        input_html = (
            f'<textarea id="field-{spec.name}" name="{escape(spec.name)}" '
            f'rows="10" spellcheck="false" class="{input_cls} resize-y min-h-16 font-mono whitespace-pre">'
            f'{escape(val_str)}</textarea>'
        )
    else:
        # text + money both render as <input type="text"> — the loader's
        # _load_money handles numeric strings either way.
        # BF.10 (2026-06-07) — composite-scalar fields (firings, range,
        # durations, etc.) pair an in-input `placeholder` with a
        # sibling always-visible `e.g. <example>` chip so the format
        # stays visible after the operator starts typing. Server
        # validator stays the truth source.
        val_str = _value_to_input_str(value)
        placeholder_attr = (
            f' placeholder="{escape(spec.placeholder)}"'
            if spec.placeholder else ""
        )
        input_html = (
            f'<input id="field-{spec.name}" name="{escape(spec.name)}" '
            f'type="text" value="{escape(val_str)}"{placeholder_attr} '
            f'class="{input_cls}">'
        )
        if spec.placeholder:
            chip_cls = (
                "text-xs text-secondary-fg font-mono whitespace-nowrap "
                "px-1.5 py-0.5 rounded-sm bg-surface-bg border "
                "border-surface-border self-start mt-1"
            )
            input_html += (
                f'<span class="{chip_cls}" aria-hidden="true">'
                f'e.g. {escape(spec.placeholder)}</span>'
            )

    # CP — business_day_offset has no consumer on scope=external
    # accounts (M.4.4.14a — we don't compute their EOD balances), so
    # hide the field-row when the operator picks external. Implemented
    # as a Tailwind arbitrary group-has-* variant against the parent
    # form's scope <select> — no raw CSS in input.css, no JS. The form
    # roots (create-form / edit-form) carry the `group` class so this
    # variant resolves against them.
    extra_cls = ""
    if spec.name == "business_day_offset":
        extra_cls = (
            " group-has-[select[name=scope]_option[value=external]:checked]:hidden"
        )
    return (
        f'<div class="{field_row_classes()}{extra_cls}">'
        f'{label}{input_html}{helper}{err_html}</div>'
    )


def _multi_select_groups_value_as_groups(
    value: object,
) -> tuple[tuple[str, ...], ...]:
    """Normalize a ``multi_select_groups`` current/override value to a
    tuple-of-tuples of strings.

    Accepts:
    - ``None`` → ``()`` (no groups)
    - ``""`` (initial create page) → ``()``
    - ``tuple[tuple[Identifier-or-str, ...], ...]`` → stringify each
      member
    - ``tuple[tuple[str, ...], ...]`` (override path on re-render) →
      pass through

    Defensive: any inner element that isn't a list/tuple is dropped
    (it can't be a valid XOR group).
    """
    if value is None or value == "":
        return ()
    if not isinstance(value, (list, tuple)):
        return ()
    groups: list[tuple[str, ...]] = []
    for inner in value:  # pyright: ignore[reportUnknownVariableType]  # WHY: outer tuple element type isn't narrowed by isinstance
        if not isinstance(inner, (list, tuple)):
            continue
        members = tuple(
            str(m)  # pyright: ignore[reportUnknownArgumentType]  # WHY: inner-tuple element type isn't narrowed
            for m in inner  # pyright: ignore[reportUnknownVariableType]  # WHY: inner-tuple element type isn't narrowed
            if str(m).strip()  # pyright: ignore[reportUnknownArgumentType]  # WHY: inner-tuple element type isn't narrowed
        )
        groups.append(members)
    return tuple(groups)


_STAGED_EDIT_BANNER_CLS = (
    "text-sm text-secondary-fg px-2 py-2 border border-dashed "
    "border-surface-border rounded-sm bg-surface-alt"
)


def _render_staged_edit_banner(
    label: str,
    message: str,
    *,
    helper: str = "",
    error: str | None = None,
    hidden_inputs: str = "",
) -> str:
    """BF.3 — render a labeled empty-state banner for fields whose
    option universe isn't knowable at the current form-render moment
    (chicken-egg). Caller writes the message in the form
    "<prereq action>, then <follow-up action>."

    Three callsites today: ``_render_multi_select_groups_field`` for
    the entity-less create page + the leg_rails-empty edit page; the
    BB.2 attach-existing block (XOR groups land on the TT edit page
    after the rail saves). BF.4 plans to add the
    ``metadata_value_examples`` callsite once that field-kind lands.
    """
    label_html = (
        f'<label class="font-semibold text-xs text-primary-fg">{escape(label)}</label>'
    )
    helper_html = (
        f'<small class="text-xs text-secondary-fg">{escape(helper)}</small>'
        if helper else ""
    )
    err_html = (
        f'<div role="alert" class="text-xs text-danger bg-red-50 px-2 py-1 rounded-sm">{escape(error)}</div>'
        if error else ""
    )
    body = (
        f'<div class="{_STAGED_EDIT_BANNER_CLS}">{escape(message)}</div>'
        f"{hidden_inputs}"
    )
    return (
        f'<div class="{field_row_classes()}">'
        f'{label_html}{body}{helper_html}{err_html}</div>'
    )


def _render_multi_select_groups_field(
    spec: FieldSpec,
    value: object,
    entity: object | None,
    error: str | None,
) -> str:
    """AB.3.7 — render a list-of-multi-selects for ``leg_rail_xor_groups``.

    Each existing group renders as a checkbox group whose option set is
    drawn from the entity's ``leg_rails`` (the sibling field named by
    ``spec.select_from="self_leg_rails"``). One always-empty trailing
    row lets the operator add a new group without JS. Unchecking every
    box in a group drops it on save (server filters empty groups in
    ``_coerce_form``).

    No entity → the create page is rendering this; show a helper
    message instead. The ``edit_only=True`` flag on the FieldSpec
    means this branch only fires if the field-spec filter on the
    create page accidentally let it through (defense-in-depth).
    """
    label_html = (
        f'<label class="font-semibold text-xs text-primary-fg">'
        f'{escape(spec.label)}{_handbook_chip_html(spec)}'
        f'{_glossary_chip_html(spec)}</label>'
    )
    helper_html = (
        f'<small class="text-xs text-secondary-fg">{escape(spec.helper)}</small>'
        if spec.helper else ""
    )
    err_html = (
        f'<div role="alert" class="text-xs text-danger bg-red-50 px-2 py-1 rounded-sm">{escape(error)}</div>'
        if error else ""
    )
    row_cls = field_row_classes()
    # Option universe = the entity's leg_rails (sibling field). On the
    # create page there's no entity; render the empty-state helper.
    if entity is None:
        return _render_staged_edit_banner(
            spec.label,
            "Save the template with at least 2 leg rails first; then "
            "open it for editing to add XOR groups.",
            helper=spec.helper,
            error=error,
        )
    leg_rails_raw = getattr(entity, "leg_rails", ()) or ()
    rails: tuple[str, ...] = tuple(
        str(r)  # pyright: ignore[reportUnknownArgumentType]  # WHY: leg_rails element type is Identifier (runtime str) but typed as Any here
        for r in leg_rails_raw  # pyright: ignore[reportUnknownVariableType]  # WHY: leg_rails is Any
    )
    groups = _multi_select_groups_value_as_groups(value)
    if not rails:
        hidden_markers = (
            f'<input type="hidden" name="{escape(spec.name)}__present" value="1">'
            f'<input type="hidden" name="{escape(spec.name)}__num_groups" value="0">'
        )
        return _render_staged_edit_banner(
            spec.label,
            "Add at least 2 leg rails to this template, save, then "
            "reopen the edit form to author XOR groups.",
            helper=spec.helper,
            error=error,
            hidden_inputs=hidden_markers,
        )
    # Render N existing groups + 1 always-empty trailing slot for
    # adding a new group. BF.4-followup (2026-06-07) — per validator
    # C1d ("no rail may appear in two groups"), disable any rail
    # that's already claimed by a sibling group; surfaces the
    # constraint in the UI instead of letting the operator make a
    # mistake then 400 on save. Each existing group's "× remove"
    # button JS-unchecks every box in its fieldset; server filters
    # empty groups on save (so unchecking IS the remove primitive).
    all_in_groups: frozenset[str] = frozenset(
        r for g in groups for r in g
    )
    blocks: list[str] = []
    for i, group in enumerate(groups):
        disabled_rails = all_in_groups - frozenset(group)
        blocks.append(
            _render_xor_group_row(
                spec.name, i, rails, group, disabled_rails,
            ),
        )
    blocks.append(
        _render_xor_group_row(
            spec.name, len(groups), rails, (), all_in_groups,
        ),
    )
    num_groups = len(groups) + 1
    body = (
        f'<div id="field-{escape(spec.name)}" '
        f'class="flex flex-col gap-2" role="group">'
        f'{"".join(blocks)}'
        f'</div>'
        f'<input type="hidden" name="{escape(spec.name)}__present" value="1">'
        f'<input type="hidden" name="{escape(spec.name)}__num_groups" '
        f'value="{num_groups}">'
    )
    return (
        f'<div class="{row_cls}">'
        f'{label_html}{body}{helper_html}{err_html}</div>'
    )


def _render_xor_group_row(
    name: str,
    index: int,
    rails: tuple[str, ...],
    selected: tuple[str, ...],
    disabled_rails: frozenset[str] = frozenset(),
) -> str:
    """One <fieldset> with all template leg_rails as checkboxes; those
    in ``selected`` start checked. Empty selected → "Add new XOR group"
    trailing slot.

    BF.4-followup (2026-06-07): ``disabled_rails`` lists rails already
    claimed by sibling groups — per validator C1d ("no rail may
    appear in two groups"), they render disabled + visually muted so
    the operator can't make the mistake. Existing groups also get a
    "× remove" button in their legend that JS-unchecks every box (the
    server-side coerce filters empty groups → group drops on save).
    """
    selected_set = frozenset(selected)
    is_new = not selected_set
    legend_text = (
        "Add new XOR group" if is_new
        else f"XOR group {index + 1}"
    )
    item_cls_base = (
        "flex items-center gap-2 font-normal text-sm "
        "text-primary-fg"
    )
    item_cls_active = f"{item_cls_base} cursor-pointer"
    item_cls_disabled = f"{item_cls_base} opacity-50 cursor-not-allowed"

    def _item(rail: str) -> str:
        is_disabled = rail in disabled_rails
        cls = item_cls_disabled if is_disabled else item_cls_active
        suffix = (
            ' <span class="text-xs italic">(in another group)</span>'
            if is_disabled else ""
        )
        # `disabled` checkboxes don't submit — that's the point;
        # the rail's value stays with its claiming group's payload.
        return (
            f'<label class="{cls}">'
            f'<input type="checkbox" name="{escape(name)}_{index}" '
            f'value="{escape(rail)}"'
            f'{" checked" if rail in selected_set else ""}'
            f'{" disabled" if is_disabled else ""}>'
            f' {escape(rail)}{suffix}</label>'
        )

    items = "".join(_item(r) for r in rails)
    fieldset_base = (
        "border border-surface-border rounded-sm px-3 py-2 bg-white"
    )
    fieldset_cls = (
        f"{fieldset_base} border-dashed bg-surface-alt"
        if is_new else fieldset_base
    )
    grid_cls = (
        "grid grid-cols-1 sm:grid-cols-2 gap-x-3 gap-y-1 mt-1"
    )
    # Remove button — only on existing groups. Inline onclick keeps
    # the JS dependency-free (matches the BB.2 reconciler section's
    # minimal-JS posture). Unchecking every box drops the group on
    # save via the server-side coerce filter.
    remove_btn_html = "" if is_new else (
        '<button type="button" '
        'class="ml-2 px-1.5 py-0.5 text-xs text-danger '
        'hover:bg-red-50 rounded-sm cursor-pointer font-normal" '
        'data-xor-remove-group '
        'aria-label="Remove this XOR group" '
        'onclick="this.closest(\'fieldset\').querySelectorAll('
        "'input[type=checkbox]:not(:disabled)'"
        ').forEach(c=>{c.checked=false});">'
        '× remove group</button>'
    )
    return (
        f'<fieldset class="{fieldset_cls}" data-group-index="{index}">'
        f'<legend class="text-xs font-semibold text-secondary-fg px-1">'
        f"{escape(legend_text)}{remove_btn_html}</legend>"
        f'<div class="{grid_cls}">{items}</div>'
        f'</fieldset>'
    )


def _metadata_value_examples_as_dict(value: object) -> dict[str, tuple[str, ...]]:
    """BF.4 — normalize a metadata_value_examples value into a
    ``dict[key, tuple[values, ...]]`` for render-time lookup, accepting
    every shape the field traverses through: ``None`` (no values),
    the canonical ``tuple[(Identifier, tuple[str, ...]), ...]`` from
    the dataclass, and the override-path ``dict[str, list[str]]`` /
    ``tuple[(str, tuple[str, ...]), ...]`` shapes coerce builds on
    a re-render.
    """
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return {  # pyright: ignore[reportUnknownVariableType]: dict comes from form override path with Any-typed entries
            str(k): tuple(str(v) for v in (vs or ()))  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]: dict elements untyped from yaml/form path
            for k, vs in value.items()  # pyright: ignore[reportUnknownVariableType]: dict.items() yields Any elements
        }
    if not isinstance(value, (list, tuple)):
        return {}
    out: dict[str, tuple[str, ...]] = {}
    for item in value:  # pyright: ignore[reportUnknownVariableType]: outer iterable is unknown-typed (form override OR dataclass shape)
        if not isinstance(item, (list, tuple)) or len(item) != 2:  # pyright: ignore[reportUnknownArgumentType]: item element type unknown
            continue
        key, vals = item  # pyright: ignore[reportUnknownVariableType]: tuple-unpack of Any-typed item
        if not isinstance(vals, (list, tuple)):
            continue
        out[str(key)] = tuple(  # pyright: ignore[reportUnknownArgumentType]: key derived from Any
            str(v) for v in vals  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]: vals is Any-typed iterable
        )
    return out


def _render_metadata_value_examples_field(
    spec: FieldSpec,  # noqa: ARG001 — kept for dispatch parity
    value: object,  # noqa: ARG001 — kept for dispatch parity
    entity: object | None,  # noqa: ARG001 — kept for dispatch parity
    error: str | None,  # noqa: ARG001 — kept for dispatch parity
) -> str:
    """BF.4-followup (2026-06-07) — the metadata_value_examples
    picker is now embedded inside each ``metadata_keys`` chip
    (one inline text input per chip). The widget owns BOTH the
    metadata_keys hidden inputs AND the value-examples text inputs;
    submission serializes through standard form encoding without
    needing a separate field-row.

    This render returns empty so the FieldSpec's slot in
    ``_FIELD_SPECS_BY_KIND["rail"]`` is invisible — the inputs
    live one row above (inside the metadata_keys chip-list).
    Coerce branch in ``_coerce_form`` still fires (gated by the
    ``__present`` marker the metadata_keys widget emits).
    """
    return ""


def _chain_children_value_as_specs(
    value: object,
) -> tuple[tuple[str, bool, int | None], ...]:
    """AB.6.7 — normalize a chain_children value to (name, fan_in,
    expected_parent_count) tuples regardless of whether it arrived
    as ChainChildSpec dataclasses (current entity reload),
    tuple-of-strings (validation-failure override), or None (create).

    The render path needs this shape: per child name, what was its
    fan_in / expected_parent_count when the entity was last saved?
    """
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        out: list[tuple[str, bool, int | None]] = []
        # WHY suppress (item-side): value is list|tuple via isinstance gate
        # but the element type is unknown (form-typed payloads carry
        # ChildSpec dataclasses OR bare strings depending on validation
        # state); the hasattr branch below picks one shape.
        for item in value:  # pyright: ignore[reportUnknownVariableType]: form-typed payloads carry mixed dataclass / bare-string elements
            if hasattr(item, "name") and hasattr(item, "fan_in"):  # pyright: ignore[reportUnknownArgumentType]: item is form-typed Any
                name = str(getattr(item, "name"))  # pyright: ignore[reportUnknownArgumentType]: item is form-typed Any
                fan_in = bool(getattr(item, "fan_in", False))  # pyright: ignore[reportUnknownArgumentType]: item is form-typed Any
                epc_raw = getattr(item, "expected_parent_count", None)  # pyright: ignore[reportUnknownArgumentType]: item is form-typed Any
                epc: int | None = (
                    int(epc_raw) if epc_raw is not None and epc_raw != "" else None
                )
                out.append((name, fan_in, epc))
            else:
                # Validation-failure path: tuple-of-strings (operator's
                # last submission). fan_in / epc came from sibling form
                # fields, not the value itself — defaulted here; the
                # form_overrides dict carries the per-child shape.
                out.append((str(item), False, None))  # pyright: ignore[reportUnknownArgumentType]: item is form-typed Any but stringifies safely
        return tuple(out)
    return ()


def _render_chain_children_field(
    spec: FieldSpec,
    value: object,
    instance: Any,  # typing-smell: ignore[explicit-any]: L2Instance — for select_from resolution
    error: str | None,
) -> str:
    """CO.3 polish (2026-06-06) — chip-list-as-primary widget. Each
    child is a draggable chip carrying its name + fan_in checkbox + epc
    number input. Add via the typeahead `<input list>` at the bottom;
    × button removes; chip DOM order = canonical sequence (no
    `__order` field needed). The epc input hides via CSS when fan_in
    is unchecked (`:has(:not(:checked))` — see input.css).

    Server contract preserved: `getlist("children")` returns names from
    the chip hidden inputs in DOM order; per-name `fan_in_<name>` and
    `epc_<name>` come from sibling inputs in the same chip. Pre-CO.3
    this was a checkbox grid showing the full universe with each
    checkbox row carrying its own fan_in + epc; awkward for both
    discovery (which child do I add?) AND ordering (no signal that
    order matters).

    Per AB.6.0 validator C8a, picking fan-in on a Rail child (not a
    TransferTemplate) returns inline error on submit; the renderer
    doesn't gate it client-side.
    """
    if spec.select_from is None:
        raise ValueError(
            f"chain_children FieldSpec {spec.name!r} requires select_from",
        )
    label = (
        f'<label for="field-{spec.name}" class="font-semibold text-xs text-primary-fg">'
        f'{escape(spec.label)}'
        f'{"<span class=\"text-danger\"> *</span>" if spec.required else ""}'
        f"{_handbook_chip_html(spec)}"
        f"{_glossary_chip_html(spec)}"
        f"</label>"
    )
    helper = (
        f'<small class="text-xs text-secondary-fg">{escape(spec.helper)}</small>'
        if spec.helper else ""
    )
    err_html = (
        f'<div role="alert" class="text-xs text-danger bg-red-50 px-2 py-1 rounded-sm">{escape(error)}</div>'
        if error else ""
    )

    options, _ = _resolve_select_options(spec.select_from, instance, "")
    existing = _chain_children_value_as_specs(value)
    selected_in_order = [(name, fan_in, epc) for name, fan_in, epc in existing]
    selected_set = {name for name, _, _ in existing}
    # Defensive: any selected child not in the option universe still
    # shows as a chip (stale reference; validator surfaces the broken
    # ref separately).

    datalist_id = f"field-{escape(spec.name)}-options"
    datalist_html = (
        f'<datalist id="{datalist_id}">'
        + "".join(
            f'<option value="{escape(o)}"></option>'
            for o in options if o not in selected_set
        )
        + "</datalist>"
    )

    chip_li_cls = (
        "flex items-center gap-2 bg-link-tint border border-surface-border "
        "rounded-sm px-2 py-1 text-sm"
    )
    chip_remove_cls = (
        "px-2 py-0.5 text-secondary-fg hover:text-danger hover:bg-red-50 "
        "rounded-sm cursor-pointer text-base leading-none"
    )
    fan_in_label_cls = (
        "flex items-center gap-1 font-normal text-xs text-secondary-fg "
        "cursor-pointer"
    )
    epc_input_cls = (
        "w-14 px-1 py-0.5 border border-surface-border rounded-sm "
        "text-sm bg-white"
    )

    def _chip(name: str, fan_in: bool, epc: int | None) -> str:
        epc_str = str(epc) if epc is not None else ""
        return (
            f'<li class="{chip_li_cls}" data-multiselect-order-value="{escape(name)}">'
            f'<span aria-hidden="true" class="cursor-grab text-secondary-fg select-none">⋮</span>'
            f'<input type="hidden" name="children" value="{escape(name)}">'
            f'<span class="grow">{escape(name)}</span>'
            f'<label class="{fan_in_label_cls}" title="N:1 fan-in: expect N parent firings per child Transfer">'
            f'<input type="checkbox" name="fan_in_{escape(name)}" value="true"{" checked" if fan_in else ""}>'
            f' fan-in</label>'
            f'<input type="number" name="epc_{escape(name)}" '
            f'value="{escape(epc_str)}" min="1" placeholder="N" '
            f'aria-label="Expected parent count for {escape(name)}" '
            f'title="Expected parent firings per child Transfer (only meaningful with fan-in)" '
            f'class="{epc_input_cls}">'
            f'<button type="button" class="{chip_remove_cls}" '
            f'aria-label="Remove {escape(name)}" '
            f'data-multiselect-remove="{escape(name)}">&times;</button>'
            f"</li>"
        )

    chips_html = "".join(
        _chip(name, fan_in, epc) for name, fan_in, epc in selected_in_order
    )
    empty_hint_li = (
        f'<li class="text-xs italic text-secondary-fg px-2 py-1" '
        f'data-multiselect-empty-hint="1">'
        f"No children selected yet — type below to add."
        f"</li>"
    )
    list_cls = (
        "flex flex-col gap-1 px-2 py-2 mb-2 border border-dashed "
        "border-surface-border rounded-sm bg-surface-bg min-h-12"
    )
    template_id = f"field-{escape(spec.name)}-chip-template"
    # Template element cloned by the bootstrap JS on add. Uses literal
    # `__NAME__` placeholders the JS substitutes with the typeahead
    # value. <template> contents don't render and aren't form-submitted.
    template_html = (
        f'<template id="{template_id}">'
        f"{_chip('__NAME__', False, None)}"
        f"</template>"
    )
    input_cls = field_input_classes()
    input_html = (
        # Hidden marker — see comment above.
        f'<input type="hidden" name="children__present" value="1">'
        f"{datalist_html}"
        f"{template_html}"
        f'<ul data-multiselect-order-list="children" '
        f'data-multiselect-options-id="{datalist_id}" '
        f'data-chip-template-id="{template_id}" '
        f'class="{list_cls}" '
        f'aria-label="Selected {escape(spec.label)} in order">'
        f"{chips_html}"
        f"{empty_hint_li if not selected_in_order else ''}"
        f"</ul>"
        f'<input type="text" id="field-{escape(spec.name)}" '
        f'data-multiselect-add="children" '
        f'data-multiselect-options-id="{datalist_id}" '
        f'list="{datalist_id}" '
        f'placeholder="Type to add a child..." '
        f'class="{input_cls} w-full">'
    )
    return (
        f'<div class="{field_row_classes()}">'
        f"{label}{input_html}{helper}{err_html}</div>"
    )


def _multi_value_as_strs(value: object) -> tuple[str, ...]:
    """Normalize the multi-select current/override value to a tuple of
    strings for the option-selected check.

    Accepts: None, tuple/list of Identifier-or-str, or a single
    Identifier/str (treated as a 1-element tuple — defensive).
    """
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(
            str(v)  # pyright: ignore[reportUnknownArgumentType]  # WHY: tuple element type isn't narrowed by isinstance; values stringify safely
            for v in value  # pyright: ignore[reportUnknownVariableType]  # WHY: tuple element type isn't narrowed by isinstance
            if str(v)  # pyright: ignore[reportUnknownArgumentType]  # WHY: tuple element type isn't narrowed by isinstance
        )
    s = str(value)
    return (s,) if s else ()


def _value_to_input_str(value: object) -> str:
    """Stringify a dataclass field value for the form input's `value=`.

    bool → ``"true"`` / ``"false"`` (lowercase) so a yaml-shaped
    ``options=("true", "false")`` select preselects correctly. tuple
    (RoleExpression / leg_rails / etc.) → comma-separated for the
    read card; the multi_select renderer reaches for the tuple
    directly via ``_multi_value_as_strs``. The ``metadata_value_examples``
    nested shape (tuple-of-(key, tuple-of-values)) renders as a YAML
    map for the yaml_block kind — see ``_metadata_value_examples_to_yaml``.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    # AI.2.d.1.a — Duration is a TypeAlias for timedelta; `str(timedelta)`
    # emits Python's "1 day, 0:00:00" repr, which the loader's
    # _ISO_DURATION_RE rejects. Emit ISO 8601 (P1D / PT24H / P1DT12H)
    # so a create-form POST round-trips cleanly through `_load_duration`.
    if isinstance(value, timedelta):
        total = int(value.total_seconds())
        days, rem = divmod(total, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, seconds = divmod(rem, 60)
        out = "P"
        if days:
            out += f"{days}D"
        t_parts: list[str] = []
        if hours:
            t_parts.append(f"{hours}H")
        if minutes:
            t_parts.append(f"{minutes}M")
        if seconds:
            t_parts.append(f"{seconds}S")
        if t_parts:
            out += "T" + "".join(t_parts)
        return out if out != "P" else "PT0S"
    # AF (E8) — FiringsTypicalPerPeriod → composite text shape: bare
    # `min, max` when business_day, `period: min, max` otherwise.
    # Round-trips through _coerce_field's firings_typical_per_period
    # branch.
    if isinstance(value, FiringsTypicalPerPeriod):
        lo, hi = value.count_range
        if value.period == "business_day":
            return f"{lo}, {hi}"
        return f"{value.period}: {lo}, {hi}"
    # X.4.f.11.6.5 — metadata_value_examples is the only field whose
    # tuple shape is nested (tuple[(key, tuple[str, ...]), ...]). Match
    # on tuple-of-2-tuples-with-tuple-second specifically and dump as
    # YAML; flat tuples fall through to the comma-join below.
    if isinstance(value, tuple) and value and all(
        isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], tuple)  # pyright: ignore[reportUnknownArgumentType]  # WHY: tuple element type isn't narrowed by isinstance
        for item in value  # pyright: ignore[reportUnknownVariableType]  # WHY: tuple element type isn't narrowed by isinstance
    ):
        return _metadata_value_examples_to_yaml(value)  # pyright: ignore[reportUnknownArgumentType]  # WHY: shape narrowed by the all() guard above
    if isinstance(value, tuple):
        return ", ".join(
            str(v)  # pyright: ignore[reportUnknownArgumentType]  # WHY: tuple element type isn't narrowed by isinstance
            for v in value  # pyright: ignore[reportUnknownVariableType]  # WHY: tuple element type isn't narrowed by isinstance
        )
    return str(value)


def _metadata_value_examples_to_yaml(
    value: tuple[tuple[object, tuple[str, ...]], ...],
) -> str:
    """Dump the tuple-of-tuples nested shape as a YAML map.

    Each (key, values-tuple) pair becomes ``key: [v1, v2, ...]`` (block
    or flow style — yaml.safe_dump picks based on length). Round-trips
    cleanly through yaml.safe_load on the way back.
    """
    import yaml  # noqa: PLC0415 — lazy to dodge import-time cost
    as_dict: dict[str, list[str]] = {
        str(k): list(v) for k, v in value
    }
    return yaml.safe_dump(as_dict, default_flow_style=False, sort_keys=False).rstrip() + "\n"


def _format_money_for_display(raw: str) -> str | None:
    """BX.5 — format a money string as USD for the read card.

    Returns ``$1,234.56`` for ``"1234.56"``, ``-$1,234.56`` for
    ``"-1234.56"`` (negative sign before the symbol, matching the
    QS + tree-fields ``currency=True`` convention). Returns ``None``
    when ``raw`` doesn't parse as a Decimal — caller falls through to
    the plain-escape path. Two decimal places fixed (Money is the
    L2 primitive's 2dp Decimal alias; see
    ``common/l2/primitives.py::Money``).
    """
    from decimal import Decimal, InvalidOperation  # noqa: PLC0415 — lazy
    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    # Quantize to 2dp; preserves sign. `f"{value:,.2f}"` thousand-separates.
    quantized = amount.quantize(Decimal("0.01"))
    negative = quantized < 0
    abs_str = f"{abs(quantized):,.2f}"
    return f"-${abs_str}" if negative else f"${abs_str}"


def _render_read_value(spec: FieldSpec, value: object) -> str:
    """Render a dataclass field value for the read-only card.

    Most fields fall through to ``_value_to_input_str``. AB.3.7's
    ``multi_select_groups`` is the exception: a nested
    tuple-of-tuples needs a per-group bullet display, since the
    flat-tuple stringifier would print the inner tuple's ``repr``
    (``('A', 'B'), ('C', 'D')`` — readable but cluttered).
    """
    ul_cls = "list-disc pl-5 m-0 text-sm text-primary-fg"
    if spec.kind == "multi_select_groups":
        groups = _multi_select_groups_value_as_groups(value)
        if not groups:
            return "—"
        # BF.1.S2: renamed `items` -> `groups_html` so the str-typed
        # local doesn't collide with the `items: list[str]` declared in
        # the sibling chain_children branch below.
        groups_html = "".join(
            f'<li>group {i + 1}: {escape(", ".join(g))}</li>'
            for i, g in enumerate(groups)
        )
        return f'<ul class="{ul_cls}">{groups_html}</ul>'
    if spec.kind == "chain_children":
        children = _chain_children_value_as_specs(value)
        if not children:
            return "—"
        tag_cls = (
            "ml-2 px-1.5 py-0.5 text-xs rounded-sm bg-link-tint "
            "text-accent border border-accent/25"
        )
        items: list[str] = []
        for name, fan_in, epc in children:
            tag = ""
            if fan_in:
                epc_str = (
                    f" epc={epc}" if epc is not None else " (variable batch)"
                )
                tag = (
                    f' <span class="{tag_cls}">[fan-in{epc_str}]</span>'
                )
            items.append(f"<li>{escape(name)}{tag}</li>")
        return f'<ul class="{ul_cls}">{"".join(items)}</ul>'
    # CF.4.g — chip-list rendering for fields tagged
    # `render_as="chip_list"`. Tuple-typed values become a flex-wrap
    # of small chips with `break-keep` so underscored identifiers
    # don't split mid-token in the value column (operator complaint
    # root cause — the value-column widening alone wasn't enough
    # because the underscore is a word-break opportunity for
    # `break-words`).
    if spec.render_as == "chip_list" and isinstance(value, tuple):
        chip_items = cast("tuple[object, ...]", value)
        if not chip_items:
            return "—"
        chip_cls = (
            "inline-block px-1.5 py-0.5 text-xs rounded-sm "
            "bg-link-tint text-accent border border-accent/25 "
            "font-mono break-keep"
        )
        chips = "".join(
            f'<span class="{chip_cls}">{escape(str(v))}</span>'
            for v in chip_items
        )
        return (
            f'<div class="flex flex-wrap gap-1 mt-0.5">{chips}</div>'
        )
    raw = _value_to_input_str(value)
    if not raw:
        return "—"
    # BX.5 — `kind="money"` IS the typed currency signal: the FieldSpec
    # dataclass already discriminates money from text/select. Render
    # money values as USD ($1,234.56) on the read card so cap /
    # expected_eod_balance / expected_net display in the operator's
    # vocabulary instead of bare Decimal repr. Matches the
    # `currency=True` pattern on `common/tree/fields.py::Dim`/`Measure`
    # + `common/dataset_contract.py::Column` — parallel API for the
    # editor-card surface. Money values whose decimal parse fails fall
    # through to the plain-escape path; the loader rejects malformed
    # money upstream so this should never fire.
    if spec.kind == "money":
        formatted = _format_money_for_display(raw)
        if formatted is not None:
            return escape(formatted)
    # CF.4.g — `render_as="monospace"` wraps in a font-mono span so
    # id-shaped values read as identifiers rather than prose.
    if spec.render_as == "monospace":
        return (
            f'<span class="font-mono text-sm">{escape(raw)}</span>'
        )
    # BF.9 follow-on (2026-05-25): render description / markdown
    # fields as HTML on the read card so an operator sees the
    # actual formatted prose (bullets, code spans, bold) — same
    # render path as the edit-form's Preview tab. Catches the
    # common case where description prose is markdown-flavored
    # (the field helper explicitly says "markdown OK").
    if spec.kind == "textarea" and spec.preview_markdown:
        import markdown as _md  # noqa: PLC0415 — lazy
        rendered = _md.markdown(
            raw, extensions=["fenced_code", "tables"],
        )
        return (
            f'<div class="prose-sm max-w-none text-sm text-primary-fg">'
            f'{rendered}</div>'
        )
    return escape(raw)


def _render_read_card_summary(
    kind: EntityKind, entity: object,
    instance: Any,  # typing-smell: ignore[explicit-any]: L2Instance — retained for callsite parity with body helper
    entity_id: str,
    html_id: str,
) -> str:
    """CF.4.c split — the always-visible part of a read card (title +
    Edit/Delete actions). Renders without the heavy `<dl>` body so
    `<details>`-wrapped cards stay cheap when collapsed.
    """
    # X.4.f.11 — surface rail subtype as a small badge on the read
    # card so the operator can tell a TwoLeg apart from a SingleLeg
    # at a glance. Non-rail entities don't get a badge.
    subtype_badge = ""
    rail_subtype = _rail_subtype_of(entity)
    if rail_subtype is not None:
        subtype_label = "two-leg" if rail_subtype == "two_leg" else "single-leg"
        badge_cls = (
            "inline-block ml-2 px-1.5 py-0.5 text-xs font-semibold "
            "rounded-sm bg-link-tint text-accent border border-accent/25"
        )
        subtype_badge = (
            f' <span class="{badge_cls}">{escape(subtype_label)}</span>'
        )
    # BX.6/11 D5 carry-forward — role-cardinality badge on Account
    # (1:1) + AccountTemplate (1:N) read cards. The math notation
    # (1:1 / 1:N) leads on the badge per locked OQ5; the secondary
    # phrase (Singleton account / Templated role) renders on the
    # card body as a sub-line (`data-role="card-cardinality-subline"`).
    # The 1:N badge optionally carries a live instance count when a
    # cfg-bound count_provider is wired into the L2Instance via
    # `_role_instance_count` — None → no count suffix; 0 →
    # "awaiting first ETL"; positive → "~N instances".
    cardinality_badge = ""
    cardinality_subline = ""
    badge_cls = (
        "inline-block ml-2 px-1.5 py-0.5 text-xs font-semibold "
        "rounded-sm bg-link-tint text-accent border border-accent/25"
    )
    subline_cls = (
        "block text-xs text-secondary-fg mt-0.5"
    )
    if kind == "account":
        cardinality_badge = (
            f' <span class="{badge_cls}" '
            f'data-cardinality-badge data-cardinality="one-to-one">'
            f'1:1</span>'
        )
        cardinality_subline = (
            f'<span class="{subline_cls}" '
            f'data-role="card-cardinality-subline">'
            f'Singleton account</span>'
        )
    elif kind == "account_template":
        # Optional live-count probe — the L2Instance can carry a
        # cosmetic `_role_instance_counts` mapping (str → int|None)
        # set by the studio's per-request hydrator; absent →
        # badge renders math notation alone (the no-DB shape per
        # OQ3 locked recommendation).
        role_attr = getattr(entity, "role", None)
        count_suffix = ""
        counts: object = getattr(instance, "_role_instance_counts", None)
        if (
            role_attr is not None
            and isinstance(counts, Mapping)
        ):
            counts_map = cast(
                "Mapping[str, object]",
                counts,
            )
            raw_count: object = counts_map.get(str(role_attr))
            if raw_count is not None:
                try:
                    count_int = int(raw_count)  # pyright: ignore[reportArgumentType]: int() accepts str / numeric, narrowed at runtime
                except (TypeError, ValueError):
                    count_int = -1
                if count_int == 0:
                    count_suffix = " · awaiting first ETL"
                elif count_int > 0:
                    count_suffix = f" · ~{count_int} instances"
        cardinality_badge = (
            f' <span class="{badge_cls}" '
            f'data-cardinality-badge data-cardinality="one-to-many">'
            f'1:N{escape(count_suffix)}</span>'
        )
        cardinality_subline = (
            f'<span class="{subline_cls}" '
            f'data-role="card-cardinality-subline">'
            f'Templated role</span>'
        )
    del instance  # parity with `_render_read_card_body` — kept for the API
    # `min-w-0` lets the h3 shrink inside the flex header so
    # `break-all` can wrap composite IDs (chain / limit_schedule use
    # `::` separators which aren't natural break points; without
    # break-all + min-w-0 they overflow into the Edit/Delete actions
    # column — user dogfood 2026-05-25).
    h3_base = "text-base font-semibold m-0 text-primary-fg min-w-0 break-all"
    # CF.4 followup (2026-06-05): title is plain text. The
    # CF.3.l title-as-diagram-focus link was a holdover from before
    # the diagram became its own top-level surface; jumping out of
    # the editor on a click that looks like a heading is surprising.
    # The Edit button right next to the title is the explicit
    # affordance. Collapsed cards still toggle on title click via
    # native `<details>`/`<summary>` semantics (the title has no
    # `onclick="stopPropagation"` so the click bubbles to the parent).
    # CG.11 (2026-06-05) — surface the analyst-readable name next to
    # the kebab id on account / account_template cards. Cold-read
    # v3: accountants read by Fed-statement name ("Cash & Due From
    # Federal Reserve"), not by GL kebab ("gl-1010-cash-due-frb"),
    # so the at-a-glance scan was broken until the operator
    # expanded each card. The name (`Account.name` field) renders
    # smaller + `text-secondary-fg` next to the id, like the rail
    # subtype badge. Other kinds keep the existing title shape.
    display_name_html = ""
    if kind == "account":
        name_attr = getattr(entity, "name", None)
        if name_attr is not None and str(name_attr).strip():
            name_cls = (
                "ml-2 text-sm font-normal text-secondary-fg "
                "break-words"
            )
            display_name_html = (
                f' <span class="{name_cls}" '
                f'data-role="card-display-name">'
                f"{escape(str(name_attr))}</span>"
            )
        # CO.3 followup (2026-06-07) — surface Account.role on the
        # summary too. role became required post-CO.3 + the cards
        # collapse-by-default on the list view (body lazy-fetched on
        # first toggle), so without surfacing role here the only way
        # an operator sees the role from the list view is to expand
        # every card. Renders as a small badge after the display
        # name (or directly after the id when name is unset) so the
        # at-a-glance scan answers both "which account" + "which
        # role does it play". Same shape as the rail subtype badge
        # so the visual vocabulary stays consistent.
        role_attr = getattr(entity, "role", None)
        if role_attr is not None and str(role_attr).strip():
            role_badge_cls = (
                "inline-block ml-2 px-1.5 py-0.5 text-xs font-semibold "
                "rounded-sm bg-link-tint text-accent border border-accent/25"
            )
            display_name_html += (
                f' <span class="{role_badge_cls}" '
                f'data-role="card-role-badge">'
                f"{escape(str(role_attr))}</span>"
            )
    # CG.12 (2026-06-05) — chain card titles render the parent only;
    # the composite "Parent::ChildA,ChildB,..." is the URL/addressing
    # key (kept on `data-entity-id` for hx-target plumbing) but it's
    # unscannable in the title. The body's `<dt>Children</dt>` row
    # (FieldSpec.kind="chain_children", label="Children") already
    # lists the children, so dropping them from the title is pure
    # signal-to-noise. Cold-read v3 P1 finding.
    #
    # CG.18 (2026-06-05) — same treatment for limit_schedule, whose
    # composite is "Role::Rail::Direction" (e.g.
    # "DDAControl::CustomerOutboundACH::Outbound"). The cold-read v4
    # suggested role-only, but multiple limit_schedules can share the
    # same parent_role (one role, many rails, ± direction), so
    # title-as-role-alone would render N indistinguishable cards.
    # Title becomes `{role} → {rail}` so each (role, rail) pair has
    # a unique scannable title; direction renders as a smaller
    # secondary-fg badge after the title (similar to display_name on
    # accounts) since direction is binary (Inbound / Outbound) and
    # carries enough weight to belong in the title row, just smaller.
    # Cold-read v4 P1 #1 — CG.12 was incomplete; finishes the
    # analogous kind.
    title_text = entity_id
    direction_badge_html = ""
    if kind == "chain":
        parent_attr = getattr(entity, "parent", None)
        if parent_attr is not None:
            title_text = str(parent_attr)
    elif kind == "limit_schedule":
        parent_role_attr = getattr(entity, "parent_role", None)
        rail_attr = getattr(entity, "rail", None)
        direction_attr = getattr(entity, "direction", None)
        if parent_role_attr is not None and rail_attr is not None:
            title_text = f"{parent_role_attr} → {rail_attr}"
        if direction_attr is not None:
            direction_cls = (
                "ml-2 text-sm font-normal text-secondary-fg "
                "break-words"
            )
            direction_badge_html = (
                f' <span class="{direction_cls}" '
                f'data-role="card-direction-badge">'
                f"{escape(str(direction_attr))}</span>"
            )
    title_html = (
        f'<h3 class="{h3_base}">{escape(title_text)}'
        f'{display_name_html}{direction_badge_html}{subtype_badge}'
        f'{cardinality_badge}</h3>'
        f'{cardinality_subline}'
    )
    # X.4.f.9.delete — DELETE on success returns empty (card disappears
    # via outerHTML swap); on validator-rejected structural break returns
    # 400 + the error fragment which swaps in place. CF.4.c —
    # `event.stopPropagation()` so clicking Edit/Delete inside a
    # `<summary>` fires the action without expanding the parent
    # `<details>`.
    # CF.4.f (followup a) — promoted from bare text links to
    # ghost-outline buttons; Delete uses danger-solid so destructive
    # actions are visually distinct. Local Tailwind utility classes
    # for now; Phase CI.3 will replace these with the typed `Button`
    # primitive when it lands. (Only one literal `CI.3 followup`
    # marker exists in the codebase; this is it.)
    edit_btn_cls = (
        # ghost-outline: accent border + accent text, fills on hover
        "inline-flex items-center px-2 py-0.5 text-xs font-semibold "
        "border border-accent text-accent rounded-sm "
        "no-underline cursor-pointer "
        "hover:bg-accent hover:text-white"
    )
    delete_btn_cls = (
        # danger-solid: red fill, lighter on hover
        "inline-flex items-center px-2 py-0.5 text-xs font-semibold "
        "border border-danger text-danger rounded-sm "
        "no-underline cursor-pointer "
        "hover:bg-danger hover:text-white"
    )
    # BX.1 (2026-06-11) — Delete no longer fires hx-delete directly.
    # Clicking opens an inline confirm banner (reference-check first,
    # 5s countdown, Cancel) rendered into the page-level
    # `#delete-confirm-banner-slot`. The actual DELETE is the banner's
    # Confirm button + a server-signed countdown token. No
    # browser-native `hx-confirm` modal.
    # BX.10 (2026-06-11) — URLs use the opaque ``<hash6>-<slug>`` form
    # for composite-keyed kinds (chain / limit_schedule); single-key
    # kinds are unchanged (kebab id verbatim).
    url_id = _url_entity_id(kind, entity)
    actions_html = (
        f'<div class="flex items-center gap-2 shrink-0">'
        f'<a class="{edit_btn_cls}" '
        f'href="/l2_shape/{kind}/{escape(url_id)}/edit" '
        f'onclick="event.stopPropagation()">Edit</a>'
        f'<a class="{delete_btn_cls}" '
        f'data-role="card-delete" '
        f'hx-get="/l2_shape/{kind}/{escape(url_id)}/delete-confirm" '
        f'hx-target="#delete-confirm-banner-slot" hx-swap="innerHTML" '
        f'onclick="event.stopPropagation()">Delete</a>'
        f"</div>"
    )
    header_cls = "flex items-start justify-between gap-3"
    return (
        f'<header class="{header_cls}">{title_html}{actions_html}</header>'
    )


def _render_read_card_body(
    kind: EntityKind, entity: object,
    instance: Any,  # typing-smell: ignore[explicit-any]: L2Instance — needed to suppress fields hidden by the two-layer rule
) -> str:
    """CF.4.c split — the heavy `<dl>` rows. Lazy-fetched via HTMX
    `?body_only=1` when the parent card is collapse-by-default."""
    specs = _filter_specs_for_entity(_FIELD_SPECS_BY_KIND[kind], entity)
    hidden = _hidden_fields_for_entity(kind, entity, instance)
    dt_cls = "font-semibold text-xs text-secondary-fg mt-2"
    dd_cls = "ml-0 mt-0.5 text-sm text-primary-fg break-words"
    rows = "".join(
        f'<dt class="{dt_cls}">{escape(s.label)}</dt>'
        f'<dd class="{dd_cls}">'
        f"{_render_read_value(s, getattr(entity, s.name, None))}"
        f"</dd>"
        for s in specs
        if s.name not in hidden
    )
    # `minmax(0, 1fr)` on the dd column (not bare `1fr`) lets long
    # unbroken values shrink + the `break-words` utility on `dd_cls`
    # then wraps them inside the card.
    dl_cls = "m-0 grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4"
    return f'<dl class="{dl_cls}">{rows}</dl>'


# CF.4.c — crossover threshold: when a page renders ≤10 cards, eager
# render is cheaper than 10 HTMX round-trips on first expand. Above
# the threshold, collapse-by-default + lazy-fetch keeps the response
# bytes proportional to the toolbar + summaries, not the heavy `<dl>`s.
# Operator can tune via the `collapsed=` arg on the renderer.
COLLAPSE_THRESHOLD = 10


def _render_read_card(
    kind: EntityKind, entity: object,
    instance: Any,  # typing-smell: ignore[explicit-any]: L2Instance — passed through to body / summary helpers
    *, collapsed: bool = False,
) -> str:
    """Read-only card — the post-PUT response + the click-to-expand
    target for the list view.

    CF.4.c: ``collapsed=True`` wraps the heavy `<dl>` body inside a
    `<details>` whose body fragment is HTMX-fetched on first expand
    (`/l2_shape/<kind>/<id>?body_only=1`, the existing read_card
    endpoint with a query flag). The crossover
    threshold is ``COLLAPSE_THRESHOLD`` — `_render_list_page` decides
    based on total_count. Sasquatch_pr (7 rails) stays eager;
    heavy_density_v1 (100+ rails) collapses.
    """
    entity_id = _entity_id(kind, entity)
    # BX.10 (2026-06-11) — opaque URL form for composite-keyed kinds;
    # single-key kinds keep the kebab id verbatim. Used wherever the
    # card emits a URL pointing at this entity.
    url_id = _url_entity_id(kind, entity)
    # CSS-safe id slug — composite-keyed kinds use ``::`` in their
    # addressing string, which CSS parses as pseudo-element syntax;
    # the URL-side path stays ``::``, only the HTML id swaps.
    html_id = f"entity-{kind}-{escape(_html_id_slug(entity_id))}"
    summary_html = _render_read_card_summary(
        kind, entity, instance, entity_id, html_id,
    )
    card_cls = entity_card_classes()
    if collapsed:
        # CF.4.c — body fragment loaded on first `toggle` event from
        # the parent `<details>`. `hx-trigger="toggle once"` fires
        # exactly once; subsequent open/close cycles re-use the
        # already-fetched body. Placeholder reads "loading…" until
        # the swap completes; reader-friendly for screen readers.
        body_url = f"/l2_shape/{kind}/{escape(url_id)}?body_only=1"
        body_placeholder = (
            '<div data-role="card-body" class="text-xs '
            'text-secondary-fg italic mt-1">loading…</div>'
        )
        # CF.4 followup (2026-06-05): `group` on `<details>` so the
        # chevron can read the parent's `[open]` state via Tailwind's
        # `group-open:` variant. Native browser marker is suppressed
        # because we render our own — placement, color, and rotation
        # all need to match the rest of the design system.
        summary_wrapper_cls = (
            "list-none cursor-pointer flex items-start gap-2 "
            "[&::-webkit-details-marker]:hidden"
        )
        # `▸` (U+25B8) rotates 90° on open. `transition-transform`
        # smooths the spin; `mt-1` aligns the glyph with the h3's
        # x-height so it doesn't float above the title.
        chevron_html = (
            '<span class="inline-block transition-transform '
            'group-open:rotate-90 text-secondary-fg mt-1 select-none" '
            'aria-hidden="true" data-role="card-chevron">▸</span>'
        )
        return (
            f'<article class="{card_cls}" id="{html_id}" '
            f'data-kind="{escape(kind)}" '
            f'data-entity-id="{escape(entity_id)}">'
            f'<details class="group" hx-get="{body_url}" '
            f'hx-target="find [data-role=\'card-body\']" '
            f'hx-swap="outerHTML" hx-trigger="toggle once">'
            f'<summary class="{summary_wrapper_cls}">'
            f'{chevron_html}'
            f'<div class="flex-1 min-w-0">{summary_html}</div>'
            f"</summary>"
            f"{body_placeholder}"
            f"</details>"
            f"</article>"
        )
    # Eager render — the historical behavior + the small-L2 path.
    body_html = _render_read_card_body(kind, entity, instance)
    return (
        f'<article class="{card_cls}" id="{html_id}" '
        f'data-kind="{escape(kind)}" data-entity-id="{escape(entity_id)}">'
        f"{summary_html}"
        f'<div class="mb-2"></div>'  # vertical gap between header and body
        f"{body_html}"
        f"</article>"
    )


# CG.13 (2026-06-05) — operator-readable kind labels moved to
# `_components.py` as the shared source of truth, so the aria-label
# site there and the h1 / page-title sites here read the same map.
# Import the helpers via `kind_label_singular` / `kind_label_plural`.

# CG.6 (2026-06-05) — one-sentence blurb per list kind, used by
# `_render_list_page` to render the trainer-style header strip
# (matches `/training/` and `/etl/` shape: h1 + 1-sentence blurb on
# a white `border-b` strip). Keep blurbs short — the operator
# already knows the kind by name; the strip is anchor + context,
# not documentation.
# BX.6/11 (2026-06-11) — h1 + cross-link rebrand for the per-
# cardinality list pages. The pre-reframe ``Accounts`` / ``Account
# templates`` h1s become ``Roles — 1:1`` / ``Roles — 1:N`` so the
# editor surface speaks the role vocabulary end-to-end. The blurb
# below keeps the per-kind explanation; the sub-line under the h1
# adds the legacy term (Singleton account / Templated role) in
# secondary-fg + a cross-link to the sibling-cardinality page.
def _list_page_h1_for_kind(kind: EntityKind) -> tuple[str, str]:
    """Return ``(h1_text, extra_html_below_h1)`` for the kind's
    list page header.

    ``h1_text`` is the visible string in the ``<h1>`` element
    (pre-escape). ``extra_html_below_h1`` is a fragment rendered
    immediately under the h1 (already HTML-escaped where needed)
    — used for the BX.6/11 cross-link strip on the account /
    account_template pages.
    """
    if kind == "account":
        # Sibling page → 1:N templated roles
        extra = (
            '<p class="text-xs text-secondary-fg m-0 mt-1" '
            'data-role="card-cardinality-subline">'
            'Singleton account &middot; one ledger row per role. '
            '<a class="text-accent hover:underline" '
            'href="/l2_shape/account_template/">'
            '&rarr; See 1:N templated roles</a>'
            '</p>'
        )
        return ("Roles — 1:1", extra)
    if kind == "account_template":
        extra = (
            '<p class="text-xs text-secondary-fg m-0 mt-1" '
            'data-role="card-cardinality-subline">'
            'Templated role &middot; N runtime instances per declaration. '
            '<a class="text-accent hover:underline" '
            'href="/l2_shape/account/">'
            '&rarr; See 1:1 singleton accounts</a>'
            '</p>'
        )
        return ("Roles — 1:N", extra)
    # Every other kind keeps the plural-label h1 + no sub-line.
    return (kind_label_plural(kind), "")


_LIST_PAGE_BLURB_BY_KIND: Mapping[EntityKind, str] = {
    "account": (
        "One row per ledger position the institution holds — "
        "internal GLs and external counterparty accounts both live "
        "here, distinguished by scope."
    ),
    "account_template": (
        "Patterns that materialize many parallel accounts at ETL "
        "time (e.g. one customer DDA per customer-id)."
    ),
    "rail": (
        "The lowest-level money-movement leg — ACH credit, internal "
        "GL move, wire debit. Two-leg rails post symmetric debits "
        "and credits; single-leg rails post one side."
    ),
    "transfer_template": (
        "Multi-leg event templates that bundle two or more rails "
        "into one logical transfer (e.g. card-purchase = auth + "
        "post)."
    ),
    "chain": (
        "Parent → child dependencies between transfers — ACH "
        "settlement triggers GL clearing 1-2 days later, etc."
    ),
    "limit_schedule": (
        "Per-account limit windows the L1 invariants check against "
        "(e.g. customer-DDA NSF must be ≥ 0 by end-of-day)."
    ),
}


def _edit_h1_parts(
    kind: EntityKind,
    entity: object,
    entity_id: str,
    title_suffix: str,
) -> tuple[str, str]:
    """CG-followup.1+2 (2026-06-05) — compute the edit-page h1's
    visible HTML + the matching `<title>` detail slot.

    The list-card titles (CG.11, CG.12, CG.18) already chose a
    per-kind operator-readable display key:
    - account → kebab `id`, plus the `name` field as a secondary-fg
      span (CG.11 applied to card titles; mirrored here).
    - account_template / transfer_template / rail → entity_id is
      already operator-readable (role / name).
    - chain → parent only (the composite "Parent::children" stays on
      `data-entity-id` and the URL path; CG.12 lock).
    - limit_schedule → "{role} → {rail}" with direction parenthesized
      for clarity (CG.18 lock; the card title renders direction as a
      separate badge, but the title bar / h1 prose can absorb it).

    Pre-followup the h1 read `Edit account — Cash...: gl-..` (em-dash
    + colon, ambiguous) and chain / limit_schedule h1s leaked the
    full composite. This helper unifies on middle-dot separators in
    both the h1 prose and the CG.21 title detail.

    Returns (h1_inner_html, title_detail_text). The h1 carries any
    `<span>` chrome (escape-safe). The title detail is a plain
    string (callers escape it at the `<title>` interpolation).
    """
    singular = kind_label_singular(kind)
    if kind == "chain":
        parent_attr = getattr(entity, "parent", None)
        display_key = str(parent_attr) if parent_attr is not None else entity_id
        h1_inner = (
            f"Edit {escape(singular)} · {escape(display_key)}"
            f"{escape(title_suffix)}"
        )
        title_detail = (
            f"Edit {singular} · {display_key}{title_suffix}"
        )
        return h1_inner, title_detail
    if kind == "limit_schedule":
        parent_role_attr = getattr(entity, "parent_role", None)
        rail_attr = getattr(entity, "rail", None)
        direction_attr = getattr(entity, "direction", None)
        if parent_role_attr is not None and rail_attr is not None:
            display_key = f"{parent_role_attr} → {rail_attr}"
            if direction_attr is not None:
                display_key += f" ({direction_attr})"
        else:
            display_key = entity_id
        h1_inner = (
            f"Edit {escape(singular)} · {escape(display_key)}"
            f"{escape(title_suffix)}"
        )
        title_detail = (
            f"Edit {singular} · {display_key}{title_suffix}"
        )
        return h1_inner, title_detail
    # account / account_template / transfer_template / rail —
    # entity_id is already the operator-readable key.
    display_name_span = ""
    if kind == "account":
        name_attr = getattr(entity, "name", None)
        if name_attr is not None and str(name_attr).strip():
            name_cls = (
                "ml-2 text-base font-normal text-secondary-fg break-words"
            )
            display_name_span = (
                f' <span class="{name_cls}" '
                f'data-role="edit-h1-display-name">'
                f"{escape(str(name_attr))}</span>"
            )
    h1_inner = (
        f"Edit {escape(singular)} · {escape(entity_id)}"
        f"{escape(title_suffix)}{display_name_span}"
    )
    title_detail = (
        f"Edit {singular} · {entity_id}{title_suffix}"
    )
    return h1_inner, title_detail


def _htmx_head_block() -> str:
    """CG-bug.2 (2026-06-05) — htmx script tag + the X.4.e.5
    `beforeSwap` 4xx-as-data shim. Used by every editor page that
    carries `hx-*` attributes: list view, create form, edit form,
    singleton form, rail subtype picker, 404 chrome.

    Pre-fix: only the list view loaded htmx. The edit + create
    forms carry `hx-delete` (CG.19's Delete button) and the
    markdown-Preview tab's `hx-post` — both silently no-op'd
    because htmx wasn't on the page. Dogfood found the Preview
    no-op; the Delete button's no-op was latent.

    CO.2 (2026-06-06) — also pulls in Sortable.js + the
    multi-select chip-list bootstrap. The multi-select widget
    renders a draggable "Selected (in order)" chip list above the
    checkbox grid; Sortable powers the drag, and a small
    bootstrap syncs the hidden `<name>__order` input on every
    reorder + every checkbox toggle. The bootstrap is a no-op on
    pages that don't render any multi-select fields (e.g., the
    list view, the 404 chrome).
    """
    return (
        '<script src="https://unpkg.com/htmx.org@1.9.10"></script>\n'
        # CO.2 — Sortable.js vendored locally (was CDN, broke Playwright's
        # navigation-wait race; local serving is also less brittle in
        # offline / CI scenarios). Source: sortablejs@1.15.6.
        f'  <script src="{asset_url("/static/js/sortable.min.js")}"></script>\n'
        "  <script>\n"
        "    // X.4.e.5 fix — HTMX defaults to NOT swapping 4xx response bodies\n"
        "    // (treats them as errors). The validator returns 400 + an inline\n"
        "    // error fragment; we WANT that fragment swapped in so the user\n"
        "    // sees the error + their typed-but-invalid form content. Enable\n"
        "    // 4xx swaps explicitly. (5xx still treated as errors.)\n"
        "    // Attach to `document`, not `document.body` — this script runs in\n"
        "    // <head> before <body> is parsed, so document.body is null and\n"
        "    // .addEventListener would throw a TypeError. HTMX events bubble\n"
        "    // all the way up to document, so this catches them just the same.\n"
        "    document.addEventListener('htmx:beforeSwap', function(evt) {\n"
        "      var status = evt.detail.xhr.status;\n"
        "      if (status >= 400 && status < 500) {\n"
        "        evt.detail.shouldSwap = true;\n"
        "        evt.detail.isError = false;\n"
        "      }\n"
        "    });\n"
        "    // CO.3 — multi-select chip-list bootstrap. For each\n"
        "    // `[data-multiselect-order-list]` element on the page: wire\n"
        "    // Sortable.js for vertical drag-reorder, the × delete\n"
        "    // buttons inside each chip, and the typeahead `<input list>`\n"
        "    // below the list (datalist-driven autocomplete; pressing\n"
        "    // Enter or selecting from the dropdown adds a chip). Safe to\n"
        "    // run on pages without multi-selects — the querySelectorAll\n"
        "    // returns [].\n"
        "    function bootstrapMultiSelectOrder() {\n"
        "      document.querySelectorAll('[data-multiselect-order-list]').forEach(function(list) {\n"
        "        var name = list.getAttribute('data-multiselect-order-list');\n"
        "        var datalistId = list.getAttribute('data-multiselect-options-id');\n"
        "        var datalist = datalistId ? document.getElementById(datalistId) : null;\n"
        "        var addInput = document.querySelector('input[data-multiselect-add=\"' + name + '\"]');\n"
        "        function syncHints() {\n"
        "          // CO.3 — no `__order` field anymore; the per-chip hidden\n"
        "          // inputs carry both the values AND the order (DOM order).\n"
        "          // This callback only manages the empty-state hint.\n"
        "          var chipCount = list.querySelectorAll('[data-multiselect-order-value]').length;\n"
        "          var hint = list.querySelector('[data-multiselect-empty-hint]');\n"
        "          if (chipCount === 0 && !hint) {\n"
        "            var emptyLi = document.createElement('li');\n"
        "            emptyLi.className = 'text-xs italic text-secondary-fg px-2 py-1';\n"
        "            emptyLi.setAttribute('data-multiselect-empty-hint', '1');\n"
        "            emptyLi.textContent = 'No items selected yet — type below to add.';\n"
        "            list.appendChild(emptyLi);\n"
        "          } else if (chipCount > 0 && hint) {\n"
        "            hint.remove();\n"
        "          }\n"
        "        }\n"
        "        function removeFromDatalist(val) {\n"
        "          if (!datalist) return;\n"
        "          Array.prototype.forEach.call(\n"
        "            datalist.querySelectorAll('option'),\n"
        "            function(opt) { if (opt.value === val) opt.remove(); }\n"
        "          );\n"
        "        }\n"
        "        function addToDatalist(val) {\n"
        "          if (!datalist) return;\n"
        "          var opt = document.createElement('option');\n"
        "          opt.value = val;\n"
        "          datalist.appendChild(opt);\n"
        "        }\n"
        "        function datalistHas(val) {\n"
        "          if (!datalist) return true;\n"
        "          return Array.prototype.some.call(\n"
        "            datalist.querySelectorAll('option'),\n"
        "            function(opt) { return opt.value === val; }\n"
        "          );\n"
        "        }\n"
        "        var templateId = list.getAttribute('data-chip-template-id');\n"
        "        var template = templateId ? document.getElementById(templateId) : null;\n"
        "        function buildChipFromTemplate(val) {\n"
        "          // CO.3 — when the widget ships a <template> element\n"
        "          // (e.g. chain_children with its fan_in checkbox + epc\n"
        "          // input embedded per chip), clone it and substitute\n"
        "          // literal `__NAME__` placeholders. The template-driven\n"
        "          // path keeps widget-specific markup colocated with\n"
        "          // the server render rather than encoded in JS.\n"
        "          if (!template) return null;\n"
        "          var li = template.content.firstElementChild.cloneNode(true);\n"
        "          li.setAttribute('data-multiselect-order-value', val);\n"
        "          // Walk attribute + textContent strings and replace __NAME__.\n"
        "          var walker = document.createTreeWalker(li, NodeFilter.SHOW_ELEMENT, null);\n"
        "          do {\n"
        "            var el = walker.currentNode;\n"
        "            for (var i = 0; i < el.attributes.length; i++) {\n"
        "              var a = el.attributes[i];\n"
        "              if (a.value.indexOf('__NAME__') !== -1) {\n"
        "                el.setAttribute(a.name, a.value.split('__NAME__').join(val));\n"
        "              }\n"
        "            }\n"
        "            for (var j = 0; j < el.childNodes.length; j++) {\n"
        "              var n = el.childNodes[j];\n"
        "              if (n.nodeType === Node.TEXT_NODE && n.nodeValue.indexOf('__NAME__') !== -1) {\n"
        "                n.nodeValue = n.nodeValue.split('__NAME__').join(val);\n"
        "              }\n"
        "            }\n"
        "          } while (walker.nextNode());\n"
        "          return li;\n"
        "        }\n"
        "        function buildSimpleChip(val) {\n"
        "          var li = document.createElement('li');\n"
        "          li.className = 'flex items-center gap-2 bg-link-tint border border-surface-border rounded-sm px-2 py-1 text-sm';\n"
        "          li.setAttribute('data-multiselect-order-value', val);\n"
        "          var handle = document.createElement('span');\n"
        "          handle.setAttribute('aria-hidden', 'true');\n"
        "          handle.className = 'cursor-grab text-secondary-fg select-none';\n"
        "          handle.textContent = '\\u22EE';\n"
        "          var hiddenInput = document.createElement('input');\n"
        "          hiddenInput.type = 'hidden';\n"
        "          hiddenInput.name = name;\n"
        "          hiddenInput.value = val;\n"
        "          var label = document.createElement('span');\n"
        "          label.className = 'grow';\n"
        "          label.textContent = val;\n"
        "          var rmBtn = document.createElement('button');\n"
        "          rmBtn.type = 'button';\n"
        "          rmBtn.className = 'px-2 py-0.5 text-secondary-fg hover:text-danger hover:bg-red-50 rounded-sm cursor-pointer text-base leading-none';\n"
        "          rmBtn.setAttribute('aria-label', 'Remove ' + val);\n"
        "          rmBtn.setAttribute('data-multiselect-remove', val);\n"
        "          rmBtn.innerHTML = '&times;';\n"
        "          li.appendChild(handle);\n"
        "          li.appendChild(hiddenInput);\n"
        "          li.appendChild(label);\n"
        "          li.appendChild(rmBtn);\n"
        "          return li;\n"
        "        }\n"
        "        var allowFreeform = list.getAttribute('data-multiselect-allow-freeform') === '1';\n"
        "        function addChip(val) {\n"
        "          if (!val) return false;\n"
        "          var sel = '[data-multiselect-order-value=\"' + val.replace(/\"/g, '\\\\\"') + '\"]';\n"
        "          if (list.querySelector(sel)) return false;  // already present\n"
        "          if (!allowFreeform && !datalistHas(val)) return false;  // not a valid option\n"
        "          var hint = list.querySelector('[data-multiselect-empty-hint]');\n"
        "          if (hint) hint.remove();\n"
        "          var li = buildChipFromTemplate(val) || buildSimpleChip(val);\n"
        "          list.appendChild(li);\n"
        "          removeFromDatalist(val);\n"
        "          syncHints();\n"
        "          return true;\n"
        "        }\n"
        "        function removeChip(val) {\n"
        "          var sel = '[data-multiselect-order-value=\"' + val.replace(/\"/g, '\\\\\"') + '\"]';\n"
        "          var el = list.querySelector(sel);\n"
        "          if (!el) return;\n"
        "          el.remove();\n"
        "          addToDatalist(val);\n"
        "          syncHints();\n"
        "        }\n"
        "        if (typeof Sortable !== 'undefined') {\n"
        "          new Sortable(list, {\n"
        "            animation: 150,\n"
        "            handle: '.cursor-grab',\n"
        "            filter: '[data-multiselect-empty-hint]',\n"
        "            onEnd: syncHints,\n"
        "          });\n"
        "        }\n"
        "        // Delegated × button handler.\n"
        "        list.addEventListener('click', function(evt) {\n"
        "          var btn = evt.target.closest('[data-multiselect-remove]');\n"
        "          if (!btn) return;\n"
        "          removeChip(btn.getAttribute('data-multiselect-remove'));\n"
        "        });\n"
        "        // Typeahead add: fires when the operator picks a\n"
        "        // datalist option (input event with a matching value) OR\n"
        "        // presses Enter with a non-empty input.\n"
        "        if (addInput) {\n"
        "          addInput.addEventListener('input', function() {\n"
        "            var val = addInput.value.trim();\n"
        "            if (datalistHas(val) && addChip(val)) addInput.value = '';\n"
        "          });\n"
        "          addInput.addEventListener('keydown', function(evt) {\n"
        "            if (evt.key !== 'Enter') return;\n"
        "            evt.preventDefault();\n"
        "            var val = addInput.value.trim();\n"
        "            if (addChip(val)) addInput.value = '';\n"
        "          });\n"
        "        }\n"
        "      });\n"
        "    }\n"
        "    if (document.readyState === 'loading') {\n"
        "      document.addEventListener('DOMContentLoaded', bootstrapMultiSelectOrder);\n"
        "    } else {\n"
        "      bootstrapMultiSelectOrder();\n"
        "    }\n"
        "  </script>"
    )


def _form_page_header_html(title: str) -> str:
    """CG.7 (2026-06-05) — trainer-style header strip for the form
    pages (create / edit / singleton / subtype-picker). Title sits
    where the back-arrow used to live; the top-nav above provides
    navigation. Same h1 + 1-sentence-blurb shape as the list-page
    header from CG.6, except form pages don't carry a blurb — the
    `_render_intro_details(intro_html)` box inside the main content
    already carries the detailed orientation copy."""
    return (
        f'<header class="px-8 py-4 border-b border-surface-border bg-white">'
        f'<h1 class="text-xl font-semibold m-0">{escape(title)}</h1>'
        f"</header>"
    )


def _form_page_header_raw_html(h1_inner_html: str) -> str:
    """CG-followup.1 (2026-06-05) — same trainer-style header strip
    as `_form_page_header_html` but the caller hands over the h1's
    inner HTML pre-escaped + pre-chrome-attached. Used by
    `_render_edit_page` so the edit-h1 can carry a typed
    display-name `<span>` next to the kebab id (CG.11 pattern),
    which the escape-everything wrapper would mangle."""
    return (
        f'<header class="px-8 py-4 border-b border-surface-border bg-white">'
        f'<h1 class="text-xl font-semibold m-0">{h1_inner_html}</h1>'
        f"</header>"
    )


def _render_unknown_kind_page(
    raw_kind: str, top_nav_html: str, instance: Any,  # typing-smell: ignore[explicit-any]: L2Instance type concrete import creates circular dep with common.l2
) -> str:
    """CG.20 (2026-06-05) — chrome a 404-style "kind not editable"
    page so a stale bookmark / runbook link / browser-history entry
    pointing at e.g. `/l2_shape/persona/` lands on a recoverable
    page (top-nav + L2 Editor link) instead of a bare
    `<h1>404</h1><p>persona is not an editable entity kind</p>`
    dead-end. The page reads in the studio's voice: explains why
    this kind isn't editable AND offers a one-click bounce back to
    the home page."""
    safe_kind = escape(raw_kind)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Recon-Gen · Studio · 404</title>
  {studio_theme_head(instance)}
  {_htmx_head_block()}
</head>
<body class="block min-h-screen font-sans bg-surface-bg text-primary-fg">
  {top_nav_html}
  <header class="px-8 py-4 border-b border-surface-border bg-white">
    <h1 class="text-xl font-semibold m-0">Page not found</h1>
    <p class="text-sm text-secondary-fg max-w-3xl m-0 mt-1">
      <code class="font-mono">{safe_kind}</code> isn't an editable
      kind in the L2 editor. It may have been retired, or the link
      you followed is stale.
    </p>
  </header>
  <main class="max-w-4xl mx-auto pt-6 px-4 pb-12 flex flex-col gap-4">
    <section class="bg-white border border-surface-border rounded-md p-5">
      <p class="text-sm m-0">
        Head back to the
        <a class="text-accent no-underline hover:underline" href="/">L2 Editor</a>
        to browse the kinds that do edit here, or jump to the
        <a class="text-accent no-underline hover:underline" href="/diagram">diagram</a>
        to see the L2 shape end-to-end.
      </p>
    </section>
  </main>
</body>
</html>
"""


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
        "URLs and on every transaction row's <code>account_id</code>) "
        "and <code>scope</code> (institution-internal vs external "
        "counterparty — controls limit + drift visibility). "
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
        "<code>(rail_name, source_role, destination_role)</code>.</p>"
        "<p>Required: <code>name</code> (unique identifier; chains, "
        "templates, and limit schedules all reference rails by this "
        "name — it doubles as the L1 matview's <code>rail_name</code> "
        "column value). Endpoint roles (<code>source_role</code> / "
        "<code>destination_role</code>) are edited on the rail itself "
        "after it's created — required for the validator to accept the "
        "rail as connected.</p>"
    ),
    "transfer_template": (
        "<p><strong>A TransferTemplate</strong> is a multi-leg event — "
        "several Rail firings that the L1 layer expects to balance to "
        "<code>expected_net</code> by <code>completion</code>. Settlement "
        "cycles, return-bundle reconciliations, anything that's not just "
        "one rail firing on its own.</p>"
        "<p>Required: <code>name</code>, <code>expected_net</code> "
        "(often 0 for fully-balanced cycles; fees may sum to a non-zero "
        "target), <code>completion</code> (the deadline expression like "
        "<code>business_day_end+1d</code>). <code>leg_rails</code> is "
        "edited after creation.</p>"
    ),
    "chain": (
        "<p><strong>A Chain row</strong> says: when this <em>parent</em> "
        "rail or template fires, the L1 layer expects one of the listed "
        "<em>children</em> to follow within the SLA. A row with one "
        "child encodes a required relationship; a row with two or more "
        "children encodes an XOR (exactly-one-of) branch. Either way, a "
        "parent firing without a matching child surfaces as a "
        "stuck-pending invariant violation.</p>"
        "<p>Required: <code>parent</code> (rail or template name) and "
        "<code>children</code> (a list of one or more rail / template "
        "names). One name in the list = required; multiple names = "
        "exactly-one-of branching (e.g. ACH return reasons).</p>"
    ),
    "limit_schedule": (
        "<p><strong>A LimitSchedule</strong> is a daily $-cap on flow "
        "from a parent role for a given rail and direction. Any day "
        "exceeding the cap surfaces as an L1 limit-breach violation.</p>"
        "<p>Required: <code>parent_role</code> (the role whose flow is "
        "capped), <code>rail</code> (the Rail name the cap applies to), "
        "<code>cap</code> (the $ ceiling), and <code>direction</code> "
        "(<code>Outbound</code> for classic send caps, <code>Inbound</code> "
        "for AML / structuring thresholds on inbound volume — AB.1).</p>"
        "<p>The same <code>(parent_role, rail)</code> pair may carry "
        "<em>both</em> an Outbound and an Inbound LimitSchedule — they "
        "show up on different branches of the L1 Limit Breach matview.</p>"
    ),
}


# X.4.f.11.5 — Rail create flow is a 2-step picker → form, because
# Rail is a discriminated union (TwoLegRail | SingleLegRail) with
# different load-bearing fields per subtype. Step 1 is a picker page
# (no form fields, two big buttons); step 2 is the create form
# filtered to the chosen subtype's fields plus a hidden subtype input
# the POST handler reads to dispatch the right constructor.
# BF.1 (2026-05-25) — subtype-specific requirements banner on the
# rail create + edit pages. Sits above the field list, beneath the
# generic `_CREATE_INTRO_BY_KIND["rail"]` paragraph that explains
# what a Rail IS. Banner purpose: surface the subtype-specific
# checklist up-front so the operator doesn't reverse-engineer the
# rules from validator errors after a failed submit (per the AI.2.e
# part-2 brief). Additive — does NOT replace the per-kind intro or
# the per-field `*` markers (per AM/BF L4).
_RAIL_SUBTYPE_REQUIREMENTS_BANNER: Mapping[RailSubtype, str] = {
    "two_leg": (
        "<p><strong>Two-leg rail requires:</strong></p>"
        "<ul>"
        "<li><code>source_role</code> + <code>destination_role</code> "
        "— Role(s) the debit + credit legs post to. Use multi-select "
        "for unioned roles (validator F1).</li>"
        "<li><code>expected_net</code> — the standalone-firing "
        "Conservation contract. Typically <code>0</code>; leave blank "
        "ONLY when this rail is exclusively used as a TransferTemplate "
        "leg (the template owns the bundle's net then).</li>"
        "</ul>"
        "<p>Both legs auto-balance to <code>expected_net</code> by "
        "the template's <code>completion</code> deadline (or on the "
        "rail's own firing when standalone).</p>"
    ),
    "single_leg": (
        "<p><strong>Single-leg rail requires:</strong></p>"
        "<ul>"
        "<li><code>leg_role</code> + <code>leg_direction</code> "
        "(<code>Debit</code> / <code>Credit</code> / <code>Variable</code>).</li>"
        "<li>A <strong>reconciler attachment</strong> — non-aggregating "
        "single-legs can't reconcile their own drift (SPEC §S3 + C3). "
        "Either attach to a TransferTemplate (closes its "
        "<code>expected_net</code>) or to an aggregating Rail "
        "(sweeps into its bundle). The Reconciler picker fieldset "
        "below handles this atomically with the save (BB.1). "
        "<em>Exception:</em> aggregating single-legs are self-"
        "reconciling and skip this requirement.</li>"
        "<li><strong>Variable-direction</strong> single-legs MUST "
        "attach to a TransferTemplate (validator C3); they can't "
        "sweep into an aggregating Rail. The template's "
        "<code>expected_net</code> determines this leg's amount at "
        "firing time.</li>"
        "</ul>"
    ),
}


def _render_intro_details(intro_html: str) -> str:
    """Wrap a per-kind / per-singleton intro card in a collapsible
    ``<details>`` so it doesn't push the actual form down the page
    (user 2026-05-25, dogfood: the dense theme structured form +
    the always-visible intro card made the page very tall). Native
    HTML — no JS required for the toggle. Default-closed; operator
    clicks "ⓘ Reference" to expand."""
    return (
        '<details class="bg-white border border-surface-border '
        'rounded-md overflow-hidden">'
        '<summary class="cursor-pointer px-5 py-3 font-semibold '
        'text-accent bg-surface-bg select-none hover:bg-link-tint">'
        'ⓘ Reference'
        '</summary>'
        f'<div class="px-5 py-4 text-sm leading-normal text-primary-fg">{intro_html}</div>'
        '</details>'
    )


def _render_subtype_requirements_banner(subtype: RailSubtype | None) -> str:
    """Render the BF.1 banner. Empty string when subtype is None
    (non-rail entities; rail-subtype-picker landing page)."""
    if subtype is None:
        return ""
    prose = _RAIL_SUBTYPE_REQUIREMENTS_BANNER.get(subtype, "")
    if not prose:
        return ""
    # bg-link-tint is the AM-landed pale-accent surface (matches
    # `_CREATE_INTRO_BY_KIND` card but with an accent tint so the
    # subtype-specific checklist visually distinguishes from the
    # generic intro). border-accent/25 + an accent left rule mark
    # this as "the requirements" surface.
    return (
        '<section aria-label="Subtype requirements" '
        'class="bg-link-tint border border-accent/25 border-l-4 '
        'border-l-accent rounded-md px-4 py-3 text-sm leading-normal '
        'text-primary-fg mb-3">'
        f"{prose}"
        "</section>"
    )


_RAIL_SUBTYPE_PICKER_INTRO: str = (
    "<p><strong>Pick the rail subtype first.</strong> A Rail is one of "
    "two shapes — they have different fields, so we need to know which "
    "before showing the form.</p>"
    "<ul>"
    "<li><strong>Two-leg rail</strong> — produces two transaction legs "
    "(debit + credit) per firing. Use this for transfers between two "
    "accounts (ACH, wire, internal transfer, settlement sweep).</li>"
    "<li><strong>Single-leg rail</strong> — produces one transaction "
    "leg per firing. Use this for fees, charges, single-sided postings, "
    "or rails reconciled by a containing TransferTemplate's "
    "ExpectedNet.</li>"
    "</ul>"
)


def _render_rail_subtype_picker(
    instance: Any,  # typing-smell: ignore[explicit-any]: L2Instance — needed to thread the L2 theme override into the page <head>
    *,
    from_param: str | None = None,
    prefill_name: str = "",
    top_nav_html: str = "",
) -> str:
    """The Rail-only subtype picker landing page.

    Step 1 of the 2-step create flow. Two big buttons, each linking to
    ``/l2_shape/rail/new?subtype=<two_leg|single_leg>`` so the picked
    subtype shows up as a query param on step 2 (back button works).

    BTb.1 — when arriving with ``?from=/etl/triage`` (Triage-CTA flow),
    render the back-breadcrumb above the picker AND propagate ``?from=``
    on each subtype link so the eventual step-2 create page keeps the
    carryover. Without this propagation the breadcrumb is dropped on
    step 1 (cold-read v3 finding: picker page shows generic "back to
    Studio" but not "Back to Triage").
    """
    # AM.1 step 7 — rail-subtype-picker migrated. .rail-subtype-picker
    # + .rail-subtype-button drop in favor of a flex column with
    # two big anchor "cards" — each hover-tinted, focus-ringed for
    # keyboard nav.
    picker_btn_cls = (
        "block bg-white border border-surface-border rounded-md px-5 py-4 "
        "no-underline text-primary-fg cursor-pointer "
        "hover:border-accent hover:bg-link-tint "
        "focus:outline-2 focus:outline-accent focus:-outline-offset-1"
    )
    # Build the subtype links — append `?from=` when in a back-breadcrumb
    # flow so step 2 inherits the carryover.
    from_qs = ""
    if from_param is not None and _safe_back_target(from_param) is not None:
        from urllib.parse import quote  # noqa: PLC0415
        from_qs = f"&from={quote(from_param, safe='/')}"
    if prefill_name:
        from urllib.parse import quote  # noqa: PLC0415
        from_qs += f"&prefill_name={quote(prefill_name, safe='')}"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Recon-Gen · Studio · Editor · Create rail · pick subtype</title>
  {studio_theme_head(instance)}
  {_htmx_head_block()}
</head>
<body class="block min-h-screen font-sans bg-surface-bg text-primary-fg">
  {top_nav_html}
  {_form_page_header_html("Create new rail — pick subtype")}
  {_back_breadcrumb_html(from_param)}
  <main class="max-w-4xl mx-auto pt-6 px-4 pb-12 flex flex-col gap-4">
    {_render_intro_details(_RAIL_SUBTYPE_PICKER_INTRO)}
    <section class="bg-white border border-surface-border rounded-md p-5">
      <div class="flex flex-col gap-3">
        <a class="{picker_btn_cls}" href="/l2_shape/rail/new?subtype=two_leg{from_qs}">
          <strong class="block text-base text-accent mb-1">Two-leg rail →</strong>
          <small class="block text-sm text-secondary-fg">Debit + credit per firing (ACH, wire, internal, settlement)</small>
        </a>
        <a class="{picker_btn_cls}" href="/l2_shape/rail/new?subtype=single_leg{from_qs}">
          <strong class="block text-base text-accent mb-1">Single-leg rail →</strong>
          <small class="block text-sm text-secondary-fg">One leg per firing (fee, charge, sub-template leg)</small>
        </a>
      </div>
    </section>
  </main>
</body>
</html>
"""


def _apply_xor_groups_update_to_tt(
    instance: Any,  # typing-smell: ignore[explicit-any]: L2Instance — mutated through mutate_l2
    *,
    tt_name: str,
    form: Any,  # typing-smell: ignore[explicit-any]: starlette FormData — reads leg_rail_xor_groups_<i> multi values
) -> Any:  # typing-smell: ignore[explicit-any]: L2Instance — same shape
    """BB.3 — apply a leg_rail_xor_groups update to a TT, used during
    attach to satisfy C1 when multiple Variable rails accumulate.

    Form payload (matches the wire shape of edit_xor_groups_form_data
    on the driver side):

      leg_rail_xor_groups__present      = "1"
      leg_rail_xor_groups__num_groups   = "N"
      leg_rail_xor_groups_0             = [rail_name, rail_name, ...]
      ...
      leg_rail_xor_groups_(N-1)         = [rail_name, ...]
    """
    from recon_gen.common.l2.primitives import Identifier

    raw_n = form.get("leg_rail_xor_groups__num_groups")
    if raw_n is None:
        return instance
    try:
        n = int(str(raw_n))
    except ValueError:
        raise ValueError(
            f"leg_rail_xor_groups__num_groups={raw_n!r} not an int"
        ) from None
    groups: list[tuple[Identifier, ...]] = []
    for i in range(n):
        rail_names = form.getlist(f"leg_rail_xor_groups_{i}")
        if not rail_names:
            continue
        groups.append(tuple(Identifier(str(r)) for r in rail_names))
    return mutate_l2(
        instance, "transfer_template", tt_name,
        {"leg_rail_xor_groups": tuple(groups)},
    )


def _create_new_reconciler_with_rail(
    instance: Any,  # typing-smell: ignore[explicit-any]: L2Instance — mutated through create_l2_entity
    *,
    new_rail_name: str,
    reconciler_kind: str,
    form: Any,  # typing-smell: ignore[explicit-any]: starlette FormData — structural multi_items / getlist surface, no shared Protocol across uvicorn/TestClient form impls
) -> Any:  # typing-smell: ignore[explicit-any]: L2Instance — same shape
    """BB.3 — create a new reconciler (TT or aggregating Rail) that
    contains ``new_rail_name``, as the second half of the composite
    rail-with-create-new atomic mutation.

    Form payload (prefixed with ``reconciler_new_``):
      - reconciler_new_name: name of the new reconciler
      - For ``reconciler_kind == 'transfer_template'``:
        - reconciler_new_transfer_key: optional (comma-separated)
        - reconciler_new_completion: optional
      - For ``reconciler_kind == 'aggregating_rail'``:
        - reconciler_new_subtype: 'single_leg' | 'two_leg'
        - reconciler_new_cadence: required
        - reconciler_new_leg_role / source_role / destination_role
        - reconciler_new_leg_direction (single_leg)
        - reconciler_new_origin / source_origin / destination_origin
        - reconciler_new_expected_net (two_leg)
      - (any other reconciler-kind's FieldSpec field can be provided)

    The rail-list field (leg_rails for TT, bundles_activity for
    aggregating Rail) is computed server-side: the new rail's name
    is appended to whatever the form's prefixed multi_select provided
    (typically empty for the first occupant; subsequent rails use
    attach-existing).
    """
    from starlette.datastructures import FormData as _FD, UploadFile
    from recon_gen.common.l2.primitives import Identifier

    # Strip the prefix; build a Starlette FormData over the sub-payload.
    # BF.1.S2: FormData expects `list[tuple[str, str | UploadFile]]`;
    # list type param is invariant so `list[tuple[str, str]]` doesn't satisfy.
    prefix = "reconciler_new_"
    sub_items: list[tuple[str, str | UploadFile]] = []
    for k, v in form.multi_items():
        if k.startswith(prefix):
            sub_items.append((k[len(prefix):], str(v)))
    sub_form = _FD(sub_items)

    # BF.1.S2: ``sub_fields`` is dict[str, object] (heterogeneous coerced form);
    # the ``or ()`` collapse + tuple() conversion go through ``object`` so
    # pyright can't see the tuple-iterable narrowing — cast + suppress the
    # corresponding upstream errors.
    from typing import cast as _cast
    if reconciler_kind == "transfer_template":
        target_kind: EntityKind = "transfer_template"
        sub_fields, _overrides = _coerce_form(target_kind, sub_form)
        existing: tuple[Identifier, ...] = _cast(
            "tuple[Identifier, ...]",
            tuple(sub_fields.get("leg_rails") or ()),  # pyright: ignore[reportArgumentType]: sub_fields stores leg_rails as tuple[Identifier, ...] but typed as object
        )
        sub_fields["leg_rails"] = (*existing, Identifier(new_rail_name))
        return create_l2_entity(instance, target_kind, sub_fields)
    if reconciler_kind == "aggregating_rail":
        target_kind = "rail"
        # Force aggregating + subtype. The driver MUST supply subtype.
        sub_fields, _overrides = _coerce_form(target_kind, sub_form)
        sub_fields["aggregating"] = True
        subtype = sub_form.get("subtype")
        if subtype is None:
            raise ValueError(
                "reconciler_new_subtype required when reconciler_kind="
                "'aggregating_rail' (single_leg | two_leg)"
            )
        sub_fields["subtype"] = str(subtype)
        existing = _cast(
            "tuple[Identifier, ...]",
            tuple(sub_fields.get("bundles_activity") or ()),  # pyright: ignore[reportArgumentType]: sub_fields stores bundles_activity as tuple[Identifier, ...] but typed as object
        )
        sub_fields["bundles_activity"] = (
            *existing, Identifier(new_rail_name),
        )
        return create_l2_entity(instance, target_kind, sub_fields)
    raise ValueError(
        f"reconciler_kind={reconciler_kind!r} not recognized "
        f"(expected 'transfer_template' or 'aggregating_rail')"
    )


def _render_reconciler_section(
    instance: Any,  # typing-smell: ignore[explicit-any]: L2Instance — read .transfer_templates / .rails for the picker options
    overrides: Mapping[str, str | tuple[str, ...]],
) -> str:
    """BB.2 — render the Reconciler picker for non-aggregating single-leg
    rails. Pairs with BB.1's create-POST handler reading
    ``reconciler_mode`` + ``reconciler_kind`` + (``reconciler_name`` |
    ``reconciler_new_*``) from the form.

    Two top-level modes (radio):
    - **Attach existing** (default) — pick a kind + a name from a
      grouped dropdown of declared TTs / aggregating Rails. Mismatched
      picks (TT name with aggregating_rail kind) return 400.
    - **Create new** — inline mini-form for the new reconciler's
      minimum required fields per kind. The new reconciler is created
      with the new rail auto-appended to its rail-list (`leg_rails`
      for TT; `bundles_activity` for aggregating Rail) — see
      ``_create_new_reconciler_with_rail``.

    Variable-direction sub-case (validator C3): aggregating Rails
    can't reconcile Variable. The server enforces; the UI shows
    helper text. A future refinement could disable the
    aggregating_rail radio when the operator picks
    leg_direction=Variable (client-side JS), kept out for the
    minimal-JS posture.
    """
    rmode_override = str(overrides.get("reconciler_mode") or "attach")
    rk_override = str(overrides.get("reconciler_kind") or "")
    rn_override = str(overrides.get("reconciler_name") or "")
    # Create-new sub-form: only the discriminators (name + subtype)
    # are read explicitly here. Every other field is driven from
    # `_FIELD_SPECS_BY_KIND` via `_render_field` with the
    # `reconciler_new_` prefix — overrides flow through that loop.
    rnn_name_override = str(overrides.get("reconciler_new_name") or "")
    rnn_subtype_override = str(overrides.get("reconciler_new_subtype") or "")

    tt_names = sorted(str(t.name) for t in instance.transfer_templates)
    agg_names = sorted(
        str(r.name) for r in instance.rails if r.aggregating
    )

    kind_options = [
        ("transfer_template", "Attach to TransferTemplate (closes its expected_net)"),
        ("aggregating_rail", "Attach to aggregating Rail (sweeps into its bundle)"),
    ]
    kind_html = "".join(
        f'<option value="{escape(v)}"'
        f'{" selected" if rk_override == v else ""}>{escape(lbl)}</option>'
        for v, lbl in kind_options
    )

    def _opt(n: str, override: str) -> str:
        sel = " selected" if override == n else ""
        return f'<option value="{escape(n)}"{sel}>{escape(n)}</option>'

    tt_opts = "".join(_opt(n, rn_override) for n in tt_names) or '<option disabled>(no TransferTemplates declared yet)</option>'
    agg_opts = "".join(_opt(n, rn_override) for n in agg_names) or '<option disabled>(no aggregating Rails declared yet)</option>'

    attach_checked = " checked" if rmode_override != "create_new" else ""
    create_checked = " checked" if rmode_override == "create_new" else ""
    attach_hidden = ' hidden' if rmode_override == "create_new" else ''
    create_hidden = ' hidden' if rmode_override != "create_new" else ''

    subtype_opts = "".join(
        f'<option value="{v}"{" selected" if rnn_subtype_override == v else ""}>{v}</option>'
        for v in ("single_leg", "two_leg")
    )

    # BF.2 — drive the create-new sub-form from `_FIELD_SPECS_BY_KIND`
    # so every TT / Rail FieldSpec is exposed (anti-drift gate per
    # BF.0 Lock L1). Skip: discriminators (name) + server-appended
    # rail-list (leg_rails for TT, bundles_activity for aggregator)
    # + edit_only fields whose option universe depends on a sibling
    # the operator must author first (leg_rail_xor_groups). The
    # `aggregating` flag is forced True server-side, so skip.
    _PREFIX = "reconciler_new_"

    def _render_specs(specs: Iterable[FieldSpec]) -> str:
        return "".join(
            _render_field(
                _prefixed_field_spec(s, _PREFIX),
                overrides.get(f"{_PREFIX}{s.name}", ""),
                instance,
            )
            for s in specs
        )

    _TT_SKIP = {"name", "leg_rails"}
    _RAIL_SKIP = {"name", "aggregating", "bundles_activity"}
    # BF.4 — `edit_only` fields (leg_rail_xor_groups, metadata_value_examples)
    # depend on sibling fields that don't exist at create-new time;
    # same convention as `_render_create_page` (their staged-edit
    # banners live on the entity edit page once siblings are saved).
    tt_fields_html = _render_specs(
        s for s in _FIELD_SPECS_BY_KIND["transfer_template"]
        if s.name not in _TT_SKIP and not s.edit_only
    )
    agg_subtype_agnostic_html = _render_specs(
        s for s in _FIELD_SPECS_BY_KIND["rail"]
        if s.subtype_only is None
        and s.name not in _RAIL_SKIP
        and not s.edit_only
    )
    agg_single_leg_html = _render_specs(
        s for s in _FIELD_SPECS_BY_KIND["rail"]
        if s.subtype_only == "single_leg"
    )
    agg_two_leg_html = _render_specs(
        s for s in _FIELD_SPECS_BY_KIND["rail"]
        if s.subtype_only == "two_leg"
    )

    # AM.1 step 4.5 — reconciler-section migrated. Semantic classes
    # (.reconciler-section, .reconciler-helper, .reconciler-mode-row,
    # .reconciler-mode-label, .reconciler-attach-block,
    # .reconciler-create-block, .reconciler-new-{tt,agg,single-leg,two-leg}-fields)
    # all retire to raw Tailwind utilities + the field_row_classes()
    # helper. data-* hooks stay for the inline JS show/hide.
    fieldset_cls = (
        "border border-surface-border rounded-md p-4 my-3 "
        "bg-surface-bg flex flex-col gap-3"
    )
    legend_cls = (
        "px-2 font-semibold text-sm text-primary-fg"
    )
    helper_p_cls = "text-sm text-secondary-fg m-0"
    mode_row_cls = "flex flex-wrap gap-4"
    mode_label_cls = (
        "flex items-center gap-2 font-normal text-sm cursor-pointer "
        "text-primary-fg"
    )
    block_cls = (
        "flex flex-col gap-3 pl-3 border-l-2 border-surface-border"
    )
    sub_block_cls = (
        "flex flex-col gap-3 px-3 py-2 border border-dashed "
        "border-surface-border rounded-sm bg-white"
    )
    label_cls = "font-semibold text-xs text-primary-fg"
    helper_small_cls = "text-xs text-secondary-fg"
    req = '<span class="text-danger"> *</span>'
    input_cls = field_input_classes()
    row_cls = field_row_classes()
    return (
        f'<fieldset class="{fieldset_cls}">'
        f'<legend class="{legend_cls}">Reconciler{req}</legend>'
        f'<p class="{helper_p_cls}">'
        "Non-aggregating single-leg rails don't reconcile their own drift. "
        "Per SPEC §S3 + C3, this rail must attach to a reconciler — either "
        "a TransferTemplate (closes its expected_net) OR an aggregating Rail "
        "(gets swept into its bundle). Save commits the rail + the reconciler "
        "mutation atomically (BB.1) — either both land or neither. "
        "Variable-direction single-legs must attach to a TransferTemplate "
        "only (per validator C3)."
        "</p>"
        # Mode radio — Attach existing vs Create new.
        f'<div class="{mode_row_cls}">'
        f'<label class="{mode_label_cls}">'
        f'<input type="radio" name="reconciler_mode" value="attach" data-reconciler-mode="attach"{attach_checked}>'
        ' Attach to existing reconciler</label>'
        f'<label class="{mode_label_cls}">'
        f'<input type="radio" name="reconciler_mode" value="create_new" data-reconciler-mode="create_new"{create_checked}>'
        ' Create new reconciler</label>'
        '</div>'
        # Attach-existing sub-form (kind + name dropdowns).
        f'<div class="{block_cls}" data-reconciler-block="attach"{attach_hidden}>'
        f'<div class="{row_cls}">'
        f'<label for="field-reconciler_kind" class="{label_cls}">Reconciler kind</label>'
        f'<select id="field-reconciler_kind" name="reconciler_kind" '
        f'aria-label="Reconciler kind" class="{input_cls}">'
        f'<option value=""{" selected" if not rk_override else ""}>— pick —</option>'
        f'{kind_html}'
        '</select>'
        '</div>'
        f'<div class="{row_cls}">'
        f'<label for="field-reconciler_name" class="{label_cls}">Reconciler name</label>'
        f'<select id="field-reconciler_name" name="reconciler_name" '
        f'aria-label="Reconciler name" class="{input_cls}">'
        f'<option value=""{" selected" if not rn_override else ""}>— pick —</option>'
        f'<optgroup label="TransferTemplates">{tt_opts}</optgroup>'
        f'<optgroup label="Aggregating Rails">{agg_opts}</optgroup>'
        '</select>'
        f'<small class="{helper_small_cls}">Pick from the optgroup matching the kind above; mismatches return 400.</small>'
        '</div>'
        # BF.3 — XOR groups land on the TT edit page after the rail
        # saves. The chicken-egg is real: XOR-group options =
        # destination TT's leg_rails, which we don't know at
        # form-render time (operator hasn't picked the TT yet, and
        # this new rail isn't in any TT's leg_rails until save). The
        # AI.10 textarea was the MVP shortcut; per the BF.0 spike's
        # locked answer P3, replace it with the staged-edit banner
        # for consistency with the TT-edit-page XOR picker's
        # empty-state copy.
        f'{_render_staged_edit_banner("XOR groups (TT only, optional)", "Save the rail first, then open the destination TT for editing to add XOR groups. Required only when attaching a Variable-direction rail to a TT that already has another Variable rail (SPEC C1 partitioning).")}'
        '</div>'
        # Create-new sub-form (kind + new-name + per-kind required minima).
        f'<div class="{block_cls}" data-reconciler-block="create_new"{create_hidden}>'
        f'<p class="{helper_p_cls}">'
        "The new reconciler is created in the same atomic save as the rail. "
        "Required fields below match the reconciler kind's validator minimum "
        "(rejected POSTs re-render with an inline error)."
        "</p>"
        f'<div class="{row_cls}">'
        f'<label for="field-reconciler_new_name" class="{label_cls}">New reconciler name{req}</label>'
        f'<input type="text" id="field-reconciler_new_name" name="reconciler_new_name" value="{escape(rnn_name_override)}" class="{input_cls}">'
        '</div>'
        f'<div class="{row_cls}">'
        f'<label for="field-reconciler_new_kind" class="{label_cls}">New reconciler kind{req}</label>'
        f'<select id="field-reconciler_new_kind" name="reconciler_new_kind" '
        f'aria-label="New reconciler kind" '
        f'data-reconciler-new-kind class="{input_cls}">'
        f'<option value=""{" selected" if not rk_override else ""}>— pick —</option>'
        f'<option value="transfer_template"{" selected" if rk_override == "transfer_template" else ""}>TransferTemplate</option>'
        f'<option value="aggregating_rail"{" selected" if rk_override == "aggregating_rail" else ""}>Aggregating Rail</option>'
        '</select>'
        f'<small class="{helper_small_cls}">Server reads `reconciler_kind` from this when mode=create_new.</small>'
        '</div>'
        # BF.2 — TT-kind fields, FieldSpec-driven (see `tt_fields_html`
        # build above for the skip-set). Adding a TransferTemplate
        # FieldSpec auto-surfaces here.
        f'<div class="{sub_block_cls}" data-reconciler-new-kind-fields="transfer_template"{"" if rk_override == "transfer_template" else " hidden"}>'
        f'{tt_fields_html}'
        '</div>'
        # BF.2 — Aggregating-rail-kind fields. Subtype dispatcher
        # stays hand-coded (it's a creation-time discriminator, not
        # a Rail dataclass field). Subtype-agnostic + per-subtype
        # field blocks are FieldSpec-driven.
        f'<div class="{sub_block_cls}" data-reconciler-new-kind-fields="aggregating_rail"{"" if rk_override == "aggregating_rail" else " hidden"}>'
        f'<div class="{row_cls}">'
        f'<label for="field-reconciler_new_subtype" class="{label_cls}">Subtype{req}</label>'
        f'<select id="field-reconciler_new_subtype" name="reconciler_new_subtype" '
        f'aria-label="Aggregating rail subtype" '
        f'data-reconciler-new-subtype class="{input_cls}">'
        f'<option value=""{" selected" if not rnn_subtype_override else ""}>— pick —</option>'
        f'{subtype_opts}'
        '</select>'
        '</div>'
        # Subtype-agnostic rail fields (description, origin, cadence,
        # metadata_keys, posted_requirements, aging windows, etc.)
        f'{agg_subtype_agnostic_html}'
        # Single-leg sub-block (leg_role + leg_direction).
        f'<div class="{sub_block_cls}" data-reconciler-new-subtype-fields="single_leg"{"" if rnn_subtype_override == "single_leg" else " hidden"}>'
        f'{agg_single_leg_html}'
        '</div>'
        # Two-leg sub-block (source_role + destination_role +
        # source_origin + destination_origin + expected_net). Per
        # validator: standalone two-leg rail MUST declare expected_net.
        f'<div class="{sub_block_cls}" data-reconciler-new-subtype-fields="two_leg"{"" if rnn_subtype_override == "two_leg" else " hidden"}>'
        f'{agg_two_leg_html}'
        '</div>'
        '</div>'
        '</div>'
        # Tiny vanilla-JS show/hide for the radio + per-kind/subtype.
        # Kept inline + dependency-free per the studio's minimal-JS
        # posture; falls back to "all sections visible" if disabled
        # (server still validates so the operator can't ship bad
        # state — the UX is just messier).
        #
        # AI.13 (2026-05-25): `setBlockHidden` toggles `disabled` on
        # contained inputs in lock-step with `hidden`. Required
        # because the BB.2 form has fields with duplicate `name`
        # values across kind/subtype blocks (e.g.
        # `reconciler_new_expected_net` is in BOTH the TT-kind block
        # AND the aggregator-two-leg block — operator fills the
        # visible one, the hidden one's empty value would otherwise
        # ALSO submit and the server's `FormData.get()` would return
        # whichever comes first in document order). Disabling hidden
        # inputs ensures only the visible/active one submits.
        '<script>'
        "(function(){"
        "const fs=document.currentScript.parentElement;"
        # setBlockHidden(block, hide) — toggle hidden + disable
        # contained inputs in lock-step so hidden inputs don't
        # submit duplicate-name values.
        "function setBlockHidden(b, hide){"
        "b.hidden = hide;"
        "b.querySelectorAll('input,select,textarea').forEach(el=>{"
        "el.disabled = hide;"
        "});"
        "}"
        # Mode radio toggle (attach vs create_new blocks).
        "fs.querySelectorAll('[data-reconciler-mode]').forEach(r=>{"
        "r.addEventListener('change',()=>{"
        "fs.querySelectorAll('[data-reconciler-block]').forEach(b=>{"
        "setBlockHidden(b, b.dataset.reconcilerBlock !== r.value);"
        "});"
        "});"
        "});"
        # Create-new-kind dropdown toggle (TT vs aggregator field-sets).
        "const k=fs.querySelector('[data-reconciler-new-kind]');"
        "if(k){k.addEventListener('change',()=>{"
        "fs.querySelectorAll('[data-reconciler-new-kind-fields]').forEach(b=>{"
        "setBlockHidden(b, b.dataset.reconcilerNewKindFields !== k.value);"
        "});"
        # Mirror create-new-kind into reconciler_kind so the BB.1
        # server reads the same field for both modes.
        "const rk=fs.querySelector('[name=reconciler_kind]');"
        "if(rk){rk.value=k.value;}"
        "});}"
        # Create-new subtype toggle (single_leg vs two_leg field-sets).
        "const st=fs.querySelector('[data-reconciler-new-subtype]');"
        "if(st){st.addEventListener('change',()=>{"
        "fs.querySelectorAll('[data-reconciler-new-subtype-fields]').forEach(b=>{"
        "setBlockHidden(b, b.dataset.reconcilerNewSubtypeFields !== st.value);"
        "});"
        # The server's _create_new_reconciler_with_rail reads
        # `reconciler_new_subtype` directly via sub_form.get('subtype'),
        # so mirror create-side subtype into the bare `subtype` key
        # under the prefix.
        "});}"
        # AI.13 (2026-05-25) — init pass: sync `disabled` state on
        # every block that's already `[hidden]` at first render. The
        # change-listeners above only fire on operator interaction;
        # without this init, hidden inputs on page-load would still
        # submit their (empty) value, colliding with duplicate-name
        # fields in visible blocks (e.g. `reconciler_new_expected_net`
        # in BOTH TT-kind and aggregator-two-leg blocks).
        "fs.querySelectorAll("
        "'[data-reconciler-block],[data-reconciler-new-kind-fields],[data-reconciler-new-subtype-fields]'"
        ").forEach(b=>{setBlockHidden(b, b.hidden);});"
        "})();"
        '</script>'
        '</fieldset>'
    )


_BACK_BREADCRUMB_LABELS: Mapping[str, str] = {
    "/etl/triage": "Triage",
    "/etl/probe": "Probe",
    "/etl/run": "Refresh Data",
    "/etl": "ETL Support",
    "/diagram": "Diagram",
}


def _safe_back_target(from_param: str | None) -> str | None:
    """Validate ``?from=`` for the back-breadcrumb (BTa.2 P1.5).

    Operator's Q6 lock — same-hostname rather than strict /etl/.
    Accept any same-origin path; reject anything that doesn't start
    with ``/`` (open-redirect guard — full URLs / scheme-relative /
    backslash bypasses all rejected).
    """
    if not from_param or not from_param.startswith("/"):
        return None
    if from_param.startswith("//") or from_param.startswith("/\\"):
        return None
    return from_param


def _back_breadcrumb_html(from_param: str | None) -> str:
    """Sticky back-breadcrumb header strip for BTa.2 P1.5.

    Renders nothing when ``?from=`` is absent / unsafe — callers
    safely concat unconditionally.
    """
    target = _safe_back_target(from_param)
    if target is None:
        return ""
    # Label the destination when known; otherwise echo the path so
    # the operator at least sees where they came from.
    label = _BACK_BREADCRUMB_LABELS.get(target.split("?", 1)[0], target)
    return (
        '<div class="bg-accent/5 border-b border-accent/20 px-4 py-2 text-sm" '
        'data-test-back-breadcrumb>'
        f'<a class="text-accent no-underline hover:underline" '
        f'href="{escape(target)}">← Back to {escape(label)}</a>'
        '</div>'
    )


def _form_str(form: Any, key: str) -> str | None:  # typing-smell: ignore[explicit-any]: starlette FormData has heterogeneous .get() values; we only want str scalars
    """Pull a string scalar out of a FormData payload.

    Returns None when the key is absent OR the value isn't a plain
    string (file uploads return UploadFile — those aren't ``?from=``
    targets and should be ignored).
    """
    raw = form.get(key)
    if raw is None:
        return None
    if not isinstance(raw, str):
        return None
    return raw


def _from_hidden_input(from_param: str | None) -> str:
    """Hidden form input that carries ``?from=`` through a POST.

    BTa.2 P1.5 — the save / create handlers read this on success and
    redirect back to ``from`` instead of ``/`` so the loop is one
    click: Triage → Edit → Save → back to Triage.
    """
    target = _safe_back_target(from_param)
    if target is None:
        return ""
    return f'<input type="hidden" name="_back_from" value="{escape(target)}">'


def _render_create_page(
    kind: EntityKind,
    instance: Any,  # typing-smell: ignore[explicit-any]: L2Instance — needed for select_from option resolution
    form_overrides: Mapping[str, str | tuple[str, ...]] | None = None,
    global_error: str | None = None,
    subtype: RailSubtype | None = None,
    from_param: str | None = None,
    top_nav_html: str = "",
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

    ``subtype`` (X.4.f.11.5) gates the field set when ``kind="rail"``:
    the picker page (rendered separately by ``_render_rail_subtype_picker``)
    routes the operator to ``?subtype=two_leg`` or ``?subtype=single_leg``,
    and that subtype is woven through here as a hidden form input the
    POST handler reads to dispatch ``create_l2_entity`` to the right
    constructor. For non-rail kinds, ``subtype`` is ignored.
    """
    specs = _filter_specs_by_subtype(_FIELD_SPECS_BY_KIND[kind], subtype)
    # BF.4-followup (2026-06-07) — `edit_only` fields used to be
    # filtered out of the create page entirely. Both edit_only
    # renderers (`_render_multi_select_groups_field` for
    # leg_rail_xor_groups, `_render_metadata_value_examples_field`
    # for metadata_value_examples) carry an entity=None branch that
    # shows a staged-edit banner explaining "Save the <sibling>
    # first; then come back to author this." Letting them render
    # surfaces the field's existence so the operator knows about
    # the post-create-edit workflow.
    overrides = form_overrides or {}
    fields_html = "".join(
        _render_field(s, overrides.get(s.name, ""), instance)
        for s in specs
    )
    # BB.2 — Reconciler picker fieldset. Renders for single_leg rails
    # (any subtype variant) AND for two_leg rails (CO.3, 2026-06-06).
    # The picker is *required for non-aggregating single_leg* and for
    # *two_leg without expected_net* by the BB.1 handler (server-side
    # `needs_reconciler` gate); aggregating single-leg rails are
    # self-reconciling per SPEC's exemption + the picker is ignored
    # if filled. Pre-CO.3 the picker was omitted for two_leg because
    # "two_leg reconciles via expected_net" — true when expected_net
    # is set, but for two_leg without expected_net (S5 bilateral) the
    # rail still needs forward-reference reconciliation via a TT, so
    # the picker is genuinely required. The browser dogfood gate
    # surfaced the inconsistency.
    reconciler_html = (
        _render_reconciler_section(instance, overrides)
        if kind == "rail" and subtype in ("single_leg", "two_leg")
        else ""
    )
    # Hidden subtype input — POST handler picks it up via _coerce_form's
    # passthrough on form keys not in the FieldSpec list (the create
    # branch in create_l2_entity reads fields["subtype"] directly).
    subtype_html = (
        f'<input type="hidden" name="subtype" value="{escape(subtype)}">'
        if subtype is not None else ""
    )
    global_err_html = (
        f'<div role="alert" class="text-sm text-danger bg-red-50 border '
        f'border-danger rounded-sm px-3 py-2 mb-3">{escape(global_error)}</div>'
        if global_error else ""
    )
    intro_html = _CREATE_INTRO_BY_KIND.get(kind, "")
    # BF.1 — subtype-specific requirements banner (rail only when
    # subtype is set). Sits above the form in the right column.
    subtype_banner_html = (
        _render_subtype_requirements_banner(subtype)
        if kind == "rail" else ""
    )
    # When a Rail subtype is picked, surface it in the page title so the
    # operator can see they're filling in the right form.
    title_suffix = (
        f" ({'two-leg' if subtype == 'two_leg' else 'single-leg'})"
        if subtype is not None else ""
    )
    # `form.create-form` retained as a pure locator hook for
    # `tests/e2e/_drivers/studio_browser_editor.py::_submit_create_form`
    # (selects `form.create-form button[type="submit"]`). Drops when
    # the driver migrates to `form[action^="/l2_shape/"]`.
    primary_btn = primary_button_classes()
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Recon-Gen · Studio · Editor · Create {escape(kind_label_singular(kind))}{escape(title_suffix)}</title>
  {studio_theme_head(instance)}
  {_htmx_head_block()}
</head>
<body class="block min-h-screen font-sans bg-surface-bg text-primary-fg">
  {top_nav_html}
  {_form_page_header_html(f"Create new {kind_label_singular(kind)}{title_suffix}")}
  {_back_breadcrumb_html(from_param)}
  <main class="max-w-4xl mx-auto pt-6 px-4 pb-12 flex flex-col gap-4">
    {_render_intro_details(intro_html)}
    {subtype_banner_html}
    <section class="bg-white border border-surface-border rounded-md p-5">
      <form method="post" action="/l2_shape/{escape(kind)}/" class="create-form group">
        {global_err_html}
        {subtype_html}
        {_from_hidden_input(from_param)}
        {fields_html}
        {reconciler_html}
        <div class="flex items-center gap-3 mt-4">
          <button type="submit" class="{primary_btn}">Create</button>
          <a class="text-accent no-underline text-xs cursor-pointer hover:underline" href="/">Cancel</a>
        </div>
      </form>
    </section>
  </main>
</body>
</html>
"""


def _render_edit_page(
    kind: EntityKind,
    entity: object,
    instance: Any,  # typing-smell: ignore[explicit-any]: L2Instance — needed for select_from option resolution
    form_overrides: Mapping[str, str | tuple[str, ...]] | None = None,
    global_error: str | None = None,
    from_param: str | None = None,
    top_nav_html: str = "",
) -> str:
    """AI.2.e — full HTML page for editing an existing entity: the dedicated
    edit screen, symmetric with ``_render_create_page``. Replaces the X.4.e
    inline hx-swap edit fragment (``_render_edit_form``) so editing reads the
    same as creating — a roomy full-page form where the per-kind / per-subtype
    field requirements (e.g. single-leg vs two-leg rail) are obvious.

    Prefilled from ``entity``; a plain HTML POST to
    ``/l2_shape/<kind>/<id>`` (the ``save`` handler 303-redirects home on
    success, re-renders this page + 400 on validation/coercion error).
    Includes ``edit_only`` fields (e.g. ``leg_rail_xor_groups``) the create
    page must omit — the entity already has the siblings they reference.
    """
    specs = _filter_specs_for_entity(_FIELD_SPECS_BY_KIND[kind], entity)
    entity_id = _entity_id(kind, entity)
    # BX.10 (2026-06-11) — URL side uses the opaque form for composite
    # kinds. The form ACTION + the Delete hx-get reference this so a
    # save / delete posts back to the same opaque URL the operator is
    # already on.
    url_id = _url_entity_id(kind, entity)
    overrides = form_overrides or {}
    hidden = _hidden_fields_for_entity(kind, entity, instance)
    fields_html = "".join(
        _render_field(
            s,
            overrides.get(s.name, getattr(entity, s.name, None)),
            instance,
            entity=entity,
        )
        for s in specs
        if s.name not in hidden
    )
    global_err_html = (
        f'<div role="alert" class="text-sm text-danger bg-red-50 border '
        f'border-danger rounded-sm px-3 py-2 mb-3">{escape(global_error)}</div>'
        if global_error else ""
    )
    intro_html = _CREATE_INTRO_BY_KIND.get(kind, "")
    rail_subtype = _rail_subtype_of(entity)
    # BF.1 — subtype-specific requirements banner on the edit page too.
    # An operator editing an existing rail benefits from the same
    # checklist (especially when they're untangling a stale rail whose
    # reconciler attachment broke).
    subtype_banner_html = (
        _render_subtype_requirements_banner(rail_subtype)
        if kind == "rail" else ""
    )
    title_suffix = (
        f" ({'two-leg' if rail_subtype == 'two_leg' else 'single-leg'})"
        if rail_subtype is not None else ""
    )
    # `form.edit-form` retained as a pure locator hook for
    # browser-driver `form.edit-form button[type="submit"]`. Drops
    # when the driver migrates to `form[action^="/l2_shape/{kind}/{id}"]`.
    primary_btn = primary_button_classes()
    # CG.19 + CG-followup.1+2 (2026-06-05) — `_edit_h1_parts` computes
    # the visible h1 HTML (with display-name span on accounts; parent-
    # only for chains; role-arrow-rail-(direction) for limit_schedule)
    # AND the matching `<title>` detail slot. Pre-followup the h1
    # leaked the full composite key for chain / limit_schedule and
    # mixed em-dash + colon as separators on accounts.
    edit_h1_inner, edit_title_detail = _edit_h1_parts(
        kind, entity, entity_id, title_suffix,
    )
    list_url = f"/l2_shape/{kind}/"
    back_link_html = (
        f'<div class="max-w-4xl mx-auto px-4 pt-3 -mb-1">'
        f'<a class="text-accent no-underline text-xs cursor-pointer hover:underline" '
        f'href="{escape(list_url)}">← back to {escape(kind_label_plural(kind))}</a>'
        f'</div>'
    )
    # BX.1 (2026-06-11) — same banner pattern on the edit page. The
    # banner lands in `#delete-confirm-banner-slot` above the form
    # section; the form stays intact so Cancel returns the operator
    # to where they were.
    delete_btn_classes = destructive_button_classes()
    delete_btn_html = (
        f'<a class="{delete_btn_classes} ml-auto" '
        f'data-role="form-delete" '
        f'hx-get="/l2_shape/{escape(kind)}/{escape(url_id)}/delete-confirm?from=edit" '
        f'hx-target="#delete-confirm-banner-slot" hx-swap="innerHTML">Delete</a>'
    )
    # BX.8 (2026-06-11) — inline mini-diagram showing the entity's
    # position in the topology. Self-node highlighted; 1-hop
    # neighbors visible for spatial context. Empty string for kinds
    # without a clean topology projection (limit_schedule per
    # operator Q3; theme + instance — no per-entity node) — the
    # helper returns "" so the markup just omits the section.
    mini_diagram_html = render_mini_diagram_html(instance, kind, entity)
    # The diagram-svg.css is loaded on /diagram but the edit page
    # doesn't link it; pull it in when the mini-diagram is present so
    # the self-highlight + hover-Edit badge rules fire. Hash-cache-bust
    # via asset_url stays consistent with the main diagram page.
    mini_diagram_css_html = (
        f'<link rel="stylesheet" href="{asset_url("diagram-svg.css")}">'
        if mini_diagram_html else ""
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Recon-Gen · Studio · Editor · {escape(edit_title_detail)}</title>
  {studio_theme_head(instance)}
  {mini_diagram_css_html}
  {_htmx_head_block()}
</head>
<body class="block min-h-screen font-sans bg-surface-bg text-primary-fg">
  {top_nav_html}
  {_form_page_header_raw_html(edit_h1_inner)}
  {back_link_html}
  {_back_breadcrumb_html(from_param)}
  <main class="max-w-4xl mx-auto pt-6 px-4 pb-12 flex flex-col gap-4">
    {_render_intro_details(intro_html)}
    {subtype_banner_html}
    <div id="delete-confirm-banner-slot" data-test-delete-banner-slot></div>
    {mini_diagram_html}
    <section class="bg-white border border-surface-border rounded-md p-5">
      <form method="post" action="/l2_shape/{escape(kind)}/{escape(url_id)}" class="edit-form group">
        {global_err_html}
        {_from_hidden_input(from_param)}
        {fields_html}
        <div class="flex items-center gap-3 mt-4">
          <button type="submit" class="{primary_btn}">Save</button>
          <a class="text-accent no-underline text-xs cursor-pointer hover:underline" href="/">Cancel</a>
          {delete_btn_html}
        </div>
      </form>
    </section>
  </main>
</body>
</html>
"""


# X.4.f.12 — singleton intro prose + helpers.
_SINGLETON_INTRO_BY_KIND: Mapping[EntityKind, tuple[str, str]] = {
    "theme": (
        "Theme",
        "<p><strong>Theme</strong> is the institution's brand palette — "
        "the colors that drive every dashboard, the studio chrome, and "
        "the audit PDF cover. Save with every section blank ⇒ theme "
        "clears and the bundled DEFAULT_PRESET takes over.</p>"
        "<p>The form below decomposes <code>ThemePreset</code> "
        "(<code>common/l2/theme.py</code>) into per-field controls. "
        "Required: <code>theme_name</code> + <code>version_description</code> "
        "+ at least one <code>data_colors</code> entry + <code>empty_fill_color</code> "
        "+ <code>gradient</code> (low/high hex pair). The UI palette "
        "groups by purpose — surfaces+text, brand, state colours, "
        "chart-axis chips — with pair-previews showing the actual bg/fg "
        "combo so contrast issues surface before deploy.</p>"
    ),
    "instance": (
        "Institution settings",
        "<p><strong>Institution settings</strong> are the top-level "
        "L2 fields that describe this institution: "
        "<code>institution_name</code> (display name surfaced in the "
        "audit PDF + Investigation app + handbook substitution), "
        "<code>institution_acronym</code> (2-4 letter abbreviation), "
        "and <code>description</code> (free-form prose the handbook "
        "renders as the intro paragraph).</p>"
        "<p>BXa.1 (2026-05-30) promoted institution_name + "
        "institution_acronym out of the deleted <code>persona</code> "
        "block. Leave any field blank to clear it (silent-fallback: "
        "institution_name regex-extracts from the description when "
        "absent; audit PDF falls back to <code>cfg.deployment_name</code>).</p>"
    ),
}


def _singleton_yaml_text(instance: object, kind: EntityKind) -> str:
    """Dump the singleton attribute as a YAML map for the textarea.

    None / unset ⇒ empty string (operator sees a blank textarea +
    intro prose explaining what an empty block means).
    """
    import dataclasses as dc  # noqa: PLC0415 — lazy
    import yaml  # noqa: PLC0415 — lazy

    if kind == "instance":
        # AI.2.c — top-level scalars dumped as one block. Omit a key
        # when its field is None; all-None ⇒ blank textarea (the
        # "clear" state). Round-trips through singleton_save_l2's
        # instance branch. Phase CP removed role_business_day_offsets
        # from the instance singleton — offsets now per-entity.
        instance_map: dict[str, object] = {}
        desc = getattr(instance, "description", None)
        if desc is not None:
            instance_map["description"] = desc
        if not instance_map:
            return ""
        return yaml.safe_dump(
            instance_map, default_flow_style=False, sort_keys=False,
        ).rstrip() + "\n"

    attr = "theme" if kind == "theme" else "persona"
    value = getattr(instance, attr, None)
    if value is None:
        return ""
    # Walk the dataclass to a plain dict that yaml.safe_dump can handle.
    # The L2 loader's per-kind helper round-trips this cleanly.
    as_dict = dc.asdict(value)  # pyright: ignore[reportUnknownArgumentType,reportUnknownVariableType]  # WHY: ThemePreset/DemoPersona are dataclasses; asdict returns plain dict[str, Any]
    return yaml.safe_dump(as_dict, default_flow_style=False, sort_keys=False).rstrip() + "\n"


# BXa.1 (2026-05-30) — _PERSONA_INSTITUTION_FIELDS + _persona_form_to_dict
# + _persona_dict_from_instance + _render_persona_form deleted with the
# DemoPersona nuke. Institution name + acronym now live on the
# `instance` singleton's structured form (see _render_instance_form
# below). What survives of the BF.7 design — labeled positional
# inputs for institution identity — moves to the instance singleton.


# BXa.1 — instance singleton's structured form fields (replaces the
# raw YAML textarea per BX.0.5b cold-read P1.1). Three top-level
# L2Instance fields edited per-input; description gets markdown
# preview per BF.9. Phase CP removed role_business_day_offsets from
# the singleton — offsets now per-Account / per-AccountTemplate.
#
# BX.13 — every entry carries an optional ``SurfaceAnchor`` pointing
# at the SURFACES_AS entry the chip should open. ``None`` skips the
# chip entirely (acronym has no operator-visible surface beyond
# substitution into the institution_name string, so no chip).
_INSTANCE_STRUCTURED_FIELDS: tuple[
    tuple[str, str, str, bool, SurfaceAnchor | None], ...
] = (
    # (form-name, label, helper, required, surfaces_anchor)
    ("institution_name", "Institution name",
     "Display name surfaced in the audit PDF header + Investigation "
     "app landing prose + handbook substitution. Optional; falls back "
     "to `cfg.deployment_name` when blank.", False,
     SurfaceAnchor("institution-name")),
    ("institution_acronym", "Institution acronym",
     "Short label (2-4 letters) used in dashboard titles + handbook "
     "substitution. Optional; \"SNB\" toggles the bundled-fixture "
     "Sasquatch concrete examples in the docs.", False,
     None),
)


def _instance_dict_from_instance(instance: Any) -> dict[str, str]:  # typing-smell: ignore[explicit-any]: L2Instance dataclass shape — getattr threads through
    """Pre-populate the instance singleton structured form. BXa.1."""
    return {
        "institution_name": str(getattr(instance, "institution_name", None) or ""),
        "institution_acronym": str(getattr(instance, "institution_acronym", None) or ""),
        "description": str(getattr(instance, "description", None) or ""),
    }


def _instance_form_to_dict(form: Mapping[str, str]) -> dict[str, object]:
    """BXa.1 — read the instance singleton structured form into a dict
    `singleton_save_l2`'s yaml.safe_load path consumes.

    Structured fields cover the 3 top-level scalars
    (institution_name + acronym + description). Phase CP removed
    role_business_day_offsets from the singleton — offsets now live
    per-Account / per-AccountTemplate.
    """
    out: dict[str, object] = {}
    for fname, _, _, _, _ in _INSTANCE_STRUCTURED_FIELDS:
        v = str(form.get(fname, "")).strip()
        if v:
            out[fname] = v
    # BXa.1: don't strip description — trailing newlines are
    # semantically meaningful (YAML `description: |` block dumps them
    # back); strip would break round-trip equality on fixtures whose
    # description ends with a newline. Empty-string still treated as
    # "clear" via the falsy check below.
    description = str(form.get("description", ""))
    # CO.3 — normalize CRLF→LF; browsers submit textarea with \r\n.
    description = description.replace("\r\n", "\n").replace("\r", "\n")
    if description.strip():
        out["description"] = description
    return out


def _render_instance_form(values: Mapping[str, str]) -> str:
    """BXa.1 — render the instance singleton structured form body.

    BX.13 — every scalar field with a ``SurfaceAnchor`` in
    ``_INSTANCE_STRUCTURED_FIELDS`` gets an inline ``where?`` chip
    next to its label; the Description field carries its own chip
    pointing at ``institution-description``.
    """
    row_cls = field_row_classes()
    input_cls = field_input_classes()
    label_cls = "font-semibold text-xs text-primary-fg"
    helper_cls = "text-xs text-secondary-fg"
    parts: list[str] = []
    for fname, label, helper, required, surfaces in _INSTANCE_STRUCTURED_FIELDS:
        req = '<span class="text-danger"> *</span>' if required else ""
        v = values.get(fname, "")
        surfaces_chip = _surfaces_as_chip_html(surfaces, field_label=label)
        parts.append(
            f'<div class="{row_cls}">'
            f'<label for="field-{fname}" class="{label_cls}">'
            f'{escape(label)}{req}{surfaces_chip}</label>'
            f'<input type="text" id="field-{fname}" name="{fname}" '
            f'value="{escape(v)}" class="{input_cls}">'
            f'<small class="{helper_cls}">{escape(helper)}</small>'
            f'</div>'
        )
    desc = values.get("description", "")
    desc_chip = _surfaces_as_chip_html(
        SurfaceAnchor("institution-description"),
        field_label="Description",
    )
    parts.append(
        f'<div class="{row_cls}">'
        f'<label for="field-description" class="{label_cls}">'
        f'Description{desc_chip}</label>'
        f'<textarea id="field-description" name="description" rows="10" '
        f'class="{input_cls} font-mono whitespace-pre resize-y min-h-16">'
        f'{escape(desc)}</textarea>'
        f'<small class="{helper_cls}">'
        f'Free-form prose (markdown OK). Handbook templates render this '
        f'as the "what is this institution" intro paragraph; the '
        f'institution_name regex extracts from here when not set above.'
        f'</small>'
        f'</div>'
    )
    return "".join(parts)


# browser supports color inputs).
#
# BX.13 — each tuple's 5th slot is an optional ``SurfaceAnchor`` pointing
# at the SURFACES_AS entry; ``None`` skips the chip for that field.
# Identity scalars + every color get a chip; analysis_name_prefix is
# QS-internal (no operator-visible surface) so no chip.
_THEME_TEXT_FIELDS: tuple[
    tuple[str, str, str, bool, SurfaceAnchor | None], ...
] = (
    # (form-name, label, helper, required, surfaces_anchor)
    ("theme_name", "Theme name",
     "Short identifier (e.g. \"snb-classic\").", True,
     SurfaceAnchor("theme-name")),
    ("version_description", "Version description",
     "One-line summary surfaced on the audit PDF cover.", True,
     SurfaceAnchor("theme-version-description")),
    ("analysis_name_prefix", "Analysis name prefix",
     "Optional prefix applied to QS analysis names (\"Demo\" / null for default).", False,
     None),
)

_THEME_OPTIONAL_URL_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("logo", "Logo URL or path", "Optional. URL (http/https/protocol-relative) or absolute file path; absolute paths copy into the docs build."),
    ("favicon", "Favicon URL or path", "Optional. Same shape as logo."),
)

# Each tuple: (form-name, label, helper, surfaces_anchor). All hex
# colors, rendered as a paired `<input type="color">` + visible text
# field so the operator can type a hex directly OR pick visually.
#
# BX.13 — every color carries an optional ``SurfaceAnchor`` pointing
# at where the value lands in dashboards / audit PDF; ``None`` skips
# the chip (the four surface-bg / surface-fg colours don't have a
# single canonical "this appears here" pointer — they're everywhere —
# so we omit the chip rather than write vague prose).
_THEME_COLOR_FIELDS: tuple[
    tuple[str, str, str, SurfaceAnchor | None], ...
] = (
    ("primary_bg", "Primary background", "Page background — most surfaces.", None),
    ("secondary_bg", "Secondary background", "Off-card / subtle stripe surfaces.", None),
    ("primary_fg", "Primary text", "Body text colour.", None),
    ("secondary_fg", "Secondary text", "Muted text, helpers, axis ticks.", None),
    ("accent", "Accent", "Primary brand colour (titles, links, primary buttons).",
     SurfaceAnchor("theme-accent")),
    ("accent_fg", "Accent foreground", "Text colour ON accent backgrounds (white on accent buttons).",
     SurfaceAnchor("theme-accent-fg")),
    ("link_tint", "Link tint", "Pale-accent cell tint for right-click drill backgrounds.",
     SurfaceAnchor("theme-link-tint")),
    ("danger", "Danger", "Error / negative-delta indicator.",
     SurfaceAnchor("theme-danger")),
    ("danger_fg", "Danger foreground", "Text on danger backgrounds.",
     SurfaceAnchor("theme-danger-fg")),
    ("warning", "Warning", "Warning indicator.",
     SurfaceAnchor("theme-warning")),
    ("warning_fg", "Warning foreground", "Text on warning backgrounds.",
     SurfaceAnchor("theme-warning-fg")),
    ("success", "Success", "Positive-delta indicator.",
     SurfaceAnchor("theme-success")),
    ("success_fg", "Success foreground", "Text on success backgrounds.",
     SurfaceAnchor("theme-success-fg")),
    ("dimension", "Dimension", "Dimension-axis chip background.",
     SurfaceAnchor("theme-dimension")),
    ("dimension_fg", "Dimension foreground", "Text on dimension chips.",
     SurfaceAnchor("theme-dimension-fg")),
    ("measure", "Measure", "Measure-axis chip background.",
     SurfaceAnchor("theme-measure")),
    ("measure_fg", "Measure foreground", "Text on measure chips.",
     SurfaceAnchor("theme-measure-fg")),
)


def _theme_form_to_dict(form: Mapping[str, str]) -> dict[str, object]:
    """BF.8 — read the structured theme form data + build the dict
    `singleton_save_l2` expects.

    Form-name convention mirrors the persona helper:
    - ``<field>`` for scalars (theme_name, version_description, etc.)
    - ``data_colors_<N>`` for the data-color list (with
      ``data_colors__count`` hidden int)
    - ``gradient_low`` / ``gradient_high`` for the [light, dark] pair
    """
    out: dict[str, object] = {}

    for fname, _, _, required, _ in _THEME_TEXT_FIELDS:
        v = str(form.get(fname, "")).strip()
        if v:
            out[fname] = v
        elif required:
            # Required scalar empty ⇒ surface as None so the loader
            # raises its own actionable message rather than the
            # studio guessing at validation messaging.
            pass

    # analysis_name_prefix: explicit "null"/"none" / blank ⇒ omit
    # (None default). Anything else passes through above.
    apn = str(form.get("analysis_name_prefix", "")).strip()
    if apn.lower() in ("null", "none", ""):
        out.pop("analysis_name_prefix", None)

    # Optional URL fields.
    for fname, _, _ in _THEME_OPTIONAL_URL_FIELDS:
        v = str(form.get(fname, "")).strip()
        if v:
            out[fname] = v

    # data_colors list.
    n_str = form.get("data_colors__count", "0")
    try:
        n = int(str(n_str))
    except ValueError:
        n = 0
    data_colors = [
        str(form.get(f"data_colors_{i}", "")).strip() for i in range(n)
    ]
    data_colors = [c for c in data_colors if c]
    if data_colors:
        out["data_colors"] = data_colors

    # empty_fill_color scalar.
    efc = str(form.get("empty_fill_color", "")).strip()
    if efc:
        out["empty_fill_color"] = efc

    # gradient: [low, high] paired pickers.
    low = str(form.get("gradient_low", "")).strip()
    high = str(form.get("gradient_high", "")).strip()
    if low and high:
        out["gradient"] = [low, high]

    # All 17 UI colors.
    for fname, _, _, _ in _THEME_COLOR_FIELDS:
        v = str(form.get(fname, "")).strip()
        if v:
            out[fname] = v

    return out


def _theme_dict_from_instance(instance: Any) -> dict[str, object]:  # typing-smell: ignore[explicit-any]: L2Instance dataclass shape
    """BF.8 — extract current theme dict for pre-populating the form.
    Mirrors `_theme_form_to_dict`'s key shape."""
    import dataclasses as dc  # noqa: PLC0415 — lazy

    theme = getattr(instance, "theme", None)
    if theme is None:
        return {}
    raw = dc.asdict(theme)  # pyright: ignore[reportUnknownArgumentType,reportUnknownVariableType]  # WHY: ThemePreset dataclass; asdict returns plain dict[str, Any]
    # Drop None-valued optional fields so the form renders blank.
    return {k: v for k, v in raw.items() if v is not None}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]  # WHY: dataclasses.asdict returns dict[str, Any]


def _render_color_picker_row(
    name: str, label: str, helper: str, value: str,
    *, pair_with: tuple[str, str] | None = None,
    surfaces_anchor: SurfaceAnchor | None = None,
) -> str:
    """BF.8 — paired `<input type="color">` + hex text input + a
    visible preview chip.

    Bound JS keeps the color picker + hex text in sync (typing
    a hex updates the picker; picking a colour updates the text)
    AND updates the preview chip's background-color so the operator
    sees the colour rendered at size.

    ``pair_with``: optional ``(other_hex, sample_text)``. When set,
    renders the preview chip with the OTHER colour as the
    background + this colour as the text (or vice versa per the
    convention — bg/fg pairing), letting the operator eyeball the
    contrast in context. Without it the preview chip just fills
    with this colour solid.

    ``surfaces_anchor`` (BX.13): optional pointer to a ``SURFACES_AS``
    entry. When set, a ``where?`` chip appears next to the label so
    the operator can see which dashboard / audit / PDF surface(s)
    consume this value before changing it.
    """
    row_cls = field_row_classes()
    label_cls = "font-semibold text-xs text-primary-fg"
    helper_cls = "text-xs text-secondary-fg"
    input_cls = field_input_classes()
    # Default to a neutral grey when unset so the color picker
    # doesn't open at #000.
    hex_value = value if value else "#cccccc"
    # Preview chip — bigger swatch (24x10) rendered with the
    # color as background. For bg/fg pairs, the chip shows the
    # actual usage (text in one colour on the other's bg).
    if pair_with is not None:
        other_hex, sample_text = pair_with
        other_safe = other_hex if other_hex else "#ffffff"
        # The chip uses the pair's bg as its background + this
        # colour as its text — operator eyeballs the actual contrast.
        preview = (
            f'<div id="field-{name}-preview" '
            f'class="flex items-center justify-center px-3 py-2 rounded-sm '
            f'text-sm font-semibold border border-surface-border w-32" '
            f'style="background:{escape(other_safe)};color:{escape(hex_value)};">'
            f'{escape(sample_text)}</div>'
        )
    else:
        preview = (
            f'<div id="field-{name}-preview" '
            f'class="rounded-sm border border-surface-border w-32 h-9" '
            f'style="background:{escape(hex_value)};"></div>'
        )
    surfaces_chip = _surfaces_as_chip_html(
        surfaces_anchor, field_label=label,
    )
    return (
        f'<div class="{row_cls}">'
        f'<label for="field-{name}-hex" class="{label_cls}">'
        f'{escape(label)}{surfaces_chip}</label>'
        f'<div class="flex items-center gap-3">'
        f'<input type="color" id="field-{name}-color" '
        f'value="{escape(hex_value)}" '
        f'class="w-10 h-9 p-0 border border-surface-border rounded-sm cursor-pointer" '
        f'oninput="var v=this.value;document.getElementById(&quot;field-{name}-hex&quot;).value=v;'
        f'var p=document.getElementById(&quot;field-{name}-preview&quot;);'
        f'if(p){{ if(p.style.background){{p.style.background=v;}}'
        f'if(p.style.color){{p.style.color=v;}} }}">'
        f'<input type="text" id="field-{name}-hex" name="{name}" '
        f'value="{escape(value)}" '
        f'placeholder="#aabbcc" '
        f'class="{input_cls} font-mono tabular-nums w-32" '
        f'oninput="if (/^#[0-9a-fA-F]{{6}}$/.test(this.value)){{'
        f'document.getElementById(&quot;field-{name}-color&quot;).value=this.value;'
        f'var p=document.getElementById(&quot;field-{name}-preview&quot;);'
        f'if(p){{ if(p.style.background){{p.style.background=this.value;}}'
        f'if(p.style.color){{p.style.color=this.value;}} }} }}">'
        f'{preview}'
        f'</div>'
        f'<small class="{helper_cls}">{escape(helper)}</small>'
        f'</div>'
    )


def _render_theme_form(
    theme_dict: Mapping[str, object],
    *,
    extra_data_color_slots: int = 1,
) -> str:
    """BF.8 — render the structured theme form body."""
    row_cls = field_row_classes()
    input_cls = field_input_classes()
    label_cls = "font-semibold text-xs text-primary-fg"
    helper_cls = "text-xs text-secondary-fg"
    section_cls = (
        "border border-surface-border rounded-md p-4 mb-4 "
        "bg-surface-bg flex flex-col gap-3"
    )
    section_label_cls = (
        "font-semibold text-sm text-primary-fg flex items-center gap-2"
    )

    parts: list[str] = []

    # Identity section.
    parts.append(
        f'<fieldset class="{section_cls}">'
        f'<legend class="{section_label_cls}">Identity</legend>'
    )
    for fname, label, helper, required, surfaces in _THEME_TEXT_FIELDS:
        req = '<span class="text-danger"> *</span>' if required else ""
        v = str(theme_dict.get(fname, "") or "")
        surfaces_chip = _surfaces_as_chip_html(surfaces, field_label=label)
        parts.append(
            f'<div class="{row_cls}">'
            f'<label for="field-{fname}" class="{label_cls}">'
            f'{escape(label)}{req}{surfaces_chip}</label>'
            f'<input type="text" id="field-{fname}" name="{fname}" '
            f'value="{escape(v)}" class="{input_cls}">'
            f'<small class="{helper_cls}">{escape(helper)}</small>'
            f'</div>'
        )
    parts.append('</fieldset>')

    # Data colour palette.
    data_colors = theme_dict.get("data_colors", [])
    # WHY: theme_dict is form-derived; element type is Any.
    dc_items: list[object] = (
        list(data_colors) if isinstance(data_colors, list)  # pyright: ignore[reportUnknownArgumentType]: theme_dict element type is Any
        else []
    )
    dc_total = len(dc_items) + extra_data_color_slots
    parts.append(
        f'<fieldset class="{section_cls}">'
        f'<legend class="{section_label_cls}">Data colour palette</legend>'
        f'<p class="{helper_cls} m-0">Hex colours cycled per series in QS charts. At least one required. Blank slots dropped on save.</p>'
        f'<input type="hidden" name="data_colors__count" value="{dc_total}">'
    )
    for i in range(dc_total):
        v = str(dc_items[i]) if i < len(dc_items) else ""
        # BX.13 — only the first series carries the section-level
        # surfaces-as chip; the rest of the rows skip it to avoid the
        # chip stack visually competing with the per-series colour
        # pickers. Operator clicking any row sees the same content.
        anchor = SurfaceAnchor("theme-data-colors") if i == 0 else None
        parts.append(
            _render_color_picker_row(
                f"data_colors_{i}",
                f"Series {i + 1}",
                "Hex (e.g. #1f4e79).",
                v,
                surfaces_anchor=anchor,
            )
        )
    parts.append('</fieldset>')

    # Empty fill + gradient.
    efc = str(theme_dict.get("empty_fill_color", "") or "")
    gradient = theme_dict.get("gradient", [])
    # WHY: theme_dict is form-derived; element type is Any.
    grad_items: list[object] = (
        list(gradient) if isinstance(gradient, list)  # pyright: ignore[reportUnknownArgumentType]: theme_dict element type is Any
        else []
    )
    low = str(grad_items[0]) if len(grad_items) > 0 else ""
    high = str(grad_items[1]) if len(grad_items) > 1 else ""
    parts.append(
        f'<fieldset class="{section_cls}">'
        f'<legend class="{section_label_cls}">Empty + gradient</legend>'
        + _render_color_picker_row(
            "empty_fill_color", "Empty fill",
            "Colour used when a chart cell has no data.", efc,
            surfaces_anchor=SurfaceAnchor("theme-empty-fill-color"),
        )
        + _render_color_picker_row(
            "gradient_low", "Gradient (low)",
            "Light end of the heatmap gradient.", low,
            surfaces_anchor=SurfaceAnchor("theme-gradient"),
        )
        + _render_color_picker_row(
            "gradient_high", "Gradient (high)",
            "Dark end of the heatmap gradient.", high,
            surfaces_anchor=SurfaceAnchor("theme-gradient"),
        )
        + '</fieldset>'
    )

    # UI palette — 17 colors grouped by purpose so the operator
    # understands what each role drives. Pair-renders bg/fg colors
    # side-by-side with a preview showing actual usage (sample
    # text in the fg colour on the bg colour) so contrast issues
    # surface visually before deploy.
    def _color_value(fname: str) -> str:
        return str(theme_dict.get(fname, "") or "")

    def _color_label(fname: str) -> str:
        for (n, lbl, _, _) in _THEME_COLOR_FIELDS:
            if n == fname:
                return lbl
        return fname

    def _color_helper(fname: str) -> str:
        for (n, _, h, _) in _THEME_COLOR_FIELDS:
            if n == fname:
                return h
        return ""

    def _color_surfaces_anchor(fname: str) -> SurfaceAnchor | None:
        for (n, _, _, anchor) in _THEME_COLOR_FIELDS:
            if n == fname:
                return anchor
        return None

    def _render_solo(fname: str) -> str:
        return _render_color_picker_row(
            fname, _color_label(fname), _color_helper(fname),
            _color_value(fname),
            surfaces_anchor=_color_surfaces_anchor(fname),
        )

    def _render_pair(bg_name: str, fg_name: str, sample: str) -> str:
        """Render the bg + fg colors with each preview showing the
        ACTUAL pair (fg text on bg surface) so contrast is visible
        at a glance."""
        return (
            _render_color_picker_row(
                bg_name, _color_label(bg_name), _color_helper(bg_name),
                _color_value(bg_name),
                pair_with=(_color_value(fg_name), sample),
                surfaces_anchor=_color_surfaces_anchor(bg_name),
            )
            + _render_color_picker_row(
                fg_name, _color_label(fg_name), _color_helper(fg_name),
                _color_value(fg_name),
                pair_with=(_color_value(bg_name), sample),
                surfaces_anchor=_color_surfaces_anchor(fg_name),
            )
        )

    parts.append(
        f'<fieldset class="{section_cls}">'
        f'<legend class="{section_label_cls}">UI palette — surfaces + text</legend>'
        f'<p class="{helper_cls} m-0">Page backgrounds + body text. Every panel / card across QS dashboards, the studio chrome, and the audit PDF reads from these. <strong>Pair-previews</strong> show the actual surface/text combo.</p>'
        + _render_solo("primary_bg")
        + _render_solo("secondary_bg")
        + _render_solo("primary_fg")
        + _render_solo("secondary_fg")
        + '</fieldset>'
    )

    parts.append(
        f'<fieldset class="{section_cls}">'
        f'<legend class="{section_label_cls}">UI palette — brand</legend>'
        f'<p class="{helper_cls} m-0">Accent is the primary brand colour — titles, links, primary buttons, focus rings. <code>accent_fg</code> is the text colour ON accent backgrounds (white-on-accent buttons). <code>link_tint</code> is the pale-accent wash used as the background for right-click-drill table cells.</p>'
        + _render_pair("accent", "accent_fg", "Sample button")
        + _render_solo("link_tint")
        + '</fieldset>'
    )

    parts.append(
        f'<fieldset class="{section_cls}">'
        f'<legend class="{section_label_cls}">UI palette — state colours</legend>'
        f'<p class="{helper_cls} m-0">Three state pairs — danger (errors, breach indicators), warning (heads-up signals), success (positive-delta / closed-clean indicators). Each pair pre-previews the bg + fg combo: aim for AA contrast minimum.</p>'
        + _render_pair("danger", "danger_fg", "Error")
        + _render_pair("warning", "warning_fg", "Warning")
        + _render_pair("success", "success_fg", "Closed clean")
        + '</fieldset>'
    )

    parts.append(
        f'<fieldset class="{section_cls}">'
        f'<legend class="{section_label_cls}">UI palette — chart axis chips</legend>'
        f'<p class="{helper_cls} m-0">QS visuals carry dimension / measure chips in the field-wells. <code>dimension</code> + <code>measure</code> are the chip backgrounds; <code>*_fg</code> the chip text.</p>'
        + _render_pair("dimension", "dimension_fg", "by Account")
        + _render_pair("measure", "measure_fg", "SUM(amount)")
        + '</fieldset>'
    )

    # Optional brand assets.
    parts.append(
        f'<fieldset class="{section_cls}">'
        f'<legend class="{section_label_cls}">Brand assets (optional)</legend>'
    )
    for fname, label, helper in _THEME_OPTIONAL_URL_FIELDS:
        v = str(theme_dict.get(fname, "") or "")
        parts.append(
            f'<div class="{row_cls}">'
            f'<label for="field-{fname}" class="{label_cls}">{escape(label)}</label>'
            f'<input type="text" id="field-{fname}" name="{fname}" '
            f'value="{escape(v)}" placeholder="https://… or /abs/path.png" '
            f'class="{input_cls}">'
            f'<small class="{helper_cls}">{escape(helper)}</small>'
            f'</div>'
        )
    parts.append('</fieldset>')

    return "".join(parts)


def _render_singleton_page(
    kind: EntityKind,
    instance: Any,  # typing-smell: ignore[explicit-any]: L2Instance — read attribute + theme head
    yaml_text: str | None = None,
    global_error: str | None = None,
    structured_overrides: Mapping[str, object] | None = None,
    top_nav_html: str = "",
) -> str:
    """X.4.f.12 — singleton edit page (Theme / Persona).

    Single textarea carrying the entire YAML subtree. The operator's
    mental model is "this is the YAML block in the L2 file" — match
    that exactly. v1 has no per-field color pickers / nested editors;
    polish lands as a follow-on if the cosmetic-edit frequency turns
    out high enough to warrant it.
    """
    label, intro_html = _SINGLETON_INTRO_BY_KIND[kind]
    global_err_html = (
        f'<div role="alert" class="text-sm text-danger bg-red-50 border '
        f'border-danger rounded-sm px-3 py-2 mb-3">{escape(global_error)}</div>'
        if global_error else ""
    )
    primary_btn = primary_button_classes()
    input_cls = field_input_classes()
    row_cls = field_row_classes()
    # BXa.1 — persona singleton removed. theme + instance singletons
    # render as structured forms (BF.8 + BXa.1 respectively). Phase CP
    # removed role_business_day_offsets from the instance singleton —
    # offsets now per-Account / per-AccountTemplate.
    if kind == "theme":
        theme_dict = (
            dict(structured_overrides) if structured_overrides is not None
            else _theme_dict_from_instance(instance)
        )
        form_body = _render_theme_form(theme_dict)
    elif kind == "instance":
        instance_dict = (
            {str(k): str(v) for k, v in structured_overrides.items()}
            if structured_overrides is not None
            else _instance_dict_from_instance(instance)
        )
        form_body = _render_instance_form(instance_dict)
    else:
        current_yaml = yaml_text if yaml_text is not None else _singleton_yaml_text(instance, kind)
        form_body = (
            f'<div class="{row_cls}">'
            f'<label for="field-yaml" class="font-semibold text-xs text-primary-fg">YAML</label>'
            f'<textarea id="field-yaml" name="yaml" rows="22" '
            f'class="{input_cls} font-mono whitespace-pre resize-y min-h-16" '
            f'spellcheck="false">{escape(current_yaml)}</textarea>'
            f'<small class="text-xs text-secondary-fg">'
            f'Empty block ⇒ clears the {escape(kind_label_singular(kind))} (silent-fallback). '
            f'Bad YAML or missing required fields ⇒ form re-renders with '
            f'your typed content + the validator error inline.'
            f'</small>'
            f'</div>'
        )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Recon-Gen · Studio · Editor · {escape(label)}</title>
  {studio_theme_head(instance)}
  {_htmx_head_block()}
</head>
<body class="block min-h-screen font-sans bg-surface-bg text-primary-fg">
  {top_nav_html}
  {_form_page_header_html(label)}
  <main class="max-w-4xl mx-auto pt-6 px-4 pb-12 flex flex-col gap-4">
    {_render_intro_details(intro_html)}
    <section class="bg-white border border-surface-border rounded-md p-5">
      <form method="post" action="/l2_shape/{escape(kind)}/" class="create-form group">
        <input type="hidden" name="_method" value="PUT">
        {global_err_html}
        {form_body}
        <div class="flex items-center gap-3 mt-4">
          <button type="submit" class="{primary_btn}">Save</button>
          <a class="text-accent no-underline text-xs cursor-pointer hover:underline" href="/">Cancel</a>
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
    search_html: str = "",
    pager_html: str = "",
    top_nav_html: str = "",
    total_count: int | None = None,
    q: str = "",
    embed: bool = False,
    base_url: str = "",
) -> str:
    """Full HTML page — every entity of the kind rendered as a read card.

    Layout (top to bottom, matching the dashboard table-pager
    convention — operator lock 2026-06-05):
      - ``search_html`` (CF.4.b) — standalone-page search form
        (empty in the home-page embed flow because the section
        summary upstream already owns the input).
      - cards grid — the current page's read cards.
      - ``pager_html`` — range indicator + Prev/Next, sitting BELOW
        the cards (mirrors `bootstrap.js`'s `table-pager` strip).

    The caller pre-slices ``entities`` so this function only renders
    the current page.

    ``embed=True`` returns just the cards container (no html/head/body)
    so the X.4.f.7 home page can ``hx-get`` it into a section without
    nesting full documents. The home page's own <head> already loads
    htmx + the editor CSS + the htmx:beforeSwap fix, so the embed
    fragment doesn't need to redeclare them.
    """
    # CG.5 (2026-06-05): chevron + lazy-load uniformly across ALL
    # kinds. CF.4.c's count-threshold (>10 = collapse) was a graceful
    # rollout — but the cold-read v3 flagged that an operator
    # browsing a 7-rail L2 and a 100-rail L2 saw "two different
    # products" depending on which sections happened to be heavy.
    # Operator lock (2026-06-05): trade an extra click on small
    # kinds for uniform UX. `COLLAPSE_THRESHOLD` kept as a dead
    # constant for historical traceability; the next consumer can
    # delete it once nothing references it.
    # Legacy callers (post-save card refresh, the read_card route)
    # still pass `total_count=None` and get the eager render via
    # `_render_read_card`'s default — they're rendering ONE card,
    # not a list, so the chevron pattern doesn't apply.
    collapsed = total_count is not None
    cards = "\n".join(
        _render_read_card(
            kind, e, instance, collapsed=collapsed,
        )
        for e in entities
    )
    # CG.8 (2026-06-05) — empty-state in-place message. When the
    # cards grid is empty AND there's an active search, render a
    # centered prompt inside the grid area instead of letting the
    # operator stare at a blank page with only "No matches" tucked
    # next to the pager. Two shapes:
    #   - search-empty: "No <plural> match `<q>`."  + clear-search btn
    #   - kind-empty:    "No <plural> in this L2 yet." (no clear btn)
    # Both center inside the grid wrapper so the message lands where
    # the operator's eyes already are (cards are missing).
    if total_count == 0 and not entities:
        plural = kind_label_plural(kind, lowercase=True)
        if q:
            # Clear-search URL: strip `q=…` by going to the base list URL.
            # base_url is `/l2_shape/<kind>/` standalone or
            # `/l2_shape/<kind>/?embed=1` from the home page embed.
            # Two wire shapes:
            # - Standalone: plain anchor. Browser navigates to the
            #   unfiltered standalone page. NO htmx — hx-get'ing the
            #   standalone URL returns a full HTML document and
            #   swapping it into #entity-list would render the
            #   whole page (top-nav + header + cards) INSIDE the
            #   cards grid (caught on dogfood 2026-06-05).
            # - Embed: htmx-fetch the embed URL into
            #   #home-section-body-<kind> — same target the pager
            #   uses (CG.6 fix for the CF.4.j P0). Section body
            #   refreshes in place; the rest of the home page stays.
            clear_url = base_url or f"/l2_shape/{kind}/"
            if embed:
                clear_btn = (
                    f'<a class="{chrome_button_classes()} mt-3" '
                    f'href="{escape(clear_url)}" '
                    f'hx-get="{escape(clear_url)}" '
                    f'hx-target="#home-section-body-{kind}" '
                    f'hx-swap="innerHTML">Clear search</a>'
                )
            else:
                clear_btn = (
                    f'<a class="{chrome_button_classes()} mt-3" '
                    f'href="{escape(clear_url)}">Clear search</a>'
                )
            empty_state_html = (
                '<div class="col-span-full flex flex-col items-center '
                'justify-center py-12 text-center" data-role="empty-state">'
                '<p class="text-base m-0 text-secondary-fg">'
                f"No {escape(plural)} match "
                f'<code class="bg-link-tint px-1 rounded-sm">{escape(q)}</code>.'
                '</p>'
                '<p class="text-sm m-0 mt-1 text-secondary-fg">'
                'Clear search or check spelling.</p>'
                f"{clear_btn}"
                '</div>'
            )
        else:
            empty_state_html = (
                '<div class="col-span-full flex flex-col items-center '
                'justify-center py-12 text-center" data-role="empty-state">'
                '<p class="text-base m-0 text-secondary-fg">'
                f"No {escape(plural)} in this L2 yet.</p>"
                '</div>'
            )
        cards = empty_state_html
    # AM.1 step 6 — list-page chrome migrated. `entity-list` /
    # `studio-header` / `nav-link` semantic classes drop in favor
    # of raw utilities. `id="entity-list"` kept as the hx-target
    # hook the routes' delete + save fragments swap into.
    grid_cls = (
        "grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4 "
        "max-w-7xl mx-auto px-4 pt-4 pb-2"
    )
    search_wrap_open = (
        '<div class="max-w-7xl mx-auto px-4 pt-4">' if search_html else ""
    )
    search_wrap_close = "</div>" if search_html else ""
    pager_wrap_open = (
        '<div class="max-w-7xl mx-auto px-4 pb-12">' if pager_html else ""
    )
    pager_wrap_close = "</div>" if pager_html else ""
    # BX.1 (2026-06-11) — page-level slot for the delete-confirm
    # banner. ONE per page (single in-flight delete at a time);
    # card Delete buttons swap their banner in here via
    # `hx-target="#delete-confirm-banner-slot"`. In `embed` mode the
    # home page's wrapper already declares a slot of its own —
    # the per-section embed body skips its own slot to avoid id
    # duplication (handled in `_render_home`).
    if embed:
        return (
            f"{search_wrap_open}{search_html}{search_wrap_close}"
            f'<div class="{grid_cls}" data-kind="{escape(kind)}">{cards}</div>'
            f"{pager_wrap_open}{pager_html}{pager_wrap_close}"
        )
    # CG.6 (2026-06-05) — dedicated `/l2_shape/<kind>/` pages wear
    # the same trainer-style header strip as `/`, `/training/`, and
    # `/etl/`. h1 = operator-readable plural label; blurb = one-line
    # per-kind anchor pulled from `_LIST_PAGE_BLURB_BY_KIND`.
    #
    # BX.6/11 (2026-06-11) — Account + AccountTemplate list pages
    # rebrand to lead with **Roles** (the user-facing organizing
    # principle the editor reframe lifted account-vs-template into).
    # URLs unchanged per the locked OQ4(a) decision; only the h1 +
    # blurb tags speak the role vocabulary now. The cross-link to
    # the sibling cardinality page appears below the h1 so the
    # operator can pivot 1:1 ↔ 1:N without bouncing through the home.
    page_title_str, page_h1_extra_html = _list_page_h1_for_kind(kind)
    page_title = escape(page_title_str)
    page_blurb = _LIST_PAGE_BLURB_BY_KIND.get(kind, "")
    page_header_html = (
        f'<header class="px-8 py-4 border-b border-surface-border bg-white">'
        f'<h1 class="text-xl font-semibold m-0" '
        f'data-list-h1="{escape(kind)}">{page_title}</h1>'
        f'{page_h1_extra_html}'
        f'<p class="text-sm text-secondary-fg max-w-3xl m-0 mt-1">'
        f"{page_blurb}"
        f"</p>"
        f"</header>"
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Recon-Gen · Studio · Editor · {escape(kind_label_plural(kind))}</title>
  {studio_theme_head(instance)}
  {_htmx_head_block()}
</head>
<body class="block min-h-screen font-sans bg-surface-bg text-primary-fg">
  {top_nav_html}
  {page_header_html}
  <div class="max-w-7xl mx-auto px-4 pt-3">
    <div id="delete-confirm-banner-slot" data-test-delete-banner-slot></div>
  </div>
  {search_wrap_open}{search_html}{search_wrap_close}
  <main id="entity-list" class="{grid_cls}">
    {cards}
  </main>
  {pager_wrap_open}{pager_html}{pager_wrap_close}
</body>
</html>
"""


def _entity_id(kind: EntityKind, entity: object) -> str:
    """Read the addressing key off an entity — symmetric with editor.py's
    ``_find_entity`` lookup.

    BX.10 (2026-06-11): this stays returning the *internal composite key*
    (e.g. ``"CustomerInboundACH::Foo,Bar"`` for chain) because the
    addressing key is the L2-shape lookup contract — search-by-name in
    `_filter_entities`, HTML id slugs in `_render_read_card`, validation
    paths in `mutate_l2`, etc. all key off the composite form. Only the
    *URL surface* moves to opaque ``hash6-slug`` (see
    ``_url_entity_id`` + ``_resolve_url_entity_id``).
    """
    if kind == "account":
        return str(getattr(entity, "id"))
    if kind == "account_template":
        return str(getattr(entity, "role"))
    if kind in ("rail", "transfer_template"):
        return str(getattr(entity, "name"))
    if kind == "chain":
        # Z.A: composite key = "parent::sorted-children-csv" — same
        # shape editor.py's _find_entity uses to address Chain rows.
        # AB.6 (per-child): children entries are now ChainChildSpec; the
        # composite key still keys on the child names (sorted).
        children = getattr(entity, "children")
        children_csv = ",".join(sorted(str(c.name) for c in children))
        return f"{getattr(entity, 'parent')}::{children_csv}"
    # limit_schedule — 3-part composite (AB.1): parent_role::rail::direction.
    # Same (parent_role, rail) may carry both an Outbound and an Inbound
    # cap; the direction segment distinguishes which row each URL addresses.
    return (
        f"{getattr(entity, 'parent_role')}::"
        f"{getattr(entity, 'rail')}::"
        f"{getattr(entity, 'direction')}"
    )


# ---------------------------------------------------------------------------
# BX.10 (2026-06-11) — composite-key opaque URL IDs
# ---------------------------------------------------------------------------
#
# Operator lock (BX.0.7 + BX.10 Direction E): chain + limit_schedule
# URLs go opaque (``/l2_shape/chain/<hash6>-<slug>``) so renames don't
# rot bookmarks. YAML untouched; the composite-key form remains the
# in-memory addressing key (see ``_entity_id`` above).
#
# Single-key kinds (account / account_template / rail / transfer_template)
# keep their existing kebab-slug URLs verbatim — the cold-read complaint
# was specifically about composite-keyed kinds (chain / limit_schedule)
# where the ``::,`` chars rot in Slack pastes. Future cells can extend
# `_url_entity_id` to opaque single-key kinds for consistency (open
# question #2 in `bx_10.md`); the helper structure makes that a
# one-branch addition without rewiring callsites.

# Kinds that get the opaque URL treatment. Closed tuple so pyright catches
# the "new composite kind" case at the call site.
_OPAQUE_URL_KINDS: tuple[EntityKind, ...] = ("chain", "limit_schedule")


def _slug_source_for(kind: EntityKind, entity: object) -> str:
    """BX.10 — pick the human-readable slug source per kind.

    Chain → parent (the operator-readable rail name, e.g.
    ``"CustomerInboundACH"`` → slug ``"customerinboundach"``).
    LimitSchedule → parent_role (the role name, e.g. ``"DDAControl"``
    → ``"ddacontrol"``). Empty result is acceptable — the URL falls
    back to hash6-only (still valid per ``parse_opaque_url_id``).
    """
    if kind == "chain":
        return str(getattr(entity, "parent", "") or "")
    if kind == "limit_schedule":
        return str(getattr(entity, "parent_role", "") or "")
    return ""


def _url_entity_id(kind: EntityKind, entity: object) -> str:
    """BX.10 — URL-side addressing for an entity.

    Composite-keyed kinds (chain / limit_schedule) return
    ``<hash6>-<slug>``; single-key kinds return the same as
    ``_entity_id`` (kebab id / role / name verbatim).

    The hash is derived from the *composite key string* (which is what
    ``_entity_id`` returns), so a rename that changes the composite
    key changes the URL — but that's the intended behavior: renamed
    entity is a different addressing tuple. Stale-URL handling is the
    301-redirect at the route layer (the slug drifted but the hash
    still matches an existing entity).
    """
    if kind in _OPAQUE_URL_KINDS:
        from recon_gen.common.html._components import (  # noqa: PLC0415
            build_opaque_url_id,
        )
        composite = _entity_id(kind, entity)
        return build_opaque_url_id(composite, _slug_source_for(kind, entity))
    return _entity_id(kind, entity)


def _resolve_url_entity_id(
    instance: Any,  # typing-smell: ignore[explicit-any]: L2Instance — read-only iteration
    kind: EntityKind,
    url_seg: str,
) -> tuple[object | None, str | None]:
    """BX.10 — resolve a URL path segment to ``(entity, canonical_url)``.

    Return shape:
    - ``(entity, None)`` — URL is canonical, no redirect needed.
    - ``(entity, "<new-url>")`` — entity found via hash match but the
      slug portion is stale; caller should 301-redirect to the new URL.
    - ``(None, None)`` — no entity matches; caller should 404.

    For composite-keyed kinds, parses the URL as ``<hash6>-<slug>`` and
    matches by walking entities + computing each one's hash.
    For single-key kinds, falls through to the existing
    ``_find_entity_or_none`` shape — URL is the addressing key verbatim.
    """
    if kind not in _OPAQUE_URL_KINDS:
        entity = _find_entity_or_none(instance, kind, url_seg)
        return (entity, None)

    from recon_gen.common.html._components import (  # noqa: PLC0415
        parse_opaque_url_id,
    )
    parsed = parse_opaque_url_id(url_seg)
    if parsed is None:
        # BX.10 — URL doesn't match the opaque ``<hash6>-<slug>`` shape.
        # Fall through to the legacy composite-form lookup so flows
        # that still carry composite ids (banner DELETE URLs built
        # server-side, programmatic PUT clients, the e2e suites) keep
        # working through the cell. The 301-redirect target carries
        # the canonical opaque form so a manual GET against the
        # composite URL self-heals on next visit.
        entity = _find_entity_or_none(instance, kind, url_seg)
        if entity is None:
            return (None, None)
        canonical = _url_entity_id(kind, entity)
        # Composite-form URL → canonical opaque URL is always a redirect
        # (never an exact match, by construction).
        return (entity, canonical)
    target_hash, _url_slug = parsed
    for e in _entities_for_kind(instance, kind):
        canonical = _url_entity_id(kind, e)
        # canonical is always "<hash6>" or "<hash6>-<slug>". Compare
        # hash bits via the parsed form so the comparison is symmetric.
        canonical_parsed = parse_opaque_url_id(canonical)
        if canonical_parsed is None:
            continue  # defensive — _url_entity_id always returns the shape
        c_hash, _c_slug = canonical_parsed
        if c_hash == target_hash:
            if canonical == url_seg:
                return (e, None)  # exact match
            # Hash matched but slug drifted — caller redirects.
            return (e, canonical)
    return (None, None)


def _read_card_url_for(kind: EntityKind, entity_id: str) -> str:
    """BX.2 (2026-06-11) — URL of the entity's read card.

    Save-success redirect target for the create / save handlers.
    ``entity_id`` is the post-save addressing key (see
    ``_post_save_entity_id`` for the rename-aware derivation).

    BX.10 (2026-06-11) — for composite-keyed kinds the URL goes opaque
    (``<hash6>-<slug>``) so the save-redirect lands on the canonical
    opaque URL; single-key kinds keep their kebab-slug shape unchanged.
    Percent-encoding leaves ``::`` (composite separator) alone for
    legacy callers that still pass composite-form ids — those go
    through the 301-redirect path on next GET if BX.10's resolver
    finds them.
    """
    from urllib.parse import quote  # noqa: PLC0415
    return f"/l2_shape/{kind}/{quote(entity_id, safe='::-')}"


def _read_card_url_from_entity(kind: EntityKind, entity: object) -> str:
    """BX.10 — same as ``_read_card_url_for`` but takes the entity
    directly so composite-keyed kinds get the canonical opaque URL
    without the caller having to wire ``_url_entity_id`` themselves.

    Save-success / create-success redirects use this so the operator
    lands on the opaque URL post-mutation (matches what
    ``_render_read_card_summary``'s Edit link points at).
    """
    return _read_card_url_for(kind, _url_entity_id(kind, entity))


def _post_save_entity_id(
    kind: EntityKind,
    original_entity_id: str,
    new_fields: Mapping[str, Any],  # typing-smell: ignore[explicit-any]: form-coerced field values; heterogeneous per dataclass
) -> str:
    """BX.2 — derive the post-save addressing key.

    Combines the original entity_id (the URL-path one) with any
    addressing-key field overrides in ``new_fields`` so the
    save-success redirect lands on the right read card even after a
    rename (Rail.name, TransferTemplate.name, AccountTemplate.role,
    Account.id, Chain.parent / Chain.children, LimitSchedule.parent_role /
    LimitSchedule.rail / LimitSchedule.direction).

    Falls back to ``original_entity_id`` when no addressing-key field
    is in ``new_fields`` (the common "edit a non-addressing field"
    case — name preserved, just a description change).
    """
    if kind == "account":
        raw = new_fields.get("id") if "id" in new_fields else None
        return str(raw) if raw else original_entity_id
    if kind == "account_template":
        raw = new_fields.get("role") if "role" in new_fields else None
        return str(raw) if raw else original_entity_id
    if kind in ("rail", "transfer_template"):
        raw = new_fields.get("name") if "name" in new_fields else None
        return str(raw) if raw else original_entity_id
    if kind == "chain":
        # Composite "parent::sorted-children-csv". Either component may
        # have changed; rebuild from new_fields where present, else
        # parse the original composite for the unchanged component.
        if "parent" in new_fields or "children" in new_fields:
            orig_parent, _, orig_children_csv = original_entity_id.partition("::")
            if "parent" in new_fields:
                parent_raw = new_fields["parent"]
                parent_str = str(parent_raw) if parent_raw else orig_parent
            else:
                parent_str = orig_parent
            if "children" in new_fields:
                raw_children = new_fields["children"]
                # ChainChildSpec tuple — read .name off each.
                child_names = sorted(
                    str(getattr(c, "name", c)) for c in raw_children or ()
                )
                children_csv = ",".join(child_names)
            else:
                children_csv = orig_children_csv
            return f"{parent_str}::{children_csv}"
        return original_entity_id
    # limit_schedule — 3-part composite "parent_role::rail::direction".
    if any(k in new_fields for k in ("parent_role", "rail", "direction")):
        parts = original_entity_id.split("::")
        # Backward-compat 2-part form means direction="Outbound".
        if len(parts) == 2:
            parts = [*parts, "Outbound"]
        orig_pr, orig_rail, orig_dir = (parts + ["", "", ""])[:3]
        pr_raw = new_fields.get("parent_role") if "parent_role" in new_fields else None
        rail_raw = new_fields.get("rail") if "rail" in new_fields else None
        dir_raw = new_fields.get("direction") if "direction" in new_fields else None
        pr_str = str(pr_raw) if pr_raw else orig_pr
        rail_str = str(rail_raw) if rail_raw else orig_rail
        dir_str = str(dir_raw) if dir_raw else orig_dir
        return f"{pr_str}::{rail_str}::{dir_str}"
    return original_entity_id


# ---------------------------------------------------------------------------
# CF.4.b — filter + sort for the editor list view
# ---------------------------------------------------------------------------


def _filter_entities(
    entities: tuple[object, ...], kind: EntityKind, q: str,
) -> tuple[object, ...]:
    """Case-insensitive substring match against the entity_id. CF.4.b
    ships id-only search; richer "search by role name, by leg rail,
    by description" can land as a CF.4.h refinement once the cold-read
    flags what operators actually search by."""
    if not q:
        return entities
    needle = q.lower()
    return tuple(
        e for e in entities
        if needle in _entity_id(kind, e).lower()
    )


def _sort_entities(
    entities: tuple[object, ...],
    kind: EntityKind,
    sort_axis: SortAxis,
) -> tuple[object, ...]:
    """Apply ``sort_axis`` to ``entities``. Tiebreak on entity_id
    always so pagination is stable across pages (risk #7) — the order
    tuples put the operator-chosen axis first, entity_id last. Per-axis
    branches are inlined so pyright can read each key as a uniformly-
    typed tuple (a unified ``Callable[[object], object]`` indirection
    breaks sorted()'s SupportsRichComparison bound).
    """
    if sort_axis == "default":
        return entities

    def eid(e: object) -> str:
        return _entity_id(kind, e)

    if sort_axis == "name_asc":
        return tuple(sorted(entities, key=eid))
    if sort_axis == "name_desc":
        return tuple(sorted(entities, key=eid, reverse=True))
    if sort_axis == "rail_subtype":
        # Two-leg first (TwoLegRail before SingleLegRail), then by name.
        def rail_key(e: object) -> tuple[int, str]:
            return (0 if isinstance(e, TwoLegRail) else 1, eid(e))
        return tuple(sorted(entities, key=rail_key))
    if sort_axis == "template_leg_count":
        def template_key(e: object) -> tuple[int, str]:
            leg_rails = getattr(e, "leg_rails", ())
            return (len(leg_rails), eid(e))
        return tuple(sorted(entities, key=template_key))
    if sort_axis == "chain_parent":
        def chain_key(e: object) -> tuple[str, str]:
            return (str(getattr(e, "parent", "")), eid(e))
        return tuple(sorted(entities, key=chain_key))
    # Unreachable — sort_axis is a closed Literal; pyright catches
    # every miss at the call site. Keep an explicit fallthrough so
    # the function's type is well-defined.
    return entities


def _safe_sort_axis(kind: EntityKind, raw: str | None) -> SortAxis:
    """Clamp an unknown / out-of-universe sort axis to ``default``.
    Symmetric with ``parse_toolbar_state``'s clamping — used here so
    the filter+sort path doesn't double-validate inside
    `ListToolbarState`."""
    from recon_gen.common.html._components import (  # noqa: PLC0415
        SORT_AXES_BY_KIND,
    )
    if raw is None or raw not in SORT_AXES_BY_KIND[kind]:
        return "default"
    return raw  # type: ignore[return-value]: raw membership-checked against SORT_AXES_BY_KIND[kind] above; pyright loses the narrowing across the str-to-Literal return


def _html_id_slug(entity_id: str) -> str:
    """Sanitize an entity_id for use in an HTML ``id`` attribute.

    Composite-keyed kinds (chain / limit_schedule) use ``::`` as the
    separator in their addressing string, and chain's composite also
    inlines a comma-separated children list (e.g.
    ``MerchantSettlementCycle::PayoutACH,PayoutCheck,PayoutWire``).

    Two characters need substitution:
    - ``::`` → ``__`` — CSS treats the double-colon as pseudo-element
      syntax (``#foo::bar`` matches no id).
    - ``,`` → ``_C_`` — CSS treats the comma as the selector-list
      separator (``#a,b`` parses as "id #a OR descendant b", NOT one
      id literally containing a comma). Without this swap, HTMX's
      ``hx-target="#entity-chain-Foo,Bar,Baz"`` lands on the wrong DOM
      node — chain Delete on multi-child chains would either swap a
      sibling out or silently no-op. CG.16 (cold-read v4 P0 #3).

    Both substitutions are reversible: ``_C_`` is conspicuous enough
    to round-trip back to ``,`` if a caller ever needs the original
    composite, and ``__`` is the slug already in use since CF.4.
    URL-side addressing (``/l2_shape/chain/Foo::Bar,Baz``) is
    untouched — Starlette routes happily accept both characters in
    the path segment; only the HTML ``id`` needs scrubbing.
    """
    return entity_id.replace("::", "__").replace(",", "_C_")


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


def _make_handlers(
    cache: L2InstanceCache,
    *,
    top_nav_fn: Callable[[str], str] | None = None,
) -> dict[str, Any]:  # typing-smell: ignore[explicit-any]: per-handler ASGI callables; uniform shape but per-route closure
    """Build closures over the cache for each route handler.

    Returned as a dict keyed by route name so ``make_editor_routes``
    can register them all in one pass.

    ``top_nav_fn`` (CF.4 followup 2026-06-05) is the shared Studio
    top-nav renderer threaded from ``make_studio_routes``. When None
    (unit-test surface), editor pages render without a top nav.
    """
    def top_nav_html_fn(active_href: str) -> str:
        return top_nav_fn(active_href) if top_nav_fn is not None else ""
    # CG.7 (2026-06-05) — wrappers that auto-inject the shared
    # top-nav into the structured-form pages. Same chrome unification
    # as CF.4.l did for the list page.
    def _render_create_page_local(*args: Any, **kwargs: Any) -> str:
        return _render_create_page(*args, top_nav_html=top_nav_html_fn("/"), **kwargs)
    def _render_edit_page_local(*args: Any, **kwargs: Any) -> str:
        return _render_edit_page(*args, top_nav_html=top_nav_html_fn("/"), **kwargs)
    def _render_singleton_page_local(*args: Any, **kwargs: Any) -> str:
        return _render_singleton_page(*args, top_nav_html=top_nav_html_fn("/"), **kwargs)
    def _render_rail_subtype_picker_local(*args: Any, **kwargs: Any) -> str:
        return _render_rail_subtype_picker(*args, top_nav_html=top_nav_html_fn("/"), **kwargs)

    async def list_view(request: Request) -> HTMLResponse:
        kind = _kind_from_path(request.path_params["kind"])
        if kind is None:
            return HTMLResponse(
                _render_unknown_kind_page(
                    request.path_params["kind"],
                    top_nav_html_fn("/"),
                    cache.get(),
                ),
                status_code=404,
            )
        # X.4.f.12 — singletons (theme, persona) skip the list view
        # entirely; GET /l2_shape/<singleton-kind>/ renders the
        # singleton edit page directly.
        if kind in SINGLETON_KINDS:
            return HTMLResponse(_render_singleton_page_local(kind, cache.get()))
        if kind not in _FIELD_SPECS_BY_KIND:
            return HTMLResponse(
                _render_unknown_kind_page(
                    request.path_params["kind"],
                    top_nav_html_fn("/"),
                    cache.get(),
                ),
                status_code=404,
            )
        inst = cache.get()
        entities = _entities_for_kind(inst, kind)
        # X.4.f.7 — ?embed=1 returns the cards fragment only (no html/head/
        # body) so the home page can hx-get it into a <details> section
        # without nesting full documents.
        embed = request.query_params.get("embed") == "1"
        # CF.4.b — parse the search/sort/page query params, filter +
        # sort the entities tuple, slice to the current page, build
        # the toolbar HTML, and pass everything to the renderer. The
        # primitive (`_components.py`) handles clamping bad input so
        # the route doesn't need defensive code.
        filtered = _filter_entities(
            entities, kind,
            (request.query_params.get("q") or "").strip(),
        )
        sorted_filtered = _sort_entities(
            filtered, kind,
            _safe_sort_axis(
                kind, request.query_params.get("sort_column"),
            ),
        )
        state = parse_toolbar_state(
            request.query_params,
            kind=kind,
            total_count=len(sorted_filtered),
        )
        page_entities = sorted_filtered[
            state.page_offset:state.page_offset + state.page_size
        ]
        submit_url = (
            f"/l2_shape/{kind}/?embed=1" if embed
            else f"/l2_shape/{kind}/"
        )
        # CF.4 followup (2026-06-05): the pager sits BELOW the cards
        # to match the dashboard table-pager convention (`mt-3` strip
        # in `bootstrap.js`). The search input lives ABOVE the cards
        # on the standalone page (no upstream <details> summary); the
        # home-page embed has its search input in the section summary
        # so the embed renders no body-level search.
        # CF.4.j cold-read P0 #2 (2026-06-05): when embed=True the
        # pager's hx-target must point at the home page's section-
        # body wrapper (`#home-section-body-<kind>`), NOT
        # `#entity-list` which only exists on the standalone page.
        # The Next/Prev click silently fell back to no-op without
        # this fix.
        pager_target_id = (
            f"home-section-body-{kind}" if embed else "entity-list"
        )
        pager_html = render_list_pager(
            state,
            submit_url=submit_url,
            swap_target_id=pager_target_id,
            embed=embed,
        )
        search_html = "" if embed else render_list_search(
            state,
            submit_url=submit_url,
            swap_target_id="entity-list",
            embed=embed,
        )
        # CF.4 followup (2026-06-05): editor sub-pages reuse the
        # Studio top-nav (the same chrome the Trainer wears) instead
        # of the bespoke "Studio · editor · <kind>" inline header.
        # `_top_nav_html("/")` makes the L2 Editor entry the active
        # one (entry_href == "/" requires an exact match per
        # `emit_top_nav._is_active`; passing `/` from sub-pages is
        # the convention).
        return HTMLResponse(
            _render_list_page(
                kind, page_entities, inst,
                search_html=search_html,
                pager_html=pager_html,
                top_nav_html=top_nav_html_fn("/") if not embed else "",
                total_count=state.total_count,
                q=state.q,
                base_url=submit_url,
                embed=embed,
            ),
        )

    async def read_card(request: Request) -> Response:
        kind = _kind_from_path(request.path_params["kind"])
        url_seg = request.path_params["entity_id"]
        if kind is None or kind not in _FIELD_SPECS_BY_KIND:
            return HTMLResponse("not editable", status_code=404)
        inst = cache.get()
        # BX.10 (2026-06-11) — opaque URL resolution for composite-keyed
        # kinds. Returns (entity, canonical_url) where canonical_url is
        # not None when the slug portion drifted from the entity's
        # current name (rename → stale bookmark → 301 self-heal).
        entity, canonical_url = _resolve_url_entity_id(inst, kind, url_seg)
        if entity is None:
            return HTMLResponse("not found", status_code=404)
        if canonical_url is not None:
            # Preserve ``?body_only=1`` etc. across the redirect so a
            # stale-slug HTMX lazy-load still hits the body-fragment
            # endpoint after the bounce.
            qs = request.url.query
            target = f"/l2_shape/{kind}/{canonical_url}"
            if qs:
                target = f"{target}?{qs}"
            return RedirectResponse(target, status_code=301)
        # CF.4.c — ``?body_only=1`` returns just the `<dl>` rows so
        # collapse-by-default cards can lazy-fetch the heavy body on
        # first expand. Same endpoint, same auth path, no new route
        # to drift against the full-card one.
        if request.query_params.get("body_only") == "1":
            return HTMLResponse(_render_read_card_body(kind, entity, inst))
        return HTMLResponse(
            _render_read_card(kind, entity, inst),
        )

    async def edit_form(request: Request) -> Response:
        kind = _kind_from_path(request.path_params["kind"])
        url_seg = request.path_params["entity_id"]
        if kind is None or kind not in _FIELD_SPECS_BY_KIND:
            return HTMLResponse("not editable", status_code=404)
        inst = cache.get()
        entity, canonical_url = _resolve_url_entity_id(inst, kind, url_seg)
        if entity is None:
            return HTMLResponse("not found", status_code=404)
        if canonical_url is not None:
            # BX.10 — stale-slug 301 to canonical edit URL.
            qs = request.url.query
            target = f"/l2_shape/{kind}/{canonical_url}/edit"
            if qs:
                target = f"{target}?{qs}"
            return RedirectResponse(target, status_code=301)
        from_param = request.query_params.get("from")
        return HTMLResponse(_render_edit_page_local(kind, entity, inst, from_param=from_param))

    async def save(request: Request) -> Response:
        """AI.2.e — dedicated-screen save: coerce → mutate → validate →
        save → 303-redirect home (symmetric with the create POST).

        Validation / coercion failure → 400 + the full-page edit screen
        re-rendered with the error banner + the operator's typed values
        preserved. Bound to both POST and PUT /l2_shape/<kind>/<id> so the
        plain HTML edit form (POST) and any programmatic PUT both work.

        BX.10 (2026-06-11) — for composite-keyed kinds the path segment
        is opaque (``<hash6>-<slug>``); resolved to the composite key
        ONCE up front, then the composite key feeds every mutate /
        rename / find call below. Save-success redirect re-derives the
        opaque URL from the post-mutate entity (rename → new hash).
        """
        kind = _kind_from_path(request.path_params["kind"])
        url_seg = request.path_params["entity_id"]
        if kind is None or kind not in _FIELD_SPECS_BY_KIND:
            return HTMLResponse("not editable", status_code=404)

        # BX.10 — resolve opaque URL → composite entity_id for the
        # mutate / find calls below. The path may be opaque (chain /
        # limit_schedule) or single-key (account / rail / etc.).
        inst_for_resolve = cache.get()
        resolved_entity, _canonical = _resolve_url_entity_id(
            inst_for_resolve, kind, url_seg,
        )
        if resolved_entity is None:
            return HTMLResponse("not found", status_code=404)
        entity_id = _entity_id(kind, resolved_entity)

        form = await request.form()
        # BTa.2 P1.5 — `_back_from` hidden input round-trips the
        # original ``?from=`` so save-then-redirect lands on the
        # triage / probe / run page the operator came from.
        from_param = _form_str(form, "_back_from")
        try:
            new_fields, coerced_overrides = _coerce_form(kind, form)
        except (ValueError, TypeError) as exc:
            inst = cache.get()
            entity = _find_entity_or_none(inst, kind, entity_id)
            if entity is None:
                # CR.11 — entity deleted mid-flight. Clean 410 instead
                # of the pre-CR.11 NotImplementedError for non-account
                # kinds (the placeholder fallback only implemented
                # ``"account"``).
                return _entity_gone_response(kind, entity_id)
            # Best-effort overrides — coerce_form raised before producing
            # them; capture the raw scalar fields from .items() so the
            # operator's typed values aren't lost.
            best_effort = {str(k): str(v) for k, v in form.items()}
            return HTMLResponse(
                _render_edit_page_local(
                    kind, entity, inst,
                    form_overrides=best_effort,
                    global_error=f"Field coercion failed: {exc}",
                    from_param=from_param,
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
        # / Chain.parent / Chain.children for a rail/template name rename).
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
                from recon_gen.common.l2.primitives import (  # noqa: PLC0415
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
            if entity is None:
                # CR.11 — entity deleted mid-flight; same shape as the
                # form-coercion branch above.
                return _entity_gone_response(kind, entity_id)
            return HTMLResponse(
                _render_edit_page_local(
                    kind, entity, inst,
                    form_overrides=coerced_overrides,
                    global_error=str(exc),
                    from_param=from_param,
                ),
                status_code=400,
            )

        cache.save(new_inst)
        # BX.2 (2026-06-11) — save-success 303-redirects to the entity's
        # read card by default (operator stays in the editing flow,
        # sees their just-saved fields rendered, can keep iterating).
        # The pre-BX.2 default was `/` (the home page), which forced an
        # extra "scroll to the section + find the row" step on every
        # save. ``?from=`` (carried via the `_back_from` hidden input)
        # still wins when set, so Triage → Edit → Save → Triage stays
        # one click (BTa.2 P1.5).
        # BX.10 (2026-06-11) — derive the opaque URL from the post-save
        # entity so a rename (composite key changed → hash changed)
        # lands on the new canonical URL. Look up the entity by its
        # post-save composite key, then render the URL via
        # ``_read_card_url_from_entity`` to get the opaque form for
        # composite kinds.
        new_entity_id = _post_save_entity_id(kind, entity_id, new_fields)
        new_entity = _find_entity_or_none(new_inst, kind, new_entity_id)
        if new_entity is not None:
            default_target = _read_card_url_from_entity(kind, new_entity)
        else:
            # Fall back to the pre-BX.10 composite-form redirect if the
            # post-save entity can't be re-found (shouldn't happen, but
            # don't 500 the response).
            default_target = _read_card_url_for(kind, new_entity_id)
        redirect_target = _safe_back_target(from_param) or default_target
        return RedirectResponse(redirect_target, status_code=303)

    async def new_form(request: Request) -> HTMLResponse:
        kind = _kind_from_path(request.path_params["kind"])
        if kind is None or kind not in _FIELD_SPECS_BY_KIND:
            return HTMLResponse("not editable", status_code=404)
        from_param = request.query_params.get("from")
        # BTb.2 — prefill the `name` field when Triage CTA carries
        # `?prefill_name=<observed_value>`. Eliminates the retype-the-
        # phantom-rail-name friction the cold-read v3 flagged.
        prefill_name = request.query_params.get("prefill_name") or ""
        form_overrides: Mapping[str, str | tuple[str, ...]] = (
            {"name": prefill_name} if prefill_name else {}
        )
        # X.4.f.11.5 — Rail is a discriminated union; the create flow
        # is 2-step. Step 1 (no ?subtype=) is the picker page; step 2
        # (?subtype=two_leg|single_leg) renders the create form
        # filtered to that subtype's fields. Other kinds skip both
        # branches and render the form directly.
        if kind == "rail":
            raw_subtype = request.query_params.get("subtype")
            subtype: RailSubtype | None
            if raw_subtype == "two_leg":
                subtype = "two_leg"
            elif raw_subtype == "single_leg":
                subtype = "single_leg"
            elif raw_subtype is None:
                return HTMLResponse(
                    _render_rail_subtype_picker_local(
                        cache.get(),
                        from_param=from_param,
                        prefill_name=prefill_name,
                    ),
                )
            else:
                return HTMLResponse(
                    f"unknown rail subtype: {escape(raw_subtype)}",
                    status_code=400,
                )
            return HTMLResponse(
                _render_create_page_local(
                    kind, cache.get(), subtype=subtype,
                    from_param=from_param,
                    form_overrides=form_overrides or None,
                ),
            )
        return HTMLResponse(
            _render_create_page_local(
                kind, cache.get(),
                from_param=from_param,
                form_overrides=form_overrides or None,
            ),
        )

    async def create(request: Request) -> Response:
        """X.4.f.9.create — POST a new entity into the kind's collection.

        Coerce → construct (catches required-field errors) → validate
        → save → 303-redirect back to home. Failure re-renders the
        create page with the error inline + the operator's typed
        values preserved.

        X.4.f.11.5: Rail-only — reads the hidden ``subtype`` form key
        the picker injected and threads it through both
        ``create_l2_entity`` (so the constructor dispatches to TwoLeg
        vs SingleLeg) AND the error re-render path (so the
        validation-failure page stays on the right filtered form
        instead of bouncing back to the picker).
        """
        kind = _kind_from_path(request.path_params["kind"])
        if kind is None:
            return HTMLResponse("not editable", status_code=404)

        form = await request.form()
        # BTa.2 P1.5 — `_back_from` round-trips ``?from=`` through the
        # POST so save-then-redirect lands on the triage / probe / run
        # page the operator came from (vs always bouncing to /).
        from_param = _form_str(form, "_back_from")

        # X.4.f.12 — singleton POST (Theme / Persona / Instance).
        # The form's hidden ``_method=PUT`` confirms intent (browser
        # form-method is POST; the route table can't distinguish a
        # singleton-save from a list-create POST otherwise).
        #
        # BF.7 (persona) + BF.8 (theme): structured forms POST per-
        # field controls. Server collects them into the dict shape
        # the YAML loader expects, dumps to YAML, then reuses the
        # existing `singleton_save_l2` path so validation stays the
        # single source of truth. instance singleton stays as the
        # raw `yaml` field (two-scalar block doesn't warrant a
        # structured form).
        if kind in SINGLETON_KINDS:
            import yaml as _yaml  # noqa: PLC0415
            structured_dict: dict[str, object] | None = None
            # BXa.1 — persona dispatch removed (kind no longer in
            # SINGLETON_KINDS). theme + instance both render as
            # structured forms. Phase CP removed
            # role_business_day_offsets from the instance form.
            if kind == "theme":
                structured_dict = _theme_form_to_dict(
                    {k: str(v) for k, v in form.multi_items()},
                )
                yaml_text = _yaml.safe_dump(
                    structured_dict, default_flow_style=False, sort_keys=False,
                ) if structured_dict else ""
            elif kind == "instance":
                structured_dict = _instance_form_to_dict(
                    {k: str(v) for k, v in form.multi_items()},
                )
                yaml_text = _yaml.safe_dump(
                    structured_dict, default_flow_style=False, sort_keys=False,
                ) if structured_dict else ""
            else:
                yaml_text = str(form.get("yaml", ""))
            try:
                new_inst = singleton_save_l2(cache.get(), kind, yaml_text)
            except ValueError as exc:
                return HTMLResponse(
                    _render_singleton_page_local(
                        kind, cache.get(),
                        yaml_text=yaml_text,
                        global_error=str(exc),
                        structured_overrides=structured_dict,
                    ),
                    status_code=400,
                )
            try:
                validate(new_inst)
            except L2ValidationError as exc:
                return HTMLResponse(
                    _render_singleton_page_local(
                        kind, cache.get(),
                        yaml_text=yaml_text,
                        global_error=str(exc),
                        structured_overrides=structured_dict,
                    ),
                    status_code=400,
                )
            cache.save(new_inst)
            # BX.2 (2026-06-11) — singleton save 303-redirects back to
            # the singleton's own page (`/l2_shape/<kind>/`) so the
            # operator sees the just-saved values render through the
            # full singleton page. ``?from=`` still wins for triage /
            # probe / run round-trips.
            singleton_target = f"/l2_shape/{kind}/"
            redirect_target = _safe_back_target(from_param) or singleton_target
            return RedirectResponse(redirect_target, status_code=303)

        if kind not in _FIELD_SPECS_BY_KIND:
            return HTMLResponse("not editable", status_code=404)

        # Pull the hidden subtype field for rails. It's not in any
        # FieldSpec — the create handler reads it directly from form
        # and threads it through create_l2_entity + the re-render path.
        rail_subtype: RailSubtype | None = None
        if kind == "rail":
            raw = form.get("subtype")
            raw_str = str(raw) if raw is not None else ""
            if raw_str == "two_leg":
                rail_subtype = "two_leg"
            elif raw_str == "single_leg":
                rail_subtype = "single_leg"
            else:
                # Missing subtype on a rail POST means the operator
                # bypassed the picker (or a bug stripped the hidden
                # field). Bounce to the picker — the operator can
                # restart cleanly.
                return RedirectResponse(
                    "/l2_shape/rail/new", status_code=303,
                )

        try:
            new_fields, coerced_overrides = _coerce_form(kind, form)
        except (ValueError, TypeError) as exc:
            best_effort = {str(k): str(v) for k, v in form.items()}
            return HTMLResponse(
                _render_create_page_local(
                    kind, cache.get(),
                    form_overrides=best_effort,
                    global_error=f"Field coercion failed: {exc}",
                    subtype=rail_subtype,
                    from_param=from_param,
                ),
                status_code=400,
            )

        # Thread subtype into the typed fields dict so create_l2_entity
        # can dispatch on it. Treat as object for the heterogeneous
        # fields-mapping value type.
        if rail_subtype is not None:
            new_fields["subtype"] = rail_subtype

        # BB.1 / BB.3 — when creating a non-aggregating single-leg
        # rail (S3 / C3 bilateral) or a two-leg rail without
        # expected_net (S5 bilateral), the operator MUST pair the
        # create with a reconciler. Form-pairing the picker keeps
        # the validator strict (no invalid in-flight states) while
        # making the create step a single atomic composite mutation.
        # The picker payload arrives as ``reconciler_kind`` +
        # ``reconciler_name`` form fields.
        non_agg_single_leg = (
            kind == "rail"
            and rail_subtype == "single_leg"
            and not bool(new_fields.get("aggregating") or False)
        )
        two_leg_without_expected_net = (
            kind == "rail"
            and rail_subtype == "two_leg"
            and new_fields.get("expected_net") is None
        )
        needs_reconciler = (
            non_agg_single_leg or two_leg_without_expected_net
        )
        # BB.2 — resolve reconciler_mode UP-FRONT (needs to gate the
        # missing-required validation below + drive both the attach
        # and create-new server paths). Default "attach" preserves
        # the BB.1 contract.
        reconciler_mode = (
            str(form.get("reconciler_mode") or "attach").strip()
            or "attach"
        )
        # BB.2 — capture the reconciler picker values into
        # ``coerced_overrides`` regardless of whether needs_reconciler
        # fires, so a re-render on validation failure preserves the
        # operator's typed picker state. The reconciler fields aren't
        # FieldSpecs so they don't come through _coerce_form. The
        # create-new sub-form (BB.2 mode=create_new) carries its own
        # `reconciler_new_*` prefix-set; mirror those to overrides
        # too so the operator doesn't retype on a re-render.
        coerced_overrides["reconciler_mode"] = reconciler_mode
        raw_rk_form = form.get("reconciler_kind")
        # When mode=create_new the operator's kind choice lives on
        # `reconciler_new_kind` (the inline JS mirrors it to
        # `reconciler_kind`, but a no-JS submit needs the fallback).
        raw_rk_new_form = form.get("reconciler_new_kind")
        if raw_rk_new_form and not raw_rk_form:
            raw_rk_form = raw_rk_new_form
        raw_rn_form = form.get("reconciler_name")
        if raw_rk_form is not None:
            coerced_overrides["reconciler_kind"] = str(raw_rk_form)
        if raw_rn_form is not None:
            coerced_overrides["reconciler_name"] = str(raw_rn_form)
        # Mirror every reconciler_new_* into overrides so a re-render
        # round-trip preserves the inline sub-form state.
        for fk, fv in form.multi_items():
            if str(fk).startswith("reconciler_new_"):
                coerced_overrides[str(fk)] = str(fv)
        reconciler_kind: str | None = None
        reconciler_name: str | None = None
        if needs_reconciler:
            raw_rk = raw_rk_form
            raw_rn = form.get("reconciler_name")
            raw_rn_new = form.get("reconciler_new_name")
            reconciler_kind = str(raw_rk).strip() if raw_rk else ""
            reconciler_name = str(raw_rn).strip() if raw_rn else ""
            reconciler_new_name = (
                str(raw_rn_new).strip() if raw_rn_new else ""
            )
            # Required-input gate splits on mode: attach needs both
            # kind + existing name; create_new needs kind + new-name.
            if reconciler_mode == "create_new":
                missing_required = (
                    not reconciler_kind or not reconciler_new_name
                )
            else:
                missing_required = (
                    not reconciler_kind or not reconciler_name
                )
            if missing_required:
                return HTMLResponse(
                    _render_create_page_local(
                        kind, cache.get(),
                        form_overrides=coerced_overrides,
                        global_error=(
                            "Reconciler required for non-aggregating "
                            "single-leg rails: pick an existing "
                            "TransferTemplate (closes the TT's "
                            "expected_net) or aggregating Rail (gets "
                            "swept into a bundle), or create one "
                            "inline. Per SPEC §S3, an unreconciled "
                            "single-leg rail's drift would persist "
                            "forever."
                        ),
                        subtype=rail_subtype,
                        from_param=from_param,
                    ),
                    status_code=400,
                )
            if reconciler_kind not in ("transfer_template", "aggregating_rail"):
                return HTMLResponse(
                    _render_create_page_local(
                        kind, cache.get(),
                        form_overrides=coerced_overrides,
                        global_error=(
                            f"reconciler_kind={reconciler_kind!r} not "
                            f"recognized (expected 'transfer_template' "
                            f"or 'aggregating_rail')"
                        ),
                        subtype=rail_subtype,
                        from_param=from_param,
                    ),
                    status_code=400,
                )

        try:
            new_inst = create_l2_entity(cache.get(), kind, new_fields)
        except ValueError as exc:
            return HTMLResponse(
                _render_create_page_local(
                    kind, cache.get(),
                    form_overrides=coerced_overrides,
                    global_error=str(exc),
                    subtype=rail_subtype,
                    from_param=from_param,
                ),
                status_code=400,
            )

        # BB.1 / BB.3 — apply the reconciler mutation to the in-memory
        # new_inst BEFORE validate(). The composite (add rail + edit/
        # create reconciler) is the unit of atomicity: either both
        # land or neither. Two paths:
        #   reconciler_mode = "attach" (BB.1) — append to existing.
        #   reconciler_mode = "create_new" (BB.2/BB.3) — create a new
        #     reconciler with the new rail in its rail-list field.
        # Driver computes the right path; operator UI offers both
        # modes via the BB.2 radio (create-new sub-form lives inside
        # _render_reconciler_section).
        if needs_reconciler:
            if reconciler_mode == "create_new":
                try:
                    new_inst = _create_new_reconciler_with_rail(
                        new_inst,
                        new_rail_name=str(new_fields["name"]),
                        reconciler_kind=str(reconciler_kind),
                        form=form,
                    )
                except (KeyError, ValueError, TypeError) as exc:
                    return HTMLResponse(
                        _render_create_page_local(
                            kind, cache.get(),
                            form_overrides=coerced_overrides,
                            global_error=(
                                f"Reconciler create-new failed: {exc}"
                            ),
                            subtype=rail_subtype,
                        ),
                        status_code=400,
                    )
            else:
                try:
                    new_inst = attach_rail_to_reconciler(
                        new_inst,
                        new_rail_name=str(new_fields["name"]),
                        reconciler_kind=str(reconciler_kind),
                        reconciler_name=str(reconciler_name),
                    )
                except (KeyError, ValueError) as exc:
                    return HTMLResponse(
                        _render_create_page_local(
                            kind, cache.get(),
                            form_overrides=coerced_overrides,
                            global_error=(
                                f"Reconciler attach failed: {exc}"
                            ),
                            subtype=rail_subtype,
                        ),
                        status_code=400,
                    )
                # BB.3 — if the reconciler is a TT and the form
                # carries an xor_groups update (multiple Variable-
                # direction rails getting attached need to be in a
                # leg_rail_xor_groups partition before C1 fires), apply
                # it in the same composite. Driver computes the
                # partial groups (filtered to rails currently in the
                # cached TT) at each attach step.
                if (
                    reconciler_kind == "transfer_template"
                    and "leg_rail_xor_groups__present" in form
                ):
                    try:
                        new_inst = _apply_xor_groups_update_to_tt(
                            new_inst,
                            tt_name=str(reconciler_name),
                            form=form,
                        )
                    except (KeyError, ValueError) as exc:
                        return HTMLResponse(
                            _render_create_page_local(
                                kind, cache.get(),
                                form_overrides=coerced_overrides,
                                global_error=(
                                    f"Reconciler XOR groups update "
                                    f"failed: {exc}"
                                ),
                                subtype=rail_subtype,
                            ),
                            status_code=400,
                        )

        try:
            validate(new_inst)
        except L2ValidationError as exc:
            return HTMLResponse(
                _render_create_page_local(
                    kind, cache.get(),
                    form_overrides=coerced_overrides,
                    global_error=str(exc),
                    subtype=rail_subtype,
                    from_param=from_param,
                ),
                status_code=400,
            )

        cache.save(new_inst)
        # BX.2 (2026-06-11) — create-success 303-redirects to the new
        # entity's read card by default (the operator sees what they
        # just made, can fix typos / fill optional fields without
        # re-navigating from home). ``?from=`` still wins so the
        # Triage → New → Save → Triage one-click loop is preserved
        # (BTa.2 P1.5).
        new_entity_id = _post_save_entity_id(kind, "", new_fields)
        # BX.10 (2026-06-11) — redirect to the opaque URL form for
        # composite-keyed kinds so the operator lands on the canonical
        # post-BX.10 URL (matching what the Edit link on the read card
        # already points at). Look up the newly-created entity to
        # derive its hash + slug.
        if new_entity_id:
            new_entity = _find_entity_or_none(new_inst, kind, new_entity_id)
            default_target = (
                _read_card_url_from_entity(kind, new_entity)
                if new_entity is not None
                else _read_card_url_for(kind, new_entity_id)
            )
        else:
            default_target = "/"
        redirect_target = _safe_back_target(from_param) or default_target
        return RedirectResponse(redirect_target, status_code=303)

    async def delete_handler(request: Request) -> Response:
        kind = _kind_from_path(request.path_params["kind"])
        url_seg = request.path_params["entity_id"]
        if kind is None or kind not in _FIELD_SPECS_BY_KIND:
            return HTMLResponse("not editable", status_code=404)

        # BX.10 (2026-06-11) — resolve opaque URL → composite entity_id
        # so HMAC verify + mutate / delete keys off the composite key
        # (the banner-side HMAC was issued against the composite via
        # `delete_confirm_handler`). The resolver accepts both opaque
        # and composite-form URL segs.
        inst_for_resolve = cache.get()
        resolved_entity, _canonical = _resolve_url_entity_id(
            inst_for_resolve, kind, url_seg,
        )
        if resolved_entity is None:
            return HTMLResponse("not found", status_code=404)
        entity_id = _entity_id(kind, resolved_entity)

        # BX.1 (2026-06-11) — DELETE requires the server-signed confirm
        # token issued by GET /l2_shape/<kind>/<id>/delete-confirm.
        # The token encodes start_ts (HMAC-signed); the verifier
        # enforces ``now - start_ts >= COUNTDOWN_SECS`` server-side so
        # a client that disables the disabled-attribute via DevTools
        # still hits the wall clock check. No backward-compat path
        # for unsigned DELETEs: any pre-BX.1 client tab that fires
        # raw DELETE gets a 400 with the actionable error.
        confirm_token = request.query_params.get("confirm_token", "")
        if not confirm_token:
            return HTMLResponse(
                '<div role="alert" data-test-delete-error="missing-token" '
                'class="text-sm text-danger bg-red-50 border '
                'border-danger rounded-sm px-3 py-2 mb-3">'
                "Confirm required — click Delete on the entity to "
                "open the inline confirm banner; wait the countdown; "
                "then click Confirm.</div>",
                status_code=400,
            )
        verify = verify_confirm_token(
            confirm_token, kind, entity_id,
            countdown_secs=_delete_confirm.COUNTDOWN_SECS,
        )
        if verify.status == "invalid":
            return HTMLResponse(
                '<div role="alert" data-test-delete-error="invalid-token" '
                'class="text-sm text-danger bg-red-50 border '
                'border-danger rounded-sm px-3 py-2 mb-3">'
                "Confirm token invalid — reload the page and try "
                "again.</div>",
                status_code=400,
            )
        if verify.status == "too_early":
            return HTMLResponse(
                f'<div role="alert" data-test-delete-error="too-early" '
                f'class="text-sm text-danger bg-red-50 border '
                f'border-danger rounded-sm px-3 py-2 mb-3">'
                f"Wait for the countdown — only {verify.elapsed:.1f}s "
                f"elapsed.</div>",
                status_code=400,
            )

        try:
            new_inst = delete_l2_entity(cache.get(), kind, entity_id)
        except KeyError:
            return HTMLResponse("not found", status_code=404)

        try:
            validate(new_inst)
        except L2ValidationError as exc:
            return HTMLResponse(
                f'<div role="alert" class="text-sm text-danger bg-red-50 '
                f'border border-danger rounded-sm px-3 py-2 mb-3">'
                f"Cannot delete: {escape(str(exc))}</div>",
                status_code=400,
            )

        cache.save(new_inst)
        # CG.19 (2026-06-05) — the edit form's Delete button passes
        # ?from=edit; on success the handler responds with HX-Redirect
        # to the list page so the operator lands on the kind's list
        # instead of seeing the now-stale edit form. Card-source
        # deletes keep the empty-body shape (HX-Swap removes the
        # banner; the home page's cascade-reload listener refreshes
        # the relevant section).
        from_source = request.query_params.get("from", "")
        if from_source == "edit":
            resp = HTMLResponse("")
            resp.headers["HX-Redirect"] = f"/l2_shape/{kind}/"
            resp.headers["HX-Trigger"] = "l2-cascade-reload"
            return resp
        # Empty body — the chrome's HX-Swap removes the banner.
        resp = HTMLResponse("")
        resp.headers["HX-Trigger"] = "l2-cascade-reload"
        return resp

    async def delete_confirm_handler(request: Request) -> Response:
        """BX.1 — GET /l2_shape/<kind>/<id>/delete-confirm: returns
        the inline confirm banner. Walks the L2Instance for incoming
        references first; when refs are present the banner renders
        in `blocked` mode (no Confirm button, listing each referrer
        with an edit link). When refs are absent the banner renders
        in `armed` mode with a 5s countdown + signed Confirm token.

        Lives behind GET (not POST) so it's idempotent + safe to
        re-fire if the operator clicks Delete twice.

        BX.10 (2026-06-11) — for composite-keyed kinds the path is
        opaque (``<hash6>-<slug>``); resolved to the composite key
        once up front. The composite key feeds the find-references /
        HMAC-issue calls below so the operator-visible banner shows
        a human-readable id and the DELETE URL the banner builds
        carries the same composite (matching the HMAC payload).
        """
        kind = _kind_from_path(request.path_params["kind"])
        url_seg = request.path_params["entity_id"]
        if kind is None or kind not in _FIELD_SPECS_BY_KIND:
            return HTMLResponse("not editable", status_code=404)
        instance = cache.get()
        resolved_entity, _canonical = _resolve_url_entity_id(
            instance, kind, url_seg,
        )
        if resolved_entity is None:
            # Same 410-Gone shape as the save / read-card mid-flight
            # delete race.
            return _entity_gone_response(kind, url_seg)
        entity_id = _entity_id(kind, resolved_entity)
        refs = _find_references(instance, kind, entity_id)
        banner_html = render_delete_confirm_banner(
            kind, entity_id, refs,
            countdown_secs=_delete_confirm.COUNTDOWN_SECS,
        )
        return HTMLResponse(banner_html)

    async def delete_cancel_handler(_request: Request) -> HTMLResponse:
        """BX.1 — GET /l2_shape/_delete_cancel: returns an empty
        replacement so the Cancel button kills the in-flight banner.
        The Cancel button's hx-swap=outerHTML on `#delete-confirm-banner`
        replaces the banner aside with the empty-string body — leaving
        the slot div empty and ready for the next Delete click.
        """
        return HTMLResponse("")

    async def preview_markdown(request: Request) -> HTMLResponse:
        """BF.9 (2026-05-25) — markdown → HTML preview endpoint.

        POST body is a form-encoded payload with ONE field name
        (any name; HTMX's ``hx-include`` scopes the source). Reads
        the FIRST non-meta form value + renders via Python-Markdown.
        Returns the rendered HTML for the htmx swap target.

        Lazy-imports `markdown` so the studio surface boots without
        a hard dep on Python-Markdown (it ships transitively via
        mkdocs but isn't in the studio's required-extras list).
        """
        import markdown as _md  # noqa: PLC0415 — lazy
        form = await request.form()
        # Take the first non-empty value; HTMX's hx-include scopes
        # the include set so there's exactly one textarea on POST.
        text = ""
        for k, v in form.multi_items():
            if k.startswith("_") or k == "csrf":
                continue
            text = str(v)
            if text:
                break
        if not text.strip():
            return HTMLResponse(
                '<p class="text-secondary-fg italic m-0">(empty — nothing to preview)</p>',
            )
        # extras=[]: stay vanilla. The handbook templates render at
        # build time + escape per their own template logic; the
        # studio preview is informational, NOT load-bearing.
        rendered = _md.markdown(text, extensions=["fenced_code", "tables"])
        return HTMLResponse(rendered)

    return {
        "list_view": list_view,
        "read_card": read_card,
        "edit_form": edit_form,
        "save": save,
        "delete": delete_handler,
        "delete_confirm": delete_confirm_handler,
        "delete_cancel": delete_cancel_handler,
        "new_form": new_form,
        "create": create,
        "preview_markdown": preview_markdown,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_VALID_KINDS: frozenset[str] = frozenset(
    ("account", "account_template", "rail", "transfer_template", "chain",
     "limit_schedule",
     # X.4.f.12 — singletons (theme, persona) are valid kinds for the
     # URL path; the route handlers branch on SINGLETON_KINDS to use
     # the singleton form/save flow instead of list/CRUD.
     # AI.2.c — ``instance`` is the third singleton (top-level
     # description + institution_name + institution_acronym).
     "theme", "persona", "instance"),
)


def _kind_from_path(raw: str) -> EntityKind | None:
    """Coerce the URL path slug to a typed EntityKind. None if invalid."""
    if raw in _VALID_KINDS:
        return raw  # type: ignore[return-value]: validated against the typed Literal set
    return None


def _rename_trigger_field(kind: EntityKind) -> str | None:
    """Which field's change should cascade across L2 references.

    Per ``editor.rename_identifier``:

    - account / account_template — ``role`` is the cross-cutting
      identifier (Rail.source_role, parent_role, LimitSchedule.parent_role,
      …). Account.id is addressing-only — no incoming references.
    - rail / transfer_template — ``name`` is both the addressing key
      AND the reference target (TransferTemplate.leg_rails,
      Rail.bundles_activity, Chain.parent / Chain.children entries).
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


def _entity_gone_response(kind: EntityKind, entity_id: str) -> HTMLResponse:
    """CR.11 (2026-06-08) — clean 410 Gone response for the mid-flight
    delete race.

    Replaces the pre-CR.11 ``_placeholder`` defensive fallback that
    only implemented ``"account"`` (the 5 other ``EntityKind`` values
    raised ``NotImplementedError`` from the same call sites, turning a
    "rare race" into a 500 page for anyone deleting a non-account
    entity mid-edit).

    The race shape: operator opens an edit form, the entity gets
    deleted from another tab / a colliding write, operator submits the
    form. The pre-CR.11 path tried to re-render the edit form with a
    placeholder entity; the new path tells the operator clearly that
    the entity is gone + offers a link back to the list view. No data
    is lost (the form values are gone but they were targeting a
    deleted entity anyway).
    """
    safe_id = escape(entity_id)
    safe_kind = escape(kind)
    body = (
        f"<!doctype html><html><head><title>Entity gone</title></head>"
        f"<body><h1>{safe_kind} {safe_id!s} no longer exists</h1>"
        f"<p>The entity was deleted while you were editing — likely "
        f"in another tab or by a collaborator. Your form values "
        f"targeted the deleted entity and cannot be saved.</p>"
        f"<p><a href=\"/\">Back to Studio</a></p></body></html>"
    )
    return HTMLResponse(body, status_code=410)


# ---------------------------------------------------------------------------
# Public route-list factory
# ---------------------------------------------------------------------------


def make_editor_routes(
    cache: L2InstanceCache,
    *,
    top_nav_fn: Callable[[str], str] | None = None,
) -> list[Route]:
    """Build the editor route list bound to ``cache``.

    Spliced into ``make_studio_routes`` (X.4.e.7) so the cache + the
    diagram routes share one in-memory instance per server.

    CU.3: all editor routes are always mounted. The security perimeter
    moved entirely to the sandbox-exec writable-allowlist + the
    wrapper-driven L2 yaml overlay (writes land in a per-launch tmpdir
    that wipes on restart). Demo installs point ``--l2`` at the
    overlay; mutation writes succeed against the overlay but never
    touch canonical.
    """
    h = _make_handlers(cache, top_nav_fn=top_nav_fn)
    # ``/new`` MUST be declared before ``/{entity_id}`` so Starlette's
    # path matcher doesn't treat the literal "new" as an entity_id.
    # BX.1 (2026-06-11): the ``/_delete_cancel`` literal MUST be
    # declared before the ``/{kind}/`` catch-all for the same reason.
    routes: list[Route] = [
        # BX.1 — delete-confirm cancel endpoint. Empty-body GET so
        # Cancel button hx-swap=outerHTML kills the in-flight banner.
        Route(
            "/l2_shape/_delete_cancel", h["delete_cancel"],
            methods=["GET"], name="l2_shape_delete_cancel",
        ),
        Route(
            "/l2_shape/{kind}/", h["list_view"], methods=["GET"],
        ),
        Route(
            "/l2_shape/{kind}/", h["create"], methods=["POST"],
            name="l2_shape_create",
        ),
        Route(
            "/l2_shape/{kind}/new", h["new_form"], methods=["GET"],
            name="l2_shape_new_form",
        ),
        # BX.1 — delete-confirm banner endpoint. Declared BEFORE the
        # bare-id read route so Starlette doesn't treat
        # "delete-confirm" as an entity_id.
        Route(
            "/l2_shape/{kind}/{entity_id}/delete-confirm",
            h["delete_confirm"],
            methods=["GET"], name="l2_shape_delete_confirm",
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
            methods=["POST", "PUT"], name="l2_shape_save",
        ),
        Route(
            "/l2_shape/{kind}/{entity_id}", h["delete"],
            methods=["DELETE"], name="l2_shape_delete",
        ),
        # BF.9 (2026-05-25) — markdown preview endpoint for the
        # description-field Edit/Preview tabs.
        Route(
            "/preview/markdown", h["preview_markdown"],
            methods=["POST"], name="preview_markdown",
        ),
    ]
    return routes
