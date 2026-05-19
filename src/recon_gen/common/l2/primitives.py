"""LAYER 2 institutional-model primitives, typed 1:1 against ``SPEC.md``.

This module is the single source of truth for what an L2 instance contains
in memory. The YAML loader (M.1.2) deserializes into these types; the
validator (M.1.3) enforces the SPEC's load-time rules on top; the SQL
emitter (M.1.4) walks them; downstream apps (M.2-M.6) consume them.

Notation matches SPEC: every dataclass mirrors a SPEC primitive's tuple
shape exactly, with PascalCase types + snake_case field names. Frozen +
slotted to prevent surprise mutation and typo'd attribute access.

Per F2 (M.0.13 iteration gate): ``Rail`` is a discriminated union of
``TwoLegRail`` / ``SingleLegRail`` — pyright catches "leg_role on a
two-leg rail" at the construction site instead of at validation time.
The aggregating-rail flags (``aggregating`` / ``bundles_activity`` /
``cadence``) live as optional fields on either shape, since the SPEC
allows aggregating rails to be one-leg or two-leg.

Per F4: Money values are ``Decimal``; the YAML loader (M.1.2) is
responsible for the ``Decimal(str(value))`` coercion that dodges YAML
float precision.

Z.C (2026-05-15) — the legacy ``L2Instance.instance`` field has been
dropped. The DB-table prefix (formerly enforced via SPEC F5's
``^[a-z][a-z0-9_]*$``/30-char cap on the ``instance:`` YAML key) now
lives on the cfg as ``cfg.db_table_prefix``; the same regex/cap is
enforced by ``common/config.py``'s loader at cfg-load time. The
QS-resource-ID prefix lives as ``cfg.deployment_name`` (replaces the
former ``cfg.resource_prefix`` + ``cfg.l2_instance_prefix`` pair).

Per F1 + SPEC's load-time validation list: every Role referenced by a
Rail or AccountTemplate MUST resolve to either a declared ``Account``
or an ``AccountTemplate``. This module declares the field types; the
validator (M.1.3) walks the resolution graph.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import Literal, NewType, TypeAlias

from recon_gen.common.persona import DemoPersona

from .theme import ThemePreset


# -- Value types --------------------------------------------------------------


# An identifier — used for InstancePrefix, Role names, Rail names,
# TransferTemplate names, Account IDs, MetadataKey names, etc. The runtime
# type is ``str``; ``NewType`` gives pyright the hint that mixing identifier
# kinds (e.g. passing a Role where a Rail name is expected) is suspicious
# at the type-check site.
Identifier = NewType("Identifier", str)

# A human-readable label — for Account.name. Distinct from Identifier in
# the SPEC's Notation section (Identifier is opaque + stable; Name is
# display-only and not load-bearing for any constraint).
Name = NewType("Name", str)

# Money — Decimal to 2dp in the system's single Currency. The loader
# (M.1.2) coerces YAML numerics via ``Decimal(str(value))``.
Money: TypeAlias = Decimal

# L1's Account.Scope discriminates whether reconciliation tracks the
# account's balance (Internal) or treats it as a counterparty (External).
Scope: TypeAlias = Literal["internal", "external"]

# L1's Transaction.Origin — open enum on L1, but L2 declares each Rail's
# Origin per-instance. The SPEC pins {InternalInitiated, ExternalForcePosted}
# as the v1 set; integrators may extend.
Origin: TypeAlias = str

# Every Transaction leg's direction. ``Variable`` is the closing-leg sentinel
# whose amount + direction are both determined by a containing
# TransferTemplate's ExpectedNet at posting time.
LegDirection: TypeAlias = Literal["Debit", "Credit", "Variable"]

# A Rail's name. Distinct NewType from Identifier so pyright catches
# kind-swap bugs at the call site (passing a Role / TransferTemplateName
# where a RailName is expected). Z.B (2026-05-15) collapsed the legacy
# `Rail.transfer_type` field into the rail name itself — the rail name
# IS the "what kind of money movement" identifier across L1 + L2.
RailName = NewType("RailName", str)

# A Rail's TransferType extends L1's open enum (``Sale`` is the L1 default
# and need not be redeclared). Z.B (2026-05-15) collapsed `Rail.transfer_type`
# and `TransferTemplate.transfer_type` into rail / template names — this
# alias is retained for legacy comments + cross-module imports that the
# follow-on Z.B.5 sweep will untangle. New code SHOULD reach for `RailName`
# instead.
TransferType: TypeAlias = str

# A SPEC-vocabulary expression for a TransferTemplate's Completion derivation.
# The validator (M.1.3) enforces this against the v1 vocabulary table:
# {business_day_end, business_day_end+Nd, month_end, metadata.<key>}.
CompletionExpression: TypeAlias = str

# A SPEC-vocabulary expression for an aggregating rail's firing cadence.
# The validator (M.1.3) enforces this against the v1 vocabulary table:
# {intraday-Nh, daily-eod, daily-bod, weekly-<weekday>, monthly-eom,
#  monthly-bom, monthly-<day>}.
CadenceExpression: TypeAlias = str

# A Rail's source/destination/leg role accepts either a single Role name
# or a union of Role names ("any of these is admissible"). Always stored
# as a tuple — single-role becomes a 1-tuple; the loader normalizes.
RoleExpression: TypeAlias = tuple[Identifier, ...]

# An item in an aggregating rail's BundlesActivity. Per SPEC: a
# TransferType matches every Transfer of that type; a RailName /
# TransferTemplateName matches Transfers produced by that specific
# rail/template. Both kinds are strings; the validator resolves which.
BundlesActivityRef: TypeAlias = Identifier

# A span of time — used for aging windows (max_pending_age,
# max_unbundled_age). Loader parses ISO 8601 duration literals
# (``PT24H``, ``PT4H``, ``P1D``, etc.) into ``datetime.timedelta``.
Duration: TypeAlias = timedelta

# Per SPEC's "Higher-Entry rows" section: every row that supersedes a
# prior row of the same logical key MUST set ``Supersedes`` to one of
# these v1 categories. Storage column is open enum (no DB CHECK) so
# integrators may extend; the loader pins the v1 set at load time.
SupersedeReason: TypeAlias = Literal[
    "Inflight", "BundleAssignment", "TechnicalCorrection",
]

# AB.1 (2026-05-19): the *cap-watch perspective* of a LimitSchedule —
# whether the cap watches money leaving the parent's children
# (``Outbound``, the classic per-rail send cap) or arriving at them
# (``Inbound``, the typical AML/structuring inbound-cap pattern). Stays
# distinct from ``LegDirection`` (Debit/Credit/Variable, the
# accounting perspective on a single leg): a LimitSchedule with
# ``direction="Outbound"`` aggregates ``Debit``-amount legs from the
# child's view, ``"Inbound"`` aggregates ``Credit``-amount legs. The
# AB.1.0 lock keeps the two enums separate so the validator can
# enforce "watch direction" without leaking into the leg-amount
# vocabulary.
LimitDirection: TypeAlias = Literal["Outbound", "Inbound"]


# -- Account dimension --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Account:
    """A 1-of-1 account that exists exactly once in the institution.

    Per SPEC: singletons that Rails reference by Role; the Role is
    technically optional but in practice required for any Account a Rail
    touches (per F1, enforced by the validator at load time).

    ``description`` is free-form prose (markdown OK) read by handbook +
    training render templates per the SPEC's "Description fields" rule.
    Optional at the type level but SHOULD be filled.
    """

    id: Identifier
    scope: Scope
    name: Name | None = None
    role: Identifier | None = None
    parent_role: Identifier | None = None
    expected_eod_balance: Money | None = None
    description: str | None = None


@dataclass(frozen=True, slots=True)
class AccountTemplate:
    """A class of accounts that exists in many instances at runtime.

    Per SPEC: declares the SHAPE; the specific account instance for a
    given posting is selected at posting time (typically from
    ``Transaction.Metadata``). ``parent_role`` MUST resolve to a
    singleton ``Account`` (never another ``AccountTemplate``) — enforced
    by the validator at load time per the SPEC's "singleton parent only"
    constraint.

    ``instance_id_template`` + ``instance_name_template`` (M.4.2b) —
    optional Python str.format() templates the demo seed's
    ``_materialize_instances`` uses when synthesizing per-template
    instances. Both default to ``None``; the seed falls back to the
    legacy synthetic patterns (``"cust-{n:03d}"`` for id,
    ``"Customer {n}"`` for name) so existing L2 fixtures don't drift.
    Integrators opt in via YAML to control the persona's per-template
    naming, e.g.:

        instance_id_template: "cust-{n:03d}-bigfoot"
        instance_name_template: "Bigfoot-{n}"

    Both templates support the placeholders ``{role}`` (the template's
    ``role`` field) and ``{n}`` (1-indexed instance number). Loader
    rejects format strings that reference any other placeholder.
    """

    role: Identifier
    scope: Scope
    parent_role: Identifier | None = None
    expected_eod_balance: Money | None = None
    description: str | None = None
    instance_id_template: str | None = None
    instance_name_template: str | None = None


# -- Rails (discriminated union per F2) --------------------------------------


@dataclass(frozen=True, slots=True)
class TwoLegRail:
    """A Rail that produces two Transaction legs (debit + credit) per firing.

    When fired as a standalone Transfer, ``expected_net`` MUST be set
    (typically ``0``); L1 Conservation enforces ``Σ legs = expected_net``.
    When the rail is a leg-pattern of a TransferTemplate, ``expected_net``
    MUST be unset — the template owns the bundle's ExpectedNet. Per F3
    this is a cross-entity validation rule (the validator's pass 2).

    Per-leg Origin: ``origin`` shorthands "both legs share this Origin";
    ``source_origin`` / ``destination_origin`` override per leg when the
    legs differ (e.g., the leg touching an external counterparty is
    ``ExternalForcePosted`` while the internal counterpart is
    ``InternalInitiated``). The validator (rule O1) checks every leg
    resolves to an Origin under the SPEC's resolution table.

    PostedRequirements / aging: ``posted_requirements`` adds Rail-specific
    fields beyond the auto-derived TransferKey + chain-Required-true
    parent_transfer_id (see ``derived.posted_requirements_for``);
    ``max_pending_age`` + ``max_unbundled_age`` are aging-watch durations.
    """

    name: Identifier
    metadata_keys: tuple[Identifier, ...]
    source_role: RoleExpression
    destination_role: RoleExpression
    # Origin resolution (validator rule O1). At least one path MUST cover
    # both legs — either rail-level ``origin`` alone, both per-leg
    # overrides, or one override + rail-level ``origin``.
    origin: Origin | None = None
    source_origin: Origin | None = None
    destination_origin: Origin | None = None
    expected_net: Money | None = None
    # Integrator-declared posting requirements; see derived.py for the
    # full computed set (unions in TransferKey + chain-required fields).
    posted_requirements: tuple[Identifier, ...] = field(default_factory=tuple)
    # Aging watches — surface as exception views in dashboards.
    max_pending_age: Duration | None = None
    max_unbundled_age: Duration | None = None
    # Aggregating-rail flags. Per SPEC, aggregating rails MAY be two-leg.
    aggregating: bool = False
    bundles_activity: tuple[BundlesActivityRef, ...] = field(default_factory=tuple)
    cadence: CadenceExpression | None = None
    # Free-form prose for handbook + training render templates per
    # the SPEC's "Description fields" rule. Optional; SHOULD be filled.
    description: str | None = None
    # Per-key example metadata values (M.4.2b). When set, the demo seed's
    # broad-mode RailFiringPlant emits values from the per-key list
    # (cycling through if firings exceed list length) — the L2 Flow
    # Tracing metadata cascade reads realistic per-persona values
    # instead of the synthetic ``<rail>-firing-<seq>`` fallback.
    # Validator R13: every dict key MUST be in ``metadata_keys``.
    # Stored as a tuple-of-tuples to keep the dataclass frozen + hashable.
    metadata_value_examples: tuple[tuple[Identifier, tuple[str, ...]], ...] = (
        field(default_factory=tuple)
    )


@dataclass(frozen=True, slots=True)
class SingleLegRail:
    """A Rail that produces one Transaction leg per firing.

    Per SPEC: single-leg rails MUST be reconciled by EITHER a
    ``TransferTemplate`` whose ``leg_rails`` includes this rail OR an
    aggregating rail whose ``bundles_activity`` includes this rail's
    ``name``. A single-leg rail without either reconciliation path is
    a configuration error (validator catches at load).

    ``leg_direction = Variable`` means the leg's amount AND direction are
    determined at posting time by a containing TransferTemplate's
    ExpectedNet closure requirement. Each TransferTemplate MUST contain
    at most one Variable-direction leg.

    Per-leg Origin overrides (``source_origin`` / ``destination_origin``)
    are deliberately absent here — they only make sense on a 2-leg rail.
    The loader rejects them at load if they appear in YAML for a
    single-leg rail (hard error, per the M.1a design call).
    """

    name: Identifier
    metadata_keys: tuple[Identifier, ...]
    leg_role: RoleExpression
    leg_direction: LegDirection
    # Required for single-leg rails (every leg resolves to an Origin). The
    # default-None lets the dataclass field-order rule work; the loader
    # enforces presence at load time.
    origin: Origin | None = None
    posted_requirements: tuple[Identifier, ...] = field(default_factory=tuple)
    max_pending_age: Duration | None = None
    max_unbundled_age: Duration | None = None
    # Aggregating-rail flags. Per SPEC, single-leg aggregating rails are
    # permitted (e.g. a single-leg sweep that lands in an external
    # counterparty).
    aggregating: bool = False
    bundles_activity: tuple[BundlesActivityRef, ...] = field(default_factory=tuple)
    cadence: CadenceExpression | None = None
    # Free-form prose for handbook + training render templates per
    # the SPEC's "Description fields" rule. Optional; SHOULD be filled.
    description: str | None = None
    # Per-key example metadata values (M.4.2b) — see TwoLegRail's field
    # for full semantics. Same shape, same validator rule, same fallback.
    metadata_value_examples: tuple[tuple[Identifier, tuple[str, ...]], ...] = (
        field(default_factory=tuple)
    )


Rail: TypeAlias = TwoLegRail | SingleLegRail


# -- Transfer Templates ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TransferTemplate:
    """A multi-leg shared Transfer that bundles many Rail firings.

    Per SPEC: every firing of a ``leg_rails`` rail with the same
    ``transfer_key`` Metadata values posts to the same shared Transfer.
    L1 Conservation flags the Transfer if its legs don't sum to
    ``expected_net``; L1 Timeliness flags any leg that posts after the
    derived ``Transfer.Completion``.

    A Rail listed in ``leg_rails`` MUST NOT also fire standalone
    Transfers — its firings always join the shared Transfer matching the
    ``transfer_key`` values.

    ``leg_rail_xor_groups`` (AB.3) declares mutually-exclusive subsets
    of ``leg_rails`` — exactly one member of each inner tuple SHOULD
    fire per Transfer. Empty default keeps every pre-AB.3 template byte-
    equivalent; the structural validator (C1a-d) enforces members ⊆
    leg_rails, members are Variable-direction, no overlap between
    groups, ≥2 members per group. Runtime "exactly one fires" check
    lives in the ``_xor_group_violation`` matview (AB.3.3).
    """

    name: Identifier
    expected_net: Money
    transfer_key: tuple[Identifier, ...]
    completion: CompletionExpression
    leg_rails: tuple[Identifier, ...]
    leg_rail_xor_groups: tuple[tuple[Identifier, ...], ...] = ()
    description: str | None = None


# -- Chains ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Chain:
    """A firing rule: one parent + one list of candidate children.

    Per SPEC: list cardinality carries the entire firing semantic —
    **singleton ⇒ required** (the child SHOULD fire; missing surfaces
    as an orphan exception); **multi ⇒ XOR** (exactly one of the listed
    children SHOULD fire per parent instance). The legacy
    ``required`` / ``xor_group`` flags collapse into ``len(children)``
    (Z.A — locked 2026-05-13).

    Aggregating rails MUST NOT appear in ``children`` (they don't have
    per-Transfer parents — they sweep on cadence). Validator enforces.
    """

    parent: Identifier
    children: tuple[Identifier, ...]
    description: str | None = None


# -- Limit Schedules ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LimitSchedule:
    """A daily cap on per-direction flow per (parent role, rail, direction).

    Per SPEC: time-invariant in v1. The library projects each entry into
    the relevant ``StoredBalance.Limits`` map; L1's Limit Breach
    invariant evaluates per child individually (the cap is per-child,
    not aggregated across siblings of the parent). Z.B (2026-05-15)
    renamed ``transfer_type`` → ``rail`` — the field now references a
    Rail name directly, eliminating the templated-leg footgun where a
    `LimitSchedule on transfer_type=<leg_rail_type>` failed to fire on
    transactions tagged with the *template*'s transfer_type instead.
    AB.1 (2026-05-19) added ``direction``: a single ``(parent_role,
    rail)`` may now carry *two* schedules — one ``Outbound`` (classic
    per-rail send cap) and one ``Inbound`` (AML / structuring threshold
    on inbound volume). The validator broadens uniqueness from
    ``(parent_role, rail)`` to ``(parent_role, rail, direction)``.
    Default ``Outbound`` keeps every pre-AB.1 YAML byte-equivalent.
    """

    parent_role: Identifier
    rail: RailName
    cap: Money
    direction: LimitDirection = "Outbound"
    description: str | None = None


# -- Top-level instance ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class L2Instance:
    """A loaded + parsed L2 institutional model.

    Z.C (2026-05-15) — the legacy ``instance`` field has been dropped.
    The DB-table prefix lives on the cfg as ``cfg.db_table_prefix``;
    the QS-resource-ID prefix lives as ``cfg.deployment_name``. Each
    L2 YAML is pure topology + persona + theme; the cfg yaml carries
    the deployment-specific identifiers.
    """

    accounts: tuple[Account, ...]
    account_templates: tuple[AccountTemplate, ...]
    rails: tuple[Rail, ...]
    transfer_templates: tuple[TransferTemplate, ...]
    chains: tuple[Chain, ...]
    limit_schedules: tuple[LimitSchedule, ...]
    # Top-level institution-level prose. Read by handbook templates as
    # the "what is this institution" introductory paragraph.
    description: str | None = None
    # Optional per-role business-day offset in hours (M.4.4.14). When
    # set, an account whose role appears in this map gets its emitted
    # ``daily_balances.business_day_start`` shifted by the offset
    # (e.g., 17 → "5pm"). ``business_day_end`` shifts the same amount
    # so the 24-hour window contract holds. Roles not in the map
    # default to midnight-aligned (00:00 → 00:00 next day) — preserves
    # the deterministic baseline shape that the locked SQL files
    # under ``tests/data/_locked_seeds/`` pin (X.1.k). Used by the
    # fuzz matrix to exercise any future L1 view that depends on
    # per-role business-day boundaries differing.
    role_business_day_offsets: dict[str, int] | None = None
    # Optional brand theme for this institution (N.1.b). When set, the
    # apps consume colors from here instead of from the per-CLI
    # ``--theme-preset`` flag — one theme per L2 instance, declared
    # alongside the institution's primitives. ``None`` means "fall back
    # to the registry default" (``common/theme.py::DEFAULT_PRESET``).
    theme: ThemePreset | None = None
    # Optional ``persona:`` block (Q.5.e) — per-institution flavor
    # strings for handbook templating: institution name + acronym,
    # upstream stakeholders, GL account labels, merchant names, free-form
    # flavor literals. ``None`` means "no flavor declared"; handbook
    # templates render neutral prose derived from L2 primitive fields
    # instead.
    persona: DemoPersona | None = None
