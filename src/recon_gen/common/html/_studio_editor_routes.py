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

from recon_gen.common.html._studio_routes import asset_url, studio_theme_head
from recon_gen.common.l2.cache import L2InstanceCache
from recon_gen.common.l2.editor import (
    SINGLETON_KINDS,
    EntityKind,
    create_l2_entity,
    delete_l2_entity,
    mutate_l2,
    rename_identifier,
    singleton_save_l2,
)
from recon_gen.common.l2.primitives import (
    Account,
    FiringsTypicalPerPeriod,
    Identifier,
    Money,
    Name,
    Period,
)
from recon_gen.common.l2.validate import L2ValidationError, validate


# ---------------------------------------------------------------------------
# Field-spec dispatch — per-entity form layout
# ---------------------------------------------------------------------------


FieldKind: TypeAlias = Literal[
    "text", "select", "money", "textarea", "multi_select", "yaml_block",
    "multi_select_groups", "chain_children",
]

# X.4.f.11 — Rail is a discriminated union (TwoLegRail | SingleLegRail).
# A FieldSpec marked with one of these subtypes only renders / coerces
# when the entity matches; cross-subtype fields stay None.
RailSubtype: TypeAlias = Literal["two_leg", "single_leg"]


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
# editor's mutate_l2 dispatches on `dataclasses.replace`. X.4.f.11
# adds the load-bearing subtype-discriminating fields (source_role /
# destination_role on TwoLeg; leg_role / leg_direction on Single)
# gated by FieldSpec.subtype_only — the renderer + read card filter
# them based on the rail entity's actual subtype at edit time.
_RAIL_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        name="name",
        label="Name",
        helper="Unique rail identifier; referenced by chains + templates.",
        kind="text",
        required=True,
    ),
    # X.4.f.11.2 — TwoLegRail per-leg roles. RoleExpression is
    # tuple[Identifier, ...]; multi-select renders the list as a
    # union ("any of these roles is admissible at posting time").
    # Single-role rails select one option; the loader normalizes.
    FieldSpec(
        name="source_role",
        label="Source role",
        helper=(
            "Role of the account the debit leg posts to. Multi-select "
            "for unioned roles. Required on TwoLegRail."
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
            "Role of the account the credit leg posts to. Multi-select "
            "for unioned roles. Required on TwoLegRail."
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
    ),
    FieldSpec(
        name="destination_origin",
        label="Destination origin (override)",
        helper="Per-leg override. Blank ⇒ use the rail-level Origin for both legs.",
        kind="text",
        subtype_only="two_leg",
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
    ),
    # X.4.f.11.6 — metadata_keys + posted_requirements (both subtypes).
    # tuple[Identifier, ...] — operator types one key per line; coerce
    # splits on \n + comma, strips blanks. Empty textarea ⇒ empty tuple.
    FieldSpec(
        name="metadata_keys",
        label="Metadata keys",
        helper=(
            "One per line (or comma-separated). Identifies the metadata "
            "keys this rail's transactions carry (e.g. ach_trace_number, "
            "wire_imad)."
        ),
        kind="textarea",
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
    ),
    # X.4.f.11.9 — bundles_activity (aggregating rails only).
    # tuple[BundlesActivityRef = Identifier, ...] — multi-select from
    # rails + templates; matches by Rail.name or TransferTemplate.name.
    FieldSpec(
        name="bundles_activity",
        label="Bundles activity",
        helper=(
            "For aggregating rails only. Names the rails / templates "
            "whose Transactions this rail bundles. Multi-select."
        ),
        kind="multi_select",
        select_from="rails_or_templates",
    ),
    # X.4.f.11.6.5 — Tier-3 metadata_value_examples as a YAML block.
    # tuple[(Identifier, tuple[str, ...]), ...] — operator types/edits
    # the per-key example map directly in YAML (the same shape the L2
    # YAML carries). Empty ⇒ demo seed falls back to the synthetic
    # `<rail>-firing-<seq>` placeholder.
    FieldSpec(
        name="metadata_value_examples",
        label="Metadata value examples",
        helper=(
            "Per-key example values the demo seed cycles through. "
            "YAML map: each metadata key → list of example strings. "
            "Empty ⇒ uses synthetic per-rail fallback. Example: "
            "ach_trace_number: [\"12345-001\", \"12345-002\"]"
        ),
        kind="yaml_block",
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
# X.4.f.10 — parent + child are now dropdowns of valid rail/template
# names (was free text; typo'd values reached the validator only).
_CHAIN_FIELDS: tuple[FieldSpec, ...] = (
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
    ),
    FieldSpec(
        name="description",
        label="Description",
        helper="Free-form prose.",
        kind="textarea",
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
        name="leg_rails",
        label="Leg rails",
        helper=(
            "The Rails this template owns. Cmd/Ctrl-click to multi-select. "
            "Empty selection is rejected by the validator (a template must "
            "have at least one leg rail) — add a replacement before removing "
            "the last one, or delete the whole template instead."
        ),
        kind="multi_select",
        select_from="rails",
        required=True,
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
    # X.4.f.11.6 — Rail.metadata_keys + posted_requirements: textarea
    # one-per-line (or comma-separated). tuple[Identifier, ...].
    if spec.name in ("metadata_keys", "posted_requirements") and kind == "rail":
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
        for k, v in parsed.items():  # pyright: ignore[reportUnknownVariableType]  # WHY: yaml.safe_load returns Any-typed dict
            if not isinstance(v, list):
                raise ValueError(
                    f"Key {k!r}: expected a list of strings, "
                    f"got {type(v).__name__}",
                )
            result.append((
                Identifier(str(k)),
                tuple(str(item) for item in v),  # pyright: ignore[reportUnknownVariableType]  # WHY: list element type from yaml is Any
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

    if spec.kind == "multi_select_groups":
        return _render_multi_select_groups_field(
            spec, value, entity, error,
        )

    if spec.kind == "chain_children":
        return _render_chain_children_field(spec, value, instance, error)

    if spec.kind == "multi_select":
        # Render a checkbox group — easier than Cmd/Ctrl-clicking a
        # <select multiple>. Each checkbox submits its own form-data
        # entry on check; getlist(name) on the server side
        # reconstructs the tuple. Hidden marker ensures the field is
        # always present in the form so an empty selection (operator
        # unchecked everything) is distinguishable from "field absent" —
        # which lets the validator catch the empty-leg_rails case.
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
        check_blocks = [
            f'<label class="multi-select-item">'
            f'<input type="checkbox" name="{escape(spec.name)}" '
            f'value="{escape(o)}"'
            f'{" checked" if o in selected else ""}>'
            f' {escape(o)}</label>'
            for o in options
        ]
        input_html = (
            # Hidden marker — see comment above.
            f'<input type="hidden" name="{escape(spec.name)}__present" value="1">'
            f'<div id="field-{spec.name}" class="multi-select-group" '
            f'role="group">'
            f'{"".join(check_blocks)}</div>'
        )
    elif spec.kind == "select":
        val_str = _value_to_input_str(value)
        if spec.select_from is not None:
            options, allow_empty = _resolve_select_options(
                spec.select_from, instance, val_str,
            )
        else:
            options, allow_empty = spec.options, False
        opt_blocks = []
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
        val_str = _value_to_input_str(value)
        input_html = (
            f'<textarea id="field-{spec.name}" name="{escape(spec.name)}" '
            f'rows="3">{escape(val_str)}</textarea>'
        )
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
            f'rows="10" class="yaml-block" spellcheck="false">'
            f'{escape(val_str)}</textarea>'
        )
    else:
        # text + money both render as <input type="text"> — the loader's
        # _load_money handles numeric strings either way.
        val_str = _value_to_input_str(value)
        input_html = (
            f'<input id="field-{spec.name}" name="{escape(spec.name)}" '
            f'type="text" value="{escape(val_str)}">'
        )

    return (
        f'<div class="field-row">{label}{input_html}{helper}{err_html}</div>'
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
        f'<label>{escape(spec.label)}</label>'
    )
    helper_html = (
        f'<small class="field-helper">{escape(spec.helper)}</small>'
        if spec.helper else ""
    )
    err_html = (
        f'<div class="field-error">{escape(error)}</div>' if error else ""
    )
    # Option universe = the entity's leg_rails (sibling field). On the
    # create page there's no entity; render the empty-state helper.
    if entity is None:
        empty_msg = (
            "Save the template with at least 2 leg rails first; then "
            "open it for editing to add XOR groups."
        )
        body = (
            f'<div class="multi-select-groups-empty">{escape(empty_msg)}</div>'
        )
        return (
            f'<div class="field-row">'
            f'{label_html}{body}{helper_html}{err_html}</div>'
        )
    leg_rails_raw = getattr(entity, "leg_rails", ()) or ()
    rails: tuple[str, ...] = tuple(
        str(r)  # pyright: ignore[reportUnknownArgumentType]  # WHY: leg_rails element type is Identifier (runtime str) but typed as Any here
        for r in leg_rails_raw  # pyright: ignore[reportUnknownVariableType]  # WHY: leg_rails is Any
    )
    groups = _multi_select_groups_value_as_groups(value)
    if not rails:
        empty_msg = (
            "Add at least 2 leg rails to this template, save, then "
            "reopen the edit form to author XOR groups."
        )
        body = (
            f'<div class="multi-select-groups-empty">{escape(empty_msg)}</div>'
            f'<input type="hidden" name="{escape(spec.name)}__present" value="1">'
            f'<input type="hidden" name="{escape(spec.name)}__num_groups" value="0">'
        )
        return (
            f'<div class="field-row">'
            f'{label_html}{body}{helper_html}{err_html}</div>'
        )
    # Render N existing groups + 1 always-empty trailing slot for
    # adding a new group. Unchecking every box in a row drops that
    # group on save (server filters empty groups).
    blocks: list[str] = []
    for i, group in enumerate(groups):
        blocks.append(_render_xor_group_row(spec.name, i, rails, group))
    blocks.append(_render_xor_group_row(spec.name, len(groups), rails, ()))
    num_groups = len(groups) + 1
    body = (
        f'<div id="field-{escape(spec.name)}" '
        f'class="multi-select-groups" role="group">'
        f'{"".join(blocks)}'
        f'</div>'
        f'<input type="hidden" name="{escape(spec.name)}__present" value="1">'
        f'<input type="hidden" name="{escape(spec.name)}__num_groups" '
        f'value="{num_groups}">'
    )
    return (
        f'<div class="field-row">'
        f'{label_html}{body}{helper_html}{err_html}</div>'
    )


def _render_xor_group_row(
    name: str,
    index: int,
    rails: tuple[str, ...],
    selected: tuple[str, ...],
) -> str:
    """One <fieldset> with all template leg_rails as checkboxes; those
    in ``selected`` start checked. Empty selected → "Add new XOR group"
    trailing slot."""
    selected_set = frozenset(selected)
    is_new = not selected_set
    legend = (
        "Add new XOR group" if is_new
        else f"XOR group {index + 1}"
    )
    items = "".join(
        f'<label class="multi-select-item">'
        f'<input type="checkbox" name="{escape(name)}_{index}" '
        f'value="{escape(r)}"'
        f'{" checked" if r in selected_set else ""}>'
        f' {escape(r)}</label>'
        for r in rails
    )
    css_class = "xor-group new" if is_new else "xor-group"
    return (
        f'<fieldset class="{css_class}" data-group-index="{index}">'
        f'<legend>{escape(legend)}</legend>'
        f'<div class="multi-select-group">{items}</div>'
        f'</fieldset>'
    )


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
        for item in value:  # pyright: ignore[reportUnknownVariableType]  # WHY: form-typed; isinstance gates below narrow per item
            if hasattr(item, "name") and hasattr(item, "fan_in"):
                name = str(getattr(item, "name"))
                fan_in = bool(getattr(item, "fan_in", False))
                epc_raw = getattr(item, "expected_parent_count", None)
                epc: int | None = (
                    int(epc_raw) if epc_raw is not None and epc_raw != "" else None
                )
                out.append((name, fan_in, epc))
            else:
                # Validation-failure path: tuple-of-strings (operator's
                # last submission). fan_in / epc came from sibling form
                # fields, not the value itself — defaulted here; the
                # form_overrides dict carries the per-child shape.
                out.append((str(item), False, None))
        return tuple(out)
    return ()


def _render_chain_children_field(
    spec: FieldSpec,
    value: object,
    instance: Any,  # typing-smell: ignore[explicit-any]: L2Instance — for select_from resolution
    error: str | None,
) -> str:
    """AB.6.7 — render the chain.children multi-select with per-child
    fan_in + expected_parent_count sub-inputs.

    Layout: one checkbox per available rail/template. Each checkbox
    sits in a row that also carries a ``fan-in`` checkbox + an
    ``expected-parent-count`` text input. The sub-inputs are named
    ``fan_in_<child_name>`` / ``epc_<child_name>`` so the server can
    associate them with the right child without an index-based dance.

    On save, the server reads `getlist("children")` for selected
    names, then per name reads the sub-inputs to build a tuple of
    ``ChainChildSpec(name, fan_in, expected_parent_count)``.

    Per AB.6.0 validator C8a, picking the fan-in checkbox while the
    matching child is a Rail (not TransferTemplate) is rejected on
    submit with an inline error.
    """
    if spec.select_from is None:
        raise ValueError(
            f"chain_children FieldSpec {spec.name!r} requires select_from",
        )
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

    options, _ = _resolve_select_options(spec.select_from, instance, "")
    existing = _chain_children_value_as_specs(value)
    selected_by_name = {name: (fan_in, epc) for name, fan_in, epc in existing}
    # Defensive: any selected child not in the option set still shows
    # (stale reference; validator surfaces the broken ref separately).
    for name in selected_by_name:
        if name not in options:
            options = (*options, name)

    rows: list[str] = []
    for opt in options:
        is_selected = opt in selected_by_name
        fan_in, epc = selected_by_name.get(opt, (False, None))
        epc_str = str(epc) if epc is not None else ""
        rows.append(
            f'<div class="chain-child-row" data-child="{escape(opt)}">'
            f'<label class="multi-select-item">'
            f'<input type="checkbox" name="children" '
            f'value="{escape(opt)}"{" checked" if is_selected else ""}>'
            f' {escape(opt)}</label>'
            f'<label class="chain-child-fanin">'
            f'<input type="checkbox" name="fan_in_{escape(opt)}" '
            f'value="true"{" checked" if fan_in else ""}>'
            f' fan-in</label>'
            f'<label class="chain-child-epc">'
            f' epc:&nbsp;'
            f'<input type="text" name="epc_{escape(opt)}" '
            f'value="{escape(epc_str)}" size="3" '
            f'placeholder="—" inputmode="numeric"></label>'
            f"</div>"
        )

    # Hidden marker so the server distinguishes "form rendered with
    # empty selection" from "field absent" (same shape multi_select uses).
    input_html = (
        f'<input type="hidden" name="children__present" value="1">'
        f'<div id="field-{spec.name}" class="chain-children-group" '
        f'role="group">{"".join(rows)}</div>'
    )
    return (
        f'<div class="form-field form-field-chain-children">'
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


def _render_read_value(spec: FieldSpec, value: object) -> str:
    """Render a dataclass field value for the read-only card.

    Most fields fall through to ``_value_to_input_str``. AB.3.7's
    ``multi_select_groups`` is the exception: a nested
    tuple-of-tuples needs a per-group bullet display, since the
    flat-tuple stringifier would print the inner tuple's ``repr``
    (``('A', 'B'), ('C', 'D')`` — readable but cluttered).
    """
    if spec.kind == "multi_select_groups":
        groups = _multi_select_groups_value_as_groups(value)
        if not groups:
            return "—"
        items = "".join(
            f'<li>group {i + 1}: {escape(", ".join(g))}</li>'
            for i, g in enumerate(groups)
        )
        return f'<ul class="xor-group-list">{items}</ul>'
    if spec.kind == "chain_children":
        children = _chain_children_value_as_specs(value)
        if not children:
            return "—"
        items: list[str] = []
        for name, fan_in, epc in children:
            tag = ""
            if fan_in:
                epc_str = (
                    f" epc={epc}" if epc is not None else " (variable batch)"
                )
                tag = f' <span class="chain-child-fanin-tag">[fan-in{epc_str}]</span>'
            items.append(f"<li>{escape(name)}{tag}</li>")
        return f'<ul class="chain-children-list">{"".join(items)}</ul>'
    return escape(_value_to_input_str(value)) or "—"


def _render_read_card(
    kind: EntityKind, entity: object,
    instance: Any,  # typing-smell: ignore[explicit-any]: L2Instance — needed to suppress fields hidden by the two-layer rule
    *, demo_mode: bool = False,
) -> str:
    """Read-only card — the post-PUT response + the click-to-expand
    target for the list view.

    AH.4: ``demo_mode`` drops the Edit / Delete actions (their routes
    are 404'd in demo-mode anyway — the buttons shouldn't appear).
    """
    specs = _filter_specs_for_entity(_FIELD_SPECS_BY_KIND[kind], entity)
    entity_id = _entity_id(kind, entity)
    hidden = _hidden_fields_for_entity(kind, entity, instance)
    rows = "".join(
        f'<dt>{escape(s.label)}</dt><dd>'
        f"{_render_read_value(s, getattr(entity, s.name, None))}"
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
    # X.4.f.11 — surface rail subtype as a small badge on the read
    # card so the operator can tell a TwoLeg apart from a SingleLeg
    # at a glance. Non-rail entities don't get a badge.
    subtype_badge = ""
    rail_subtype = _rail_subtype_of(entity)
    if rail_subtype is not None:
        subtype_label = "two-leg" if rail_subtype == "two_leg" else "single-leg"
        subtype_badge = (
            f' <span class="entity-subtype-badge">{escape(subtype_label)}</span>'
        )
    if focus_node is None:
        title_html = f"<h3>{escape(entity_id)}{subtype_badge}</h3>"
    else:
        title_html = (
            f'<h3 class="entity-card-title" tabindex="0" role="button" '
            f'data-focus-node="{escape(focus_node)}" '
            f'title="Focus the diagram on this entity">'
            f"{escape(entity_id)}{subtype_badge}</h3>"
        )
    # CSS-safe id slug — composite-keyed kinds use ``::`` in their
    # addressing string, which CSS parses as pseudo-element syntax in a
    # selector like ``#entity-chain-Foo::Bar``. The URL-side path stays
    # ``::`` (matches the L2 API key contract); only the HTML id swaps.
    html_id = f"entity-{kind}-{escape(_html_id_slug(entity_id))}"
    # X.4.f.9.delete — DELETE on success returns empty (card disappears
    # via outerHTML swap); on validator-rejected structural break returns
    # 400 + the error fragment which swaps in place. No cascade — the
    # operator clears the dependent reference first. AH.4: omitted in
    # demo-mode (the edit / delete routes are 404'd there).
    actions_html = "" if demo_mode else (
        f'<div class="entity-card-actions">'
        f'<a class="edit-link" href="/l2_shape/{kind}/{escape(entity_id)}/edit">Edit</a>'
        f'<a class="delete-link" hx-delete="/l2_shape/{kind}/{escape(entity_id)}" '
        f'hx-target="#{html_id}" hx-swap="outerHTML" '
        f'hx-confirm="Delete this entity? References that block deletion '
        f'will be reported inline.">Delete</a>'
        f"</div>"
    )
    return (
        f'<article class="entity-card" id="{html_id}" '
        f'data-kind="{escape(kind)}" data-entity-id="{escape(entity_id)}">'
        f"<header>"
        f"{title_html}"
        f"{actions_html}"
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
) -> str:
    """The Rail-only subtype picker landing page.

    Step 1 of the 2-step create flow. Two big buttons, each linking to
    ``/l2_shape/rail/new?subtype=<two_leg|single_leg>`` so the picked
    subtype shows up as a query param on step 2 (back button works).
    """
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Create new rail — pick subtype — Studio</title>
  {studio_theme_head(instance)}
  <link rel="stylesheet" href="{asset_url("diagram.css")}">
  <link rel="stylesheet" href="{asset_url("editor.css")}">
</head>
<body class="create-page">
  <header class="studio-header">
    <h1>Create new rail</h1>
    <a class="nav-link" href="/">← back to Studio</a>
    <a class="nav-link" href="/l2_shape/rail/">→ list all rails</a>
  </header>
  <main class="create-page-main">
    <section class="create-intro">{_RAIL_SUBTYPE_PICKER_INTRO}</section>
    <section class="create-form-wrap">
      <div class="rail-subtype-picker">
        <a class="rail-subtype-button" href="/l2_shape/rail/new?subtype=two_leg">
          <strong>Two-leg rail →</strong>
          <small>Debit + credit per firing (ACH, wire, internal, settlement)</small>
        </a>
        <a class="rail-subtype-button" href="/l2_shape/rail/new?subtype=single_leg">
          <strong>Single-leg rail →</strong>
          <small>One leg per firing (fee, charge, sub-template leg)</small>
        </a>
      </div>
    </section>
  </main>
</body>
</html>
"""


def _render_create_page(
    kind: EntityKind,
    instance: Any,  # typing-smell: ignore[explicit-any]: L2Instance — needed for select_from option resolution
    form_overrides: Mapping[str, str | tuple[str, ...]] | None = None,
    global_error: str | None = None,
    subtype: RailSubtype | None = None,
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
    # AB.3.7 — edit-only fields (e.g. ``leg_rail_xor_groups``) reference
    # sibling dataclass fields that don't exist yet on the create page;
    # filter them out so the operator authors the sibling first.
    specs = tuple(s for s in specs if not s.edit_only)
    overrides = form_overrides or {}
    fields_html = "".join(
        _render_field(s, overrides.get(s.name, ""), instance)
        for s in specs
    )
    # Hidden subtype input — POST handler picks it up via _coerce_form's
    # passthrough on form keys not in the FieldSpec list (the create
    # branch in create_l2_entity reads fields["subtype"] directly).
    subtype_html = (
        f'<input type="hidden" name="subtype" value="{escape(subtype)}">'
        if subtype is not None else ""
    )
    global_err_html = (
        f'<div class="form-global-error">{escape(global_error)}</div>'
        if global_error else ""
    )
    intro_html = _CREATE_INTRO_BY_KIND.get(kind, "")
    # When a Rail subtype is picked, surface it in the page title so the
    # operator can see they're filling in the right form.
    title_suffix = (
        f" ({'two-leg' if subtype == 'two_leg' else 'single-leg'})"
        if subtype is not None else ""
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Create new {escape(kind)}{escape(title_suffix)} — Studio</title>
  {studio_theme_head(instance)}
  <link rel="stylesheet" href="{asset_url("diagram.css")}">
  <link rel="stylesheet" href="{asset_url("editor.css")}">
</head>
<body class="create-page">
  <header class="studio-header">
    <h1>Create new {escape(kind)}{escape(title_suffix)}</h1>
    <a class="nav-link" href="/">← back to Studio</a>
    <a class="nav-link" href="/l2_shape/{escape(kind)}/">→ list all {escape(kind)}s</a>
  </header>
  <main class="create-page-main">
    <section class="create-intro">{intro_html}</section>
    <section class="create-form-wrap">
      <form method="post" action="/l2_shape/{escape(kind)}/" class="create-form">
        {global_err_html}
        {subtype_html}
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


def _render_edit_page(
    kind: EntityKind,
    entity: object,
    instance: Any,  # typing-smell: ignore[explicit-any]: L2Instance — needed for select_from option resolution
    form_overrides: Mapping[str, str | tuple[str, ...]] | None = None,
    global_error: str | None = None,
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
        f'<div class="form-global-error">{escape(global_error)}</div>'
        if global_error else ""
    )
    intro_html = _CREATE_INTRO_BY_KIND.get(kind, "")
    rail_subtype = _rail_subtype_of(entity)
    title_suffix = (
        f" ({'two-leg' if rail_subtype == 'two_leg' else 'single-leg'})"
        if rail_subtype is not None else ""
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Edit {escape(kind)}: {escape(entity_id)} — Studio</title>
  {studio_theme_head(instance)}
  <link rel="stylesheet" href="{asset_url("diagram.css")}">
  <link rel="stylesheet" href="{asset_url("editor.css")}">
</head>
<body class="create-page edit-page">
  <header class="studio-header">
    <h1>Edit {escape(kind)}{escape(title_suffix)}: {escape(entity_id)}</h1>
    <a class="nav-link" href="/">← back to Studio</a>
    <a class="nav-link" href="/l2_shape/{escape(kind)}/">→ list all {escape(kind)}s</a>
  </header>
  <main class="create-page-main">
    <section class="create-intro">{intro_html}</section>
    <section class="create-form-wrap">
      <form method="post" action="/l2_shape/{escape(kind)}/{escape(entity_id)}" class="create-form edit-form">
        {global_err_html}
        {fields_html}
        <div class="form-actions">
          <button type="submit">Save</button>
          <a class="cancel-link" href="/">Cancel</a>
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
        "<p><strong>Theme</strong> is the institution's brand palette — the "
        "colors that drive every dashboard, the studio chrome, and the "
        "audit PDF cover. Edit the YAML below; an empty block clears the "
        "theme and the bundled DEFAULT_PRESET takes over.</p>"
        "<p>The shape mirrors <code>ThemePreset</code> in "
        "<code>common/l2/theme.py</code> — every field is required when "
        "the block is set: <code>theme_name</code>, "
        "<code>version_description</code>, "
        "<code>analysis_name_prefix</code> (or null), "
        "<code>data_colors</code> (≥1 hex), <code>empty_fill_color</code>, "
        "<code>gradient</code> ([light, dark] hex pair), plus the UI "
        "palette (<code>accent</code>, <code>primary_fg</code>, etc.).</p>"
    ),
    "persona": (
        "Persona",
        "<p><strong>Persona</strong> is the institution's flavor strings "
        "— name, acronym, upstream stakeholders, GL chart, merchant names, "
        "free-form prose. The handbook templates read these to render "
        "branded prose; an empty block falls back to neutral "
        "L2-primitive-derived language.</p>"
        "<p>The shape mirrors <code>DemoPersona</code> in "
        "<code>common/persona.py</code>. Each top-level key is optional; "
        "omit the keys you don't want to populate. <code>gl_accounts</code> "
        "items are <code>{code, name, note}</code> sub-maps — see the "
        "bundled <code>tests/l2/sasquatch_pr.yaml</code> for a reference "
        "shape.</p>"
    ),
}


def _singleton_yaml_text(instance: object, kind: EntityKind) -> str:
    """Dump the singleton attribute as a YAML map for the textarea.

    None / unset ⇒ empty string (operator sees a blank textarea +
    intro prose explaining what an empty block means).
    """
    import dataclasses as dc  # noqa: PLC0415 — lazy
    import yaml  # noqa: PLC0415 — lazy

    attr = "theme" if kind == "theme" else "persona"
    value = getattr(instance, attr, None)
    if value is None:
        return ""
    # Walk the dataclass to a plain dict that yaml.safe_dump can handle.
    # The L2 loader's per-kind helper round-trips this cleanly.
    as_dict = dc.asdict(value)  # pyright: ignore[reportUnknownArgumentType,reportUnknownVariableType]  # WHY: ThemePreset/DemoPersona are dataclasses; asdict returns plain dict[str, Any]
    return yaml.safe_dump(as_dict, default_flow_style=False, sort_keys=False).rstrip() + "\n"


def _render_singleton_page(
    kind: EntityKind,
    instance: Any,  # typing-smell: ignore[explicit-any]: L2Instance — read attribute + theme head
    yaml_text: str | None = None,
    global_error: str | None = None,
) -> str:
    """X.4.f.12 — singleton edit page (Theme / Persona).

    Single textarea carrying the entire YAML subtree. The operator's
    mental model is "this is the YAML block in the L2 file" — match
    that exactly. v1 has no per-field color pickers / nested editors;
    polish lands as a follow-on if the cosmetic-edit frequency turns
    out high enough to warrant it.
    """
    label, intro_html = _SINGLETON_INTRO_BY_KIND[kind]
    current_yaml = yaml_text if yaml_text is not None else _singleton_yaml_text(instance, kind)
    global_err_html = (
        f'<div class="form-global-error">{escape(global_error)}</div>'
        if global_error else ""
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{escape(label)} — Studio</title>
  {studio_theme_head(instance)}
  <link rel="stylesheet" href="{asset_url("diagram.css")}">
  <link rel="stylesheet" href="{asset_url("editor.css")}">
</head>
<body class="create-page">
  <header class="studio-header">
    <h1>{escape(label)}</h1>
    <a class="nav-link" href="/">← back to Studio</a>
  </header>
  <main class="create-page-main">
    <section class="create-intro">{intro_html}</section>
    <section class="create-form-wrap">
      <form method="post" action="/l2_shape/{escape(kind)}/" class="create-form">
        <input type="hidden" name="_method" value="PUT">
        {global_err_html}
        <div class="field-row">
          <label for="field-yaml">YAML</label>
          <textarea id="field-yaml" name="yaml" rows="22" class="yaml-block" spellcheck="false">{escape(current_yaml)}</textarea>
          <small class="field-helper">
            Empty block ⇒ clears the {escape(kind)} (silent-fallback).
            Bad YAML or missing required fields ⇒ form re-renders with
            your typed content + the validator error inline.
          </small>
        </div>
        <div class="form-actions">
          <button type="submit">Save</button>
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
    demo_mode: bool = False,
) -> str:
    """Full HTML page — every entity of the kind rendered as a read card.

    ``embed=True`` returns just the cards container (no html/head/body)
    so the X.4.f.7 home page can ``hx-get`` it into a section without
    nesting full documents. The home page's own <head> already loads
    htmx + the editor CSS + the htmx:beforeSwap fix, so the embed
    fragment doesn't need to redeclare them.
    """
    cards = "\n".join(
        _render_read_card(kind, e, instance, demo_mode=demo_mode)
        for e in entities
    )
    if embed:
        return f'<div class="entity-list" data-kind="{escape(kind)}">{cards}</div>'
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Studio editor — {escape(kind)}</title>
  {studio_theme_head(instance)}
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


def _html_id_slug(entity_id: str) -> str:
    """Sanitize an entity_id for use in an HTML ``id`` attribute.

    Composite-keyed kinds (chain / limit_schedule) use ``::`` as the
    separator in their addressing string. CSS treats ``::`` as
    pseudo-element syntax, so an ``hx-target="#entity-chain-Foo::Bar"``
    targets nothing — chain edit / delete / save would silently miss
    the card and the swap would fail with no surface error. Replacing
    ``::`` with ``__`` keeps the id CSS-selectable while leaving the
    URL-side addressing (``/l2_shape/chain/Foo::Bar``) untouched.
    """
    return entity_id.replace("::", "__")


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


def _make_handlers(cache: L2InstanceCache, *, demo_mode: bool = False) -> dict[str, Any]:  # typing-smell: ignore[explicit-any]: per-handler ASGI callables; uniform shape but per-route closure
    """Build closures over the cache for each route handler.

    Returned as a dict keyed by route name so ``make_editor_routes``
    can register them all in one pass.
    """

    async def list_view(request: Request) -> HTMLResponse:
        kind = _kind_from_path(request.path_params["kind"])
        if kind is None:
            return HTMLResponse(
                f"<h1>404</h1><p>{escape(request.path_params['kind'])} "
                f"is not an editable entity kind (yet).</p>",
                status_code=404,
            )
        # X.4.f.12 — singletons (theme, persona) skip the list view
        # entirely; GET /l2_shape/<singleton-kind>/ renders the
        # singleton edit page directly.
        if kind in SINGLETON_KINDS:
            return HTMLResponse(_render_singleton_page(kind, cache.get()))
        if kind not in _FIELD_SPECS_BY_KIND:
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
            _render_list_page(kind, entities, inst, embed=embed, demo_mode=demo_mode),
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
        return HTMLResponse(
            _render_read_card(kind, entity, inst, demo_mode=demo_mode),
        )

    async def edit_form(request: Request) -> HTMLResponse:
        kind = _kind_from_path(request.path_params["kind"])
        entity_id = request.path_params["entity_id"]
        if kind is None or kind not in _FIELD_SPECS_BY_KIND:
            return HTMLResponse("not editable", status_code=404)
        inst = cache.get()
        entity = _find_entity_or_none(inst, kind, entity_id)
        if entity is None:
            return HTMLResponse("not found", status_code=404)
        return HTMLResponse(_render_edit_page(kind, entity, inst))

    async def save(request: Request) -> Response:
        """AI.2.e — dedicated-screen save: coerce → mutate → validate →
        save → 303-redirect home (symmetric with the create POST).

        Validation / coercion failure → 400 + the full-page edit screen
        re-rendered with the error banner + the operator's typed values
        preserved. Bound to both POST and PUT /l2_shape/<kind>/<id> so the
        plain HTML edit form (POST) and any programmatic PUT both work.
        """
        kind = _kind_from_path(request.path_params["kind"])
        entity_id = request.path_params["entity_id"]
        if kind is None or kind not in _FIELD_SPECS_BY_KIND:
            return HTMLResponse("not editable", status_code=404)

        form = await request.form()
        try:
            new_fields, coerced_overrides = _coerce_form(kind, form)
        except (ValueError, TypeError) as exc:
            inst = cache.get()
            entity = _find_entity_or_none(inst, kind, entity_id)
            # Best-effort overrides — coerce_form raised before producing
            # them; capture the raw scalar fields from .items() so the
            # operator's typed values aren't lost.
            best_effort = {str(k): str(v) for k, v in form.items()}
            return HTMLResponse(
                _render_edit_page(
                    kind, entity if entity is not None else _placeholder(kind),
                    inst,
                    form_overrides=best_effort,
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
            return HTMLResponse(
                _render_edit_page(
                    kind, entity if entity is not None else _placeholder(kind),
                    inst,
                    form_overrides=coerced_overrides,
                    global_error=str(exc),
                ),
                status_code=400,
            )

        cache.save(new_inst)
        # AI.2.e — dedicated-screen flow: 303-redirect home on success,
        # symmetric with the create POST. (Replaces the X.4.e inline
        # read-card swap + HX-Trigger cascade-reload; a full navigation back
        # to Studio re-renders the diagram + entity lists fresh anyway.)
        return RedirectResponse("/", status_code=303)

    async def new_form(request: Request) -> HTMLResponse:
        kind = _kind_from_path(request.path_params["kind"])
        if kind is None or kind not in _FIELD_SPECS_BY_KIND:
            return HTMLResponse("not editable", status_code=404)
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
                return HTMLResponse(_render_rail_subtype_picker(cache.get()))
            else:
                return HTMLResponse(
                    f"unknown rail subtype: {escape(raw_subtype)}",
                    status_code=400,
                )
            return HTMLResponse(
                _render_create_page(kind, cache.get(), subtype=subtype),
            )
        return HTMLResponse(_render_create_page(kind, cache.get()))

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

        # X.4.f.12 — singleton POST (Theme / Persona). The form's
        # hidden ``_method=PUT`` confirms intent (browser form-method
        # is POST; the route table can't distinguish a singleton-save
        # from a list-create POST otherwise). The yaml field carries
        # the raw text; singleton_save_l2 parses + dispatches.
        if kind in SINGLETON_KINDS:
            yaml_text = str(form.get("yaml", ""))
            try:
                new_inst = singleton_save_l2(cache.get(), kind, yaml_text)
            except ValueError as exc:
                return HTMLResponse(
                    _render_singleton_page(
                        kind, cache.get(),
                        yaml_text=yaml_text,
                        global_error=str(exc),
                    ),
                    status_code=400,
                )
            try:
                validate(new_inst)
            except L2ValidationError as exc:
                return HTMLResponse(
                    _render_singleton_page(
                        kind, cache.get(),
                        yaml_text=yaml_text,
                        global_error=str(exc),
                    ),
                    status_code=400,
                )
            cache.save(new_inst)
            return RedirectResponse("/", status_code=303)

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
                _render_create_page(
                    kind, cache.get(),
                    form_overrides=best_effort,
                    global_error=f"Field coercion failed: {exc}",
                    subtype=rail_subtype,
                ),
                status_code=400,
            )

        # Thread subtype into the typed fields dict so create_l2_entity
        # can dispatch on it. Treat as object for the heterogeneous
        # fields-mapping value type.
        if rail_subtype is not None:
            new_fields["subtype"] = rail_subtype

        try:
            new_inst = create_l2_entity(cache.get(), kind, new_fields)
        except ValueError as exc:
            return HTMLResponse(
                _render_create_page(
                    kind, cache.get(),
                    form_overrides=coerced_overrides,
                    global_error=str(exc),
                    subtype=rail_subtype,
                ),
                status_code=400,
            )

        try:
            validate(new_inst)
        except L2ValidationError as exc:
            return HTMLResponse(
                _render_create_page(
                    kind, cache.get(),
                    form_overrides=coerced_overrides,
                    global_error=str(exc),
                    subtype=rail_subtype,
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
     "limit_schedule",
     # X.4.f.12 — singletons (theme, persona) are valid kinds for the
     # URL path; the route handlers branch on SINGLETON_KINDS to use
     # the singleton form/save flow instead of list/CRUD.
     "theme", "persona"),
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


def make_editor_routes(
    cache: L2InstanceCache,
    *,
    demo_mode: bool = False,
) -> list[Route]:
    """Build the editor route list bound to ``cache``.

    Spliced into ``make_studio_routes`` (X.4.e.7) so the cache + the
    diagram routes share one in-memory instance per server.

    When ``demo_mode=True`` (AE.2.b lockdown for public-demo hosting),
    the mutating routes (POST create / PUT save / DELETE delete) AND
    the new-entity form GET + edit-form GET are stripped — those forms
    submit to routes that don't exist, so showing them would just lead
    visitors to clicks that 404. The read-only list + read-card GETs
    are preserved so the demo still surfaces "here are the accounts /
    rails / templates / chains in this L2".
    """
    h = _make_handlers(cache, demo_mode=demo_mode)
    # ``/new`` MUST be declared before ``/{entity_id}`` so Starlette's
    # path matcher doesn't treat the literal "new" as an entity_id.
    # In demo-mode the /new GET is stripped — list + read-card are the
    # only routes that mount.
    routes: list[Route] = [
        Route(
            "/l2_shape/{kind}/", h["list_view"], methods=["GET"],
        ),
    ]
    if not demo_mode:
        routes.extend([
            Route(
                "/l2_shape/{kind}/", h["create"], methods=["POST"],
                name="l2_shape_create",
            ),
            Route(
                "/l2_shape/{kind}/new", h["new_form"], methods=["GET"],
                name="l2_shape_new_form",
            ),
        ])
    routes.append(
        Route(
            "/l2_shape/{kind}/{entity_id}", h["read_card"],
            methods=["GET"], name="l2_shape_read",
        ),
    )
    if not demo_mode:
        routes.extend([
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
        ])
    return routes
