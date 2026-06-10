"""BU.1 — Plant registry for the Trainer surface.

The single source of truth for every violation kind the Trainer
surfaces (per BU.0 Lock 7). The registry is a tuple of
``PlantKindEntry`` records; the Trainer UI + the plant invocation
+ the tour iframe + the parameterized e2e contract all data-drive
off the entry.

Per BU.0 Lock 8 — the registry is a **thin index** over typed
violation-class sections (``InvariantSection`` / ``L2FTExceptionSection``
/ ``L2TriageGapSection``). Display strings (title / short statement
/ remediation) live on the section. The entry just carries:

- ``kind`` — canonical machine name; matches the section's ``kind``
  (with optional ``section_kind`` override for slug-mismatch edge
  cases per BU.0 round-4 Notes).
- ``category`` — discriminator for which typed-section lookup to use
  via ``resolve_section`` (Lock 8).
- ``family`` — accordion grouping on the landing page.
- ``plant_function`` — callable that returns the SQL string applied
  to the demo DB.
- ``primitives`` — typed form-field schema operator fills in.
- ``tour_destination`` — iframe URL + sheet anchor for the Tour page.
- ``dashboard_check`` — parameterized e2e contract (BU.0 Lock 9).

Per BU.1 (vertical slice) — this module ships with ONE entry
(``phantom_rail``) so the rendering + tour + e2e patterns can be
validated end-to-end before scaling. BU.2b populates the remaining
20 entries; BU.3.x adds the 5 needs-build plants.

Adding a future violation kind = one row here + one markdown section
in the existing handbook parser + zero new UI / test files.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Final, Literal

# BV.3.3.c.bug2 — hoisted from per-invoke local-import blocks. Originally
# every ``_invoke_*_plant`` body did its own ``from recon_gen.common.l2.seed
# import ...`` to defer the import cost. That deferral meant a stale long-
# running Studio process (whose ``sys.modules['recon_gen.common.l2.seed']``
# pre-dated a new dataclass like ``LedgerDriftPlant``) only blew up on the
# operator's first click — `ImportError: cannot import name
# 'LedgerDriftPlant' from 'recon_gen.common.l2.seed'` (see
# docs/audits/_archive/bv_cold_read_v3.md §P1.1). Hoisting to module-level
# converts that into a process-boot ImportError instead — caught by the
# anti-drift smoke test (``test_bv33c_ledgerdrift_import_smoke``) AND
# loudly visible on `recon-gen studio` startup, so the operator never sees
# the tooltip-buried failure mode again. ``seed`` has no circular dep on
# ``plant_registry`` (only ``v_overlay`` imports us, behind TYPE_CHECKING).
from recon_gen.common.l2.seed import (
    ChainParentDisagreementPlant,
    DriftPlant,
    ExpectedEodBalancePlant,
    FanInChainExtraParentPlant,
    FanInChainMissingParentPlant,
    InboundCapBreachPlant,
    LedgerDriftPlant,
    LimitBreachPlant,
    MultiXorMissedPlant,
    MultiXorOverlapPlant,
    OverdraftPlant,
    ScenarioPlant,
    StuckPendingPlant,
    StuckUnbundledPlant,
    SupersessionPlant,
    TemplateInstance,
    XorVariantMissedFiringPlant,
    XorVariantOverlapPlant,
    emit_seed,
)

if TYPE_CHECKING:
    from recon_gen.common.l2.primitives import AccountTemplate, L2Instance


class PlantCategory(str, Enum):
    """Per BU.0 §0.5 matrix — drives ``resolve_section`` dispatch."""

    L1_INVARIANT = "l1_invariant"
    L2_TRIAGE = "l2_triage"
    L2_COVERAGE = "l2_coverage"
    L2FT_HYGIENE = "l2ft_hygiene"


# -- Form primitive types ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class PrimitiveStringField:
    """Free-text string input. Renders as ``<input type="text">``."""

    name: str
    label: str
    help_text: str
    default: str


@dataclass(frozen=True, slots=True)
class PrimitiveIntField:
    """Integer input. Renders as ``<input type="number">`` with
    optional min/max attrs."""

    name: str
    label: str
    help_text: str
    default: int
    min_value: int | None = None
    max_value: int | None = None


# Union of all primitive field shapes.
PrimitiveField = PrimitiveStringField | PrimitiveIntField


# -- Tour destination -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TourDestination:
    """Where the Tour page iframes into.

    ``primary_url`` is the full URL (no query-param expansion in the
    slice; BU.2+ may extend with `{prefix}` / `{form_<field>}` template
    substitution if needed).
    """

    primary_url: str
    secondary_links: tuple[tuple[str, str], ...] = ()


# -- Dashboard-check contract (BU.0 Lock 9) ---------------------------------


@dataclass(frozen=True, slots=True)
class DashboardCheck:
    """The parameterized e2e contract: after the plant fires + the
    matview refresh runs, what should be observable on the dashboard?

    Two shapes:

    - ``matview_name`` + ``min_row_count`` — for kinds backed by a
      precomputed matview (L1 + L2FT Hygiene). The e2e queries the
      matview directly + asserts row count.
    - ``url_path`` + ``expect_text_contains`` — for kinds whose
      "matview" is computed at-query-time by the route (L2 Triage —
      `/etl/triage` renders the gap card from `detect_gaps()` against
      the live tables, no stored matview).

    Exactly one shape is set per entry; the parameterized e2e
    branches on which is present.
    """

    matview_name: str | None = None
    min_row_count: int = 1
    url_path: str | None = None
    expect_text_contains: str | None = None


# -- Plant invariant contract (BV.3.3 snapshot) -----------------------------
#
# Every registry entry declares which surface its plant_function is
# allowed to mutate. The contract gates the snapshot/restore pattern:
# the BV.3.3 trainer-dogfood test snapshots the v-overlay once, runs all
# plants against the snapshot, restores per-plant; that lifecycle only
# holds if every plant_function actually constrains its writes to the
# v overlay tables. Anything that touches DDL, the base prefix, or any
# out-of-overlay object would invalidate the snapshot.
#
# Audit finding (BV.3.3): every plant_function in this module emits
# only ``INSERT INTO {prefix}_<table>`` / ``DELETE FROM {prefix}_<table>``
# / ``UPDATE {prefix}_<table>`` SQL, where ``{prefix}`` is the
# caller-supplied table prefix. The canonical caller (``apply_plants``
# in ``v_overlay.py``) passes ``v_overlay_prefix(base)``, so the writes
# land on ``<base>_v_*`` overlay tables. No plant emits DDL; no plant
# hardcodes ``<base>_*`` references.
#
# The contract is caller-blind by design: the plant emits SQL relative
# to the prefix it gets, and the SNAPSHOT/RESTORE caller is responsible
# for handing the v-overlay prefix in. The legacy
# ``/training/plant/<kind>/apply`` route (``_studio_routes.py``) still
# passes the base prefix directly — out of scope for this contract gate,
# tracked separately.

# Allowed values for ``PlantContract.mutates``. Single member today;
# extend only when an audited plant legitimately needs a wider surface
# (and the BV.3.3 snapshot lifecycle is updated to handle it).
PlantMutationSurface = Literal["v_overlay_only"]

_ALLOWED_MUTATION_SURFACES: Final[frozenset[str]] = frozenset({"v_overlay_only"})


@dataclass(frozen=True, slots=True)
class PlantContract:
    """Invariant declared by every PLANT_REGISTRY entry.

    Carries the per-plant guarantees the BV.3.3 snapshot/restore
    lifecycle relies on. Construction-time validation rejects any value
    not in ``_ALLOWED_MUTATION_SURFACES`` so a typo in a future entry
    fails at import time, not at trainer apply time.

    Fields:
      mutates: Which table surface the plant_function writes to.
        ``'v_overlay_only'`` is the only currently-allowed value —
        the plant emits SQL exclusively against ``{prefix}_*`` tables
        (transactions / daily_balances / config_kv / matviews) and the
        ``apply_plants`` caller threads in the v-overlay prefix.
    """

    mutates: PlantMutationSurface = "v_overlay_only"

    def __post_init__(self) -> None:
        if self.mutates not in _ALLOWED_MUTATION_SURFACES:
            raise ValueError(
                f"PlantContract.mutates must be one of "
                f"{sorted(_ALLOWED_MUTATION_SURFACES)}; got {self.mutates!r}."
            )


# Default contract reused across every BV.3.3-audited registry entry.
# Module-level singleton (rather than per-entry construction) so the
# anti-drift unit test can identity-compare against this constant.
DEFAULT_PLANT_CONTRACT: Final[PlantContract] = PlantContract(
    mutates="v_overlay_only",
)


# -- The registry entry -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlantKindEntry:
    """Thin index per BU.0 Lock 8 — display strings live on the
    typed violation section referenced by ``kind`` (or
    ``section_kind`` when there's a slug mismatch with the handbook
    parser's auto-derived key).
    """

    kind: str
    category: PlantCategory
    family: str
    plant_function: Callable[..., str]
    primitives: tuple[PrimitiveField, ...]
    tour_destination: TourDestination
    dashboard_check: DashboardCheck
    # Optional override when the canonical kind doesn't match the
    # typed-section's auto-derived slug (per BU.0 round-4 Notes,
    # e.g. `dead_metadata` vs `dead_metadata_declarations`).
    section_kind: str | None = None
    # Per BU.0 Lock 8 — when multiple registry rows share a
    # section_kind (e.g. limit_breach_outbound + limit_breach_inbound
    # both point at InvariantSection["limit_breach"]), the renderer
    # appends this qualifier to the title so the operator can tell
    # the sub-kinds apart. Empty for kinds with a unique section_kind.
    kind_qualifier: str | None = None
    # BV.3.3 — typed mutation-surface invariant. Defaults to
    # ``DEFAULT_PLANT_CONTRACT`` (mutates='v_overlay_only') because every
    # current registry entry audited as v-overlay-only. Override only
    # with operator review.
    contract: PlantContract = DEFAULT_PLANT_CONTRACT


# -- Plant-function adapters ------------------------------------------------


def _invoke_phantom_rail_plant(
    *,
    prefix: str,
    dialect: object,  # typing-smell: ignore[explicit-any]: Dialect — avoid circular import w/ common.sql.dialect at module load
    anchor: datetime,
    count: int,
    rail_name: str,
    instance: object = None,  # BU.2b — adapters that don't need the instance still accept it for uniform signature
) -> str:
    """Adapter from registry primitives → ``add_phantom_rail_gap_rows``.

    Keeps the registry decoupled from ``demo_etl_gaps``'s exact
    signature so the BU.2b populate step can swap adapters per kind
    without touching the renderer.
    """
    del instance  # unused — phantom_rail doesn't need the L2 declaration
    from recon_gen.common.l2.demo_etl_gaps import add_phantom_rail_gap_rows  # noqa: PLC0415
    from recon_gen.common.sql.dialect import Dialect  # noqa: PLC0415

    return add_phantom_rail_gap_rows(
        prefix=prefix,
        dialect=dialect if isinstance(dialect, Dialect) else Dialect.DUCKDB,
        anchor=anchor,
        count=count,
        rail_name=rail_name,
    )


def _invoke_phantom_template_plant(
    *,
    prefix: str,
    dialect: object,
    anchor: datetime,
    count: int,
    instance: object = None,
) -> str:
    """Adapter for ``add_phantom_template_gap_rows`` — INSERTs ``count``
    rows whose ``template_name`` doesn't resolve in the L2 declaration."""
    del instance
    from recon_gen.common.l2.demo_etl_gaps import add_phantom_template_gap_rows  # noqa: PLC0415
    from recon_gen.common.sql.dialect import Dialect  # noqa: PLC0415

    return add_phantom_template_gap_rows(
        prefix=prefix,
        dialect=dialect if isinstance(dialect, Dialect) else Dialect.DUCKDB,
        anchor=anchor,
        count=count,
    )


def _invoke_missing_metadata_plant(
    *,
    prefix: str,
    dialect: object,
    anchor: datetime,
    instance: object = None,
) -> str:
    """Adapter for ``add_missing_metadata_gap_rows`` — plants one row
    whose template resolves but whose metadata omits the template's
    required transfer_key. Needs the L2 instance to pick a target
    template that declares one."""
    from recon_gen.common.l2.demo_etl_gaps import add_missing_metadata_gap_rows  # noqa: PLC0415
    from recon_gen.common.l2.primitives import L2Instance  # noqa: PLC0415
    from recon_gen.common.sql.dialect import Dialect  # noqa: PLC0415

    if not isinstance(instance, L2Instance):
        raise ValueError(
            "missing_metadata_key plant needs an L2Instance to "
            "pick a template that declares a required transfer_key."
        )
    return add_missing_metadata_gap_rows(
        instance,
        prefix=prefix,
        dialect=dialect if isinstance(dialect, Dialect) else Dialect.DUCKDB,
        anchor=anchor,
    )


def _invoke_uncovered_rail_plant(
    *,
    prefix: str,
    dialect: object,
    anchor: datetime,
    instance: object = None,
) -> str:
    """Adapter for ``add_uncovered_rail_gap_rows`` — DELETEs all
    transactions for one L2-declared rail (alphabetically-last,
    deterministic). The Coverage Rails panel then renders that rail
    as ✗ (declared but no rows)."""
    del anchor  # unused — DELETE statement, no posting time
    from recon_gen.common.l2.demo_etl_gaps import add_uncovered_rail_gap_rows  # noqa: PLC0415
    from recon_gen.common.sql.dialect import Dialect  # noqa: PLC0415

    inst = _require_instance(instance)
    return add_uncovered_rail_gap_rows(
        inst,
        prefix=prefix,
        dialect=dialect if isinstance(dialect, Dialect) else Dialect.DUCKDB,
    )


def _invoke_uncovered_template_plant(
    *,
    prefix: str,
    dialect: object,
    anchor: datetime,
    instance: object = None,
) -> str:
    """Adapter for ``add_uncovered_template_gap_rows`` — DELETEs all
    transactions for one L2-declared template (alphabetically-last,
    deterministic). The Coverage Templates panel renders that
    template as ✗."""
    del anchor
    from recon_gen.common.l2.demo_etl_gaps import (  # noqa: PLC0415
        add_uncovered_template_gap_rows,
    )
    from recon_gen.common.sql.dialect import Dialect  # noqa: PLC0415

    inst = _require_instance(instance)
    return add_uncovered_template_gap_rows(
        inst,
        prefix=prefix,
        dialect=dialect if isinstance(dialect, Dialect) else Dialect.DUCKDB,
    )


# -- L1 invariant plant adapters (BU.2b stage 2) ----------------------------
#
# Pattern: each L1 adapter takes the same ``prefix`` / ``dialect`` /
# ``anchor`` / ``instance`` quartet the L2 Triage adapters take, plus
# form-derived primitives (typically ``days_ago`` + a money / count
# knob). The adapter:
#
#   1. Requires ``instance`` (an L2Instance) — L1 plants resolve
#      account_id / rail_name / counter_account_id from L2 via the
#      auto_scenario pickers. Raises ValueError when the picker
#      returns None (the L2 can't satisfy this plant kind — e.g. no
#      inbound 2-leg rail declared for drift).
#   2. Constructs the appropriate ScenarioPlant member (DriftPlant /
#      OverdraftPlant / etc.) populated with picker output + the
#      operator's tunables.
#   3. Wraps in a ``ScenarioPlant(template_instances=(cust1, cust2), ...)``
#      so the seed emitter has the materialized customer accounts the
#      plant references.
#   4. Calls ``emit_seed`` and returns the SQL string.
#
# Why pickers, not operator picks: the operator shouldn't have to
# decode "which external Account satisfies my drift rail's source
# role?" — that's a deterministic walk over the L2 declaration. We
# surface only the scenario-shaping knobs (days_ago, money amount).
#
# Why a fresh ``_materialize_instances`` per adapter: each plant
# needs at least one materialized customer account_id; cust1/cust2
# are deterministic from the L2 template's instance_id_template so
# the same call produces the same ids every time.


def _require_instance(instance: object) -> "L2Instance":
    """Narrow ``object`` → ``L2Instance`` with a loud error on miss.

    Adapters take ``instance: object`` for uniform-signature symmetry
    with the L2 Triage adapters that don't need it; this helper does
    the narrowing in one place + raises a Trainer-readable error when
    the route forgot to thread the instance through."""
    from recon_gen.common.l2.primitives import L2Instance  # noqa: PLC0415

    if not isinstance(instance, L2Instance):
        raise ValueError(
            "L1 plant adapter requires an L2Instance to pick a "
            "template + materialize customer accounts."
        )
    return instance


def _materialize_customers(
    instance: "L2Instance",
) -> tuple["AccountTemplate", "TemplateInstance", "TemplateInstance"]:
    """Pick the first AccountTemplate + materialize 2 customer instances.

    Returns ``(template, cust1, cust2)``. Raises ValueError when the L2
    declares no AccountTemplate — every L1 plant needs at least one
    customer-side account_id and we'd rather loud-fail at the adapter
    boundary than emit a half-populated ScenarioPlant.
    """
    from recon_gen.common.l2.auto_scenario import (  # noqa: PLC0415
        _materialize_instances, _pick_template,
    )

    template = _pick_template(instance)
    if template is None:
        raise ValueError(
            "L1 plant adapter needs an AccountTemplate in the L2 "
            "instance; none declared."
        )
    cust1, cust2 = _materialize_instances(template)
    return (template, cust1, cust2)


def _emit_scenario(
    instance: "L2Instance",
    scenario: "ScenarioPlant",
    *,
    prefix: str,
    dialect: object,
) -> str:
    """Call ``emit_seed`` with the right dialect coercion."""
    from recon_gen.common.sql.dialect import Dialect  # noqa: PLC0415

    return emit_seed(
        instance,
        scenario,
        prefix=prefix,
        dialect=dialect if isinstance(dialect, Dialect) else Dialect.DUCKDB,
    )


def _invoke_drift_plant(
    *,
    prefix: str,
    dialect: object,
    anchor: datetime,
    days_ago: int,
    delta_money: str,
    instance: object = None,
) -> str:
    """Adapter for ``DriftPlant`` — plants a (customer, day) cell where
    stored balance disagrees with computed balance by ``delta_money``.

    Picker resolves the inbound 2-leg rail + its external counter Account
    from the L2; the operator picks ``days_ago`` + ``delta_money`` only.
    """
    from recon_gen.common.l2.auto_scenario import (  # noqa: PLC0415
        _pick_external_counter_for_rail,
        _pick_inbound_2leg_rail,
    )

    instance = _require_instance(instance)
    template, cust1, cust2 = _materialize_customers(instance)
    template_role = template.role
    rail = _pick_inbound_2leg_rail(instance, template_role)
    if rail is None:
        raise ValueError(
            "drift plant: no 2-leg Rail with destination matching the "
            "template role declared in this L2."
        )
    counter = _pick_external_counter_for_rail(instance, rail)
    if counter is None:
        raise ValueError(
            f"drift plant: rail {rail.name!r} has no external Account "
            f"matching its source role."
        )
    scenario = ScenarioPlant(
        template_instances=(cust1, cust2),
        drift_plants=(
            DriftPlant(
                account_id=cust1.account_id,
                days_ago=days_ago,
                delta_money=Decimal(delta_money),
                rail_name=rail.name,
                counter_account_id=counter.id,
            ),
        ),
        today=anchor.date(),
    )
    return _emit_scenario(instance, scenario, prefix=prefix, dialect=dialect)


def _invoke_ledger_drift_plant(
    *,
    prefix: str,
    dialect: object,
    anchor: datetime,
    days_ago: int,
    delta_money: str,
    instance: object = None,
) -> str:
    """Adapter for the ``ledger_drift`` registry kind. BV.3.3.c.bug1 —
    authors a real LedgerDriftPlant (was: delegated to drift adapter,
    which planted on a LEAF and only fired ledger_drift as a
    side-effect that didn't reliably surface in the matview).

    The new shape plants a synthetic parent+child pair under a unique
    parent_role. Operator picks ``days_ago`` + ``delta_money``; the
    spine generator handles the rest.
    """
    instance = _require_instance(instance)
    _, cust1, cust2 = _materialize_customers(instance)
    scenario = ScenarioPlant(
        template_instances=(cust1, cust2),
        ledger_drift_plants=(
            LedgerDriftPlant(
                days_ago=days_ago,
                delta_money=Decimal(delta_money),
            ),
        ),
        today=anchor.date(),
    )
    return _emit_scenario(instance, scenario, prefix=prefix, dialect=dialect)


def _invoke_overdraft_plant(
    *,
    prefix: str,
    dialect: object,
    anchor: datetime,
    days_ago: int,
    money: str,
    instance: object = None,
) -> str:
    """Adapter for ``OverdraftPlant`` — plants a (customer, day) cell
    where stored balance goes negative. ``money`` MUST be negative
    (the SHOULD-constraint fires on balance < 0)."""
    instance = _require_instance(instance)
    _, cust1, cust2 = _materialize_customers(instance)
    money_dec = Decimal(money)
    if money_dec >= 0:
        raise ValueError(
            f"overdraft plant: money MUST be negative (got {money_dec}); "
            f"the Non-Negative Balance SHOULD-constraint only fires "
            f"when stored balance < 0."
        )
    scenario = ScenarioPlant(
        template_instances=(cust1, cust2),
        overdraft_plants=(
            OverdraftPlant(
                account_id=cust2.account_id,
                days_ago=days_ago,
                money=money_dec,
            ),
        ),
        today=anchor.date(),
    )
    return _emit_scenario(instance, scenario, prefix=prefix, dialect=dialect)


def _invoke_limit_breach_outbound_plant(
    *,
    prefix: str,
    dialect: object,
    anchor: datetime,
    days_ago: int,
    instance: object = None,
) -> str:
    """Adapter for ``LimitBreachPlant`` (outbound). Picker resolves the
    Outbound LimitSchedule + matching outbound 2-leg Rail + external
    counter Account; the breach amount is auto-derived from the picked
    schedule's cap via the same `_cap_breach_amount` helper the
    auto-scenario seed uses (cap × 1.5, clamped to rail range). This
    keeps the plant production-honest against any L2's cap values
    without an operator-tuned magic number.

    BV.3.2.a — pre-BV.3.2.a the operator picked `cap_breach_amount`
    with a default of $15,000, which underflowed the spec_example's
    $20,000 Inbound cap (and several Outbound caps). Removed the
    primitive so a) the trainer surfaces a real breach by construction
    and b) the same plant works against any L2 yaml the operator
    points at."""
    from recon_gen.common.l2.auto_scenario import (  # noqa: PLC0415
        _cap_breach_amount,
        _pick_breach_inputs,
    )

    instance = _require_instance(instance)
    template, cust1, cust2 = _materialize_customers(instance)
    picks = _pick_breach_inputs(instance, template.role)
    if picks is None:
        raise ValueError(
            "outbound limit_breach plant: no Outbound LimitSchedule "
            "whose rail matches an outbound 2-leg Rail with an "
            "external counterparty in this L2."
        )
    schedule, rail, counter = picks
    scenario = ScenarioPlant(
        template_instances=(cust1, cust2),
        limit_breach_plants=(
            LimitBreachPlant(
                account_id=cust1.account_id,
                days_ago=days_ago,
                rail_name=rail.name,
                amount=_cap_breach_amount(schedule.cap, rail),
                counter_account_id=counter.id,
            ),
        ),
        today=anchor.date(),
    )
    return _emit_scenario(instance, scenario, prefix=prefix, dialect=dialect)


def _invoke_limit_breach_inbound_plant(
    *,
    prefix: str,
    dialect: object,
    anchor: datetime,
    days_ago: int,
    instance: object = None,
) -> str:
    """Adapter for ``InboundCapBreachPlant`` (AB.1 inbound mirror).
    Picker resolves the Inbound LimitSchedule + matching inbound 2-leg
    Rail + external source Account; breach amount auto-derived from
    the picked schedule's cap (see outbound variant)."""
    from recon_gen.common.l2.auto_scenario import (  # noqa: PLC0415
        _cap_breach_amount,
        _pick_inbound_breach_inputs,
    )

    instance = _require_instance(instance)
    template, cust1, cust2 = _materialize_customers(instance)
    picks = _pick_inbound_breach_inputs(instance, template.role)
    if picks is None:
        raise ValueError(
            "inbound limit_breach plant: no Inbound LimitSchedule "
            "whose rail matches an inbound 2-leg Rail with an "
            "external counterparty in this L2."
        )
    schedule, rail, counter = picks
    scenario = ScenarioPlant(
        template_instances=(cust1, cust2),
        inbound_cap_breach_plants=(
            InboundCapBreachPlant(
                account_id=cust1.account_id,
                days_ago=days_ago,
                rail_name=rail.name,
                amount=_cap_breach_amount(schedule.cap, rail),
                counter_account_id=counter.id,
            ),
        ),
        today=anchor.date(),
    )
    return _emit_scenario(instance, scenario, prefix=prefix, dialect=dialect)


def _invoke_stuck_pending_plant(
    *,
    prefix: str,
    dialect: object,
    anchor: datetime,
    days_ago: int,
    amount_money: str,
    instance: object = None,
) -> str:
    """Adapter for ``StuckPendingPlant``. Picker finds the first Rail
    declaring ``max_pending_age``; the operator picks ``days_ago``
    (should comfortably exceed the rail's cap) + the amount."""
    from recon_gen.common.l2.auto_scenario import (  # noqa: PLC0415
        _pick_first_with,
    )

    instance = _require_instance(instance)
    _, cust1, cust2 = _materialize_customers(instance)
    rail = _pick_first_with(
        instance.rails, key=lambda r: r.max_pending_age is not None,
    )
    if rail is None:
        raise ValueError(
            "stuck_pending plant: no Rail declares max_pending_age "
            "in this L2."
        )
    scenario = ScenarioPlant(
        template_instances=(cust1, cust2),
        stuck_pending_plants=(
            StuckPendingPlant(
                account_id=cust1.account_id,
                days_ago=days_ago,
                rail_name=rail.name,
                amount=Decimal(amount_money),
            ),
        ),
        today=anchor.date(),
    )
    return _emit_scenario(instance, scenario, prefix=prefix, dialect=dialect)


def _invoke_stuck_unbundled_plant(
    *,
    prefix: str,
    dialect: object,
    anchor: datetime,
    days_ago: int,
    amount_money: str,
    instance: object = None,
) -> str:
    """Adapter for ``StuckUnbundledPlant``. Picker finds the first Rail
    declaring ``max_unbundled_age``."""
    from recon_gen.common.l2.auto_scenario import (  # noqa: PLC0415
        _pick_first_with,
    )

    instance = _require_instance(instance)
    _, cust1, cust2 = _materialize_customers(instance)
    rail = _pick_first_with(
        instance.rails, key=lambda r: r.max_unbundled_age is not None,
    )
    if rail is None:
        raise ValueError(
            "stuck_unbundled plant: no Rail declares max_unbundled_age "
            "in this L2."
        )
    scenario = ScenarioPlant(
        template_instances=(cust1, cust2),
        stuck_unbundled_plants=(
            StuckUnbundledPlant(
                account_id=cust2.account_id,
                days_ago=days_ago,
                rail_name=rail.name,
                amount=Decimal(amount_money),
            ),
        ),
        today=anchor.date(),
    )
    return _emit_scenario(instance, scenario, prefix=prefix, dialect=dialect)


def _invoke_chain_parent_disagreement_plant(
    *,
    prefix: str,
    dialect: object,
    anchor: datetime,
    days_ago: int,
    instance: object = None,
) -> str:
    """Adapter for ``ChainParentDisagreementPlant``. Picker resolves
    the two-template chain + child template. Synthetic parent
    transfer_ids are hardcoded so the dashboard's parent-id badge
    reads consistently across plant runs (no operator-facing knob;
    the demo's pedagogical point is 'two different parent ids on one
    child Transfer', not the specific id values)."""
    from recon_gen.common.l2.auto_scenario import (  # noqa: PLC0415
        _pick_two_template_chain_inputs,
    )

    instance = _require_instance(instance)
    _, cust1, cust2 = _materialize_customers(instance)
    picks = _pick_two_template_chain_inputs(instance)
    if picks is None:
        raise ValueError(
            "chain_parent_disagreement plant: no Chain whose singleton "
            "child resolves to a TransferTemplate in this L2."
        )
    _, child_template = picks
    scenario = ScenarioPlant(
        template_instances=(cust1, cust2),
        chain_parent_disagreement_plants=(
            ChainParentDisagreementPlant(
                child_template_name=child_template,
                days_ago=days_ago,
                parent_a_transfer_id="tr-cpd-parent-a-trainer",
                parent_b_transfer_id="tr-cpd-parent-b-trainer",
            ),
        ),
        today=anchor.date(),
    )
    return _emit_scenario(instance, scenario, prefix=prefix, dialect=dialect)


def _invoke_xor_group_missed_plant(
    *,
    prefix: str,
    dialect: object,
    anchor: datetime,
    days_ago: int,
    instance: object = None,
) -> str:
    """Adapter for ``XorVariantMissedFiringPlant``. Picker resolves
    a TransferTemplate with ≥1 XOR group AND a witness leg outside it."""
    from recon_gen.common.l2.auto_scenario import (  # noqa: PLC0415
        _pick_xor_missed_firing_inputs,
    )

    instance = _require_instance(instance)
    _, cust1, cust2 = _materialize_customers(instance)
    picks = _pick_xor_missed_firing_inputs(instance)
    if picks is None:
        raise ValueError(
            "xor_group_missed plant: no TransferTemplate declares "
            "leg_rail_xor_groups with a non-XOR-group leg_rail to use "
            "as witness."
        )
    template_name, group_idx, witness = picks
    scenario = ScenarioPlant(
        template_instances=(cust1, cust2),
        xor_variant_missed_firing_plants=(
            XorVariantMissedFiringPlant(
                template_name=template_name,
                target_xor_group_index=group_idx,
                days_ago=days_ago,
                witness_rail_name=witness,
            ),
        ),
        today=anchor.date(),
    )
    return _emit_scenario(instance, scenario, prefix=prefix, dialect=dialect)


def _invoke_xor_group_overlap_plant(
    *,
    prefix: str,
    dialect: object,
    anchor: datetime,
    days_ago: int,
    instance: object = None,
) -> str:
    """Adapter for ``XorVariantOverlapPlant``. Picker resolves any XOR
    group with ≥2 members (validator C1d enforces ≥2 at load time, so
    every declared XOR group qualifies)."""
    from recon_gen.common.l2.auto_scenario import (  # noqa: PLC0415
        _pick_xor_overlap_inputs,
    )

    instance = _require_instance(instance)
    _, cust1, cust2 = _materialize_customers(instance)
    picks = _pick_xor_overlap_inputs(instance)
    if picks is None:
        raise ValueError(
            "xor_group_overlap plant: no TransferTemplate declares "
            "leg_rail_xor_groups in this L2."
        )
    template_name, group_idx, variant_a, variant_b = picks
    scenario = ScenarioPlant(
        template_instances=(cust1, cust2),
        xor_variant_overlap_plants=(
            XorVariantOverlapPlant(
                template_name=template_name,
                target_xor_group_index=group_idx,
                days_ago=days_ago,
                variant_a_rail_name=variant_a,
                variant_b_rail_name=variant_b,
            ),
        ),
        today=anchor.date(),
    )
    return _emit_scenario(instance, scenario, prefix=prefix, dialect=dialect)


def _invoke_fan_in_missing_parent_plant(
    *,
    prefix: str,
    dialect: object,
    anchor: datetime,
    days_ago: int,
    instance: object = None,
) -> str:
    """Adapter for ``FanInChainMissingParentPlant``. Picker resolves
    any fan_in chain. ``parent_count`` is derived: expected-1 when
    expected_parent_count is set, else 1 (the orphan-threshold
    fallback)."""
    from recon_gen.common.l2.auto_scenario import (  # noqa: PLC0415
        _pick_fan_in_chain_inputs,
    )

    instance = _require_instance(instance)
    _, cust1, cust2 = _materialize_customers(instance)
    picks = _pick_fan_in_chain_inputs(instance)
    if picks is None:
        raise ValueError(
            "fan_in_missing_parent plant: no Chain declares fan_in=True "
            "with a known Rail or Template parent in this L2."
        )
    parent_rail, child_template, expected = picks
    healthy = expected if expected is not None else 2
    missing_count = max(1, healthy - 1)
    scenario = ScenarioPlant(
        template_instances=(cust1, cust2),
        fan_in_chain_missing_parent_plants=(
            FanInChainMissingParentPlant(
                chain_parent_rail_name=parent_rail,
                child_template_name=child_template,
                days_ago=days_ago,
                parent_count=missing_count,
            ),
        ),
        today=anchor.date(),
    )
    return _emit_scenario(instance, scenario, prefix=prefix, dialect=dialect)


def _invoke_fan_in_extra_parent_plant(
    *,
    prefix: str,
    dialect: object,
    anchor: datetime,
    days_ago: int,
    instance: object = None,
) -> str:
    """Adapter for ``FanInChainExtraParentPlant``. Only meaningful
    when the picked chain declares ``expected_parent_count`` —
    otherwise the AB.4.7 matview has no upper bound to flag against
    (the picker raises ValueError in that case)."""
    from recon_gen.common.l2.auto_scenario import (  # noqa: PLC0415
        _pick_fan_in_chain_inputs,
    )

    instance = _require_instance(instance)
    _, cust1, cust2 = _materialize_customers(instance)
    picks = _pick_fan_in_chain_inputs(instance)
    if picks is None:
        raise ValueError(
            "fan_in_extra_parent plant: no Chain declares fan_in=True "
            "with a known Rail or Template parent in this L2."
        )
    parent_rail, child_template, expected = picks
    if expected is None:
        raise ValueError(
            "fan_in_extra_parent plant: the picked fan_in chain has "
            "no expected_parent_count set — extra-parent violations "
            "can't be flagged without an upper bound."
        )
    scenario = ScenarioPlant(
        template_instances=(cust1, cust2),
        fan_in_chain_extra_parent_plants=(
            FanInChainExtraParentPlant(
                chain_parent_rail_name=parent_rail,
                child_template_name=child_template,
                days_ago=days_ago,
                parent_count=expected + 1,
            ),
        ),
        today=anchor.date(),
    )
    return _emit_scenario(instance, scenario, prefix=prefix, dialect=dialect)


def _invoke_multi_xor_missed_plant(
    *,
    prefix: str,
    dialect: object,
    anchor: datetime,
    days_ago: int,
    instance: object = None,
) -> str:
    """Adapter for ``MultiXorMissedPlant``. Picker resolves any chain
    with ≥2 non-fan_in children + a Rail or Template parent."""
    from recon_gen.common.l2.auto_scenario import (  # noqa: PLC0415
        _pick_multi_xor_chain_inputs,
    )

    instance = _require_instance(instance)
    _, cust1, cust2 = _materialize_customers(instance)
    picks = _pick_multi_xor_chain_inputs(instance)
    if picks is None:
        raise ValueError(
            "multi_xor_missed plant: no Chain declares ≥2 non-fan_in "
            "children with a known Rail or Template parent in this L2."
        )
    parent, _child_a, _child_b = picks
    scenario = ScenarioPlant(
        template_instances=(cust1, cust2),
        multi_xor_missed_plants=(
            MultiXorMissedPlant(
                chain_parent_rail_name=parent,
                days_ago=days_ago,
            ),
        ),
        today=anchor.date(),
    )
    return _emit_scenario(instance, scenario, prefix=prefix, dialect=dialect)


def _invoke_multi_xor_overlap_plant(
    *,
    prefix: str,
    dialect: object,
    anchor: datetime,
    days_ago: int,
    instance: object = None,
) -> str:
    """Adapter for ``MultiXorOverlapPlant``. Same picker as
    ``multi_xor_missed`` — uses children A + B as the two overlapping
    XOR siblings."""
    from recon_gen.common.l2.auto_scenario import (  # noqa: PLC0415
        _pick_multi_xor_chain_inputs,
    )

    instance = _require_instance(instance)
    _, cust1, cust2 = _materialize_customers(instance)
    picks = _pick_multi_xor_chain_inputs(instance)
    if picks is None:
        raise ValueError(
            "multi_xor_overlap plant: no Chain declares ≥2 non-fan_in "
            "children with a known Rail or Template parent in this L2."
        )
    parent, child_a, child_b = picks
    scenario = ScenarioPlant(
        template_instances=(cust1, cust2),
        multi_xor_overlap_plants=(
            MultiXorOverlapPlant(
                chain_parent_rail_name=parent,
                variant_a_child_name=child_a,
                variant_b_child_name=child_b,
                days_ago=days_ago,
            ),
        ),
        today=anchor.date(),
    )
    return _emit_scenario(instance, scenario, prefix=prefix, dialect=dialect)


def _invoke_supersession_audit_plant(
    *,
    prefix: str,
    dialect: object,
    anchor: datetime,
    days_ago: int,
    original_amount: str,
    corrected_amount: str,
    instance: object = None,
) -> str:
    """Adapter for ``SupersessionPlant``. Picker resolves a rail whose
    customer-side leg is the template role. The operator picks
    ``days_ago`` + the original + corrected amounts (the diagnostic
    matview surfaces the (original, correction) pair; non-trivial
    delta is what makes the demo visually obvious)."""
    from recon_gen.common.l2.auto_scenario import (  # noqa: PLC0415
        _pick_supersession_rail,
    )

    instance = _require_instance(instance)
    template, cust1, cust2 = _materialize_customers(instance)
    rail = _pick_supersession_rail(instance, template.role)
    if rail is None:
        raise ValueError(
            "supersession_audit plant: no Rail with customer-side leg "
            "matching the template role in this L2."
        )
    scenario = ScenarioPlant(
        template_instances=(cust1, cust2),
        supersession_plants=(
            SupersessionPlant(
                account_id=cust1.account_id,
                days_ago=days_ago,
                rail_name=rail.name,
                original_amount=Decimal(original_amount),
                corrected_amount=Decimal(corrected_amount),
            ),
        ),
        today=anchor.date(),
    )
    return _emit_scenario(instance, scenario, prefix=prefix, dialect=dialect)


# -- BU.3 needs-build adapters ---------------------------------------------
# 1 L1 (expected_eod_balance_breach) + 4 L2FT Hygiene (chain_orphan /
# dead_bundles_activity / dead_metadata / dead_limit_schedule). Pattern
# matches the existing 20 adapters: uniform signature, ``_require_instance``
# narrowing on the object-typed ``instance`` kwarg, function-local imports
# via ``noqa: PLC0415`` to keep the module-load graph narrow.


def _invoke_expected_eod_balance_breach_plant(
    *,
    prefix: str,
    dialect: object,
    anchor: datetime,
    days_ago: int,
    expected: str,
    variance: str,
    instance: object = None,
) -> str:
    """Adapter for ``ExpectedEodBalancePlant`` (BU.3.1). Picker resolves
    the first internal Account role on the L2; the operator picks
    ``days_ago`` + ``expected`` (the per-row expected_eod_balance) +
    ``variance`` (the delta between stored money + expected). The
    ``<prefix>_expected_eod_balance_breach`` matview surfaces a row when
    variance != 0.
    """
    from recon_gen.common.l2.primitives import Identifier  # noqa: PLC0415

    instance = _require_instance(instance)
    _, cust1, cust2 = _materialize_customers(instance)
    role = _pick_first_internal_account_role(instance)
    if role is None:
        raise ValueError(
            "expected_eod_balance_breach plant: no internal Account "
            "declared on this L2 (the variance matview keys on "
            "internal-scope rows)."
        )
    scenario = ScenarioPlant(
        template_instances=(cust1, cust2),
        expected_eod_balance_plants=(
            ExpectedEodBalancePlant(
                role=Identifier(role),
                days_ago=days_ago,
                expected=Decimal(expected),
                variance=Decimal(variance),
            ),
        ),
        today=anchor.date(),
    )
    return _emit_scenario(instance, scenario, prefix=prefix, dialect=dialect)


def _pick_first_internal_account_role(instance: "L2Instance") -> str | None:
    """First (alphabetical) Account role with ``scope='internal'`` on
    the L2 — the deterministic pick for the BU.3.1 EOD plant. Mirrors
    the ``find_internal_with_role`` helper in spine without raising on
    miss; the adapter raises a Trainer-readable error itself."""
    candidates: list[str] = []
    for a in instance.accounts:
        if str(a.scope) == "internal":
            candidates.append(str(a.role))
    if not candidates:
        return None
    return sorted(candidates)[0]


def _pick_first_explicit_daily_target(
    instance: "L2Instance",
) -> tuple[str, str] | None:
    """First (alphabetical) declared ``explicit_daily`` target on the
    L2. Returns ``(account_id, account_role)`` for a singleton or
    ``("", account_role)`` for a template (the role is enough to
    match any materialized instance under the template; the seed
    materializes ``cust-NNN`` accounts under each template). Returns
    None if no entity declares ``explicit_daily``.

    The picker is deterministic — alphabetical first match — so
    repeated plants land on the same target.
    """
    singletons: list[tuple[str, str]] = []
    templates: list[tuple[str, str]] = []
    for a in instance.accounts:
        if a.balance_cadence == "explicit_daily" and str(a.scope) == "internal":
            singletons.append((str(a.id), str(a.role)))
    for t in instance.account_templates:
        if t.balance_cadence == "explicit_daily" and str(t.scope) == "internal":
            templates.append(("", str(t.role)))
    candidates = sorted(singletons + templates)
    return candidates[0] if candidates else None


def _invoke_balance_cadence_gap_plant(
    *,
    prefix: str,
    dialect: object,
    anchor: datetime,
    days_ago: int,
    instance: object = None,
) -> str:
    """CL.7 — Adapter for the balance_cadence_gap plant.

    Picks the alphabetically-first ``explicit_daily`` account (or
    template-role) declared in the L2, then DELETEs the
    daily_balances row at (account_id, anchor − days_ago) so the
    CL.6 invariant matview surfaces a ``declared_daily_missing``
    row. For templates the DELETE matches by ``account_role`` so
    every materialized instance under the template gets the same
    gap (consistent with the explicit_daily declaration applying
    to all instances per CL.2 Lock 4).

    Per audit §8 + PLAN.md CL.7: raises ``ValueError`` if no
    ``explicit_daily`` entity exists on the L2 (the kind has
    nothing to plant).
    """
    from datetime import timedelta as _td  # noqa: PLC0415

    from recon_gen.common.sql.dialect import Dialect, date_literal  # noqa: PLC0415

    inst = _require_instance(instance)
    target = _pick_first_explicit_daily_target(inst)
    if target is None:
        raise ValueError(
            "balance_cadence_gap plant: no internal entity declares "
            "balance_cadence='explicit_daily' on this L2 — the kind "
            "has nothing to plant. Set balance_cadence on an Account "
            "or AccountTemplate to enable this plant."
        )
    target_account_id, target_role = target
    gap_date = (anchor - _td(days=days_ago)).date()
    gap_day = gap_date.isoformat()
    next_day = (gap_date + _td(days=1)).isoformat()
    # CT.0 — Oracle rejects bare ISO strings as TIMESTAMP comparands
    # (ORA-01843 "not a valid month"); use the dialect's date literal
    # form. PG + DuckDB also accept `DATE 'YYYY-MM-DD'`. Bounds form
    # a half-open day window [gap_day, gap_day+1) — cleaner than the
    # prior `< 'gap_dayT23:59:59'` shape and avoids the missed-by-a-
    # microsecond edge case.
    dialect_obj = dialect if isinstance(dialect, Dialect) else Dialect.DUCKDB
    lo = date_literal(gap_day, dialect_obj)
    hi = date_literal(next_day, dialect_obj)
    if target_account_id:
        # Singleton: exact match by account_id + business_day_start.
        return (
            f"-- CL.7 plant: DELETE the explicit_daily row at "
            f"({target_account_id!r}, {gap_day})\n"
            f"DELETE FROM {prefix}_daily_balances "
            f"WHERE account_id = '{target_account_id}' "
            f"AND business_day_start >= {lo} "
            f"AND business_day_start < {hi};\n"
        )
    # Template path: any materialized instance whose role matches.
    return (
        f"-- CL.7 plant: DELETE the explicit_daily rows for template "
        f"role {target_role!r} on {gap_day}\n"
        f"DELETE FROM {prefix}_daily_balances "
        f"WHERE account_role = '{target_role}' "
        f"AND business_day_start >= {lo} "
        f"AND business_day_start < {hi};\n"
    )


def _invoke_chain_orphan_plant(
    *,
    prefix: str,
    dialect: object,
    anchor: datetime,
    count: int,
    instance: object = None,
) -> str:
    """Adapter for ``add_chain_orphan_gap_rows`` (BU.3.2). Plants
    ``count`` parent firings against the first Required chain in the
    L2 with no matching child firings, so the L2FT chain_orphans check
    fires."""
    from recon_gen.common.l2.demo_etl_gaps import (  # noqa: PLC0415
        add_chain_orphan_gap_rows,
    )
    from recon_gen.common.sql.dialect import Dialect  # noqa: PLC0415

    inst = _require_instance(instance)
    return add_chain_orphan_gap_rows(
        inst,
        prefix=prefix,
        dialect=dialect if isinstance(dialect, Dialect) else Dialect.DUCKDB,
        anchor=anchor,
        count=count,
    )


def _invoke_dead_bundles_activity_plant(
    *,
    prefix: str,
    dialect: object,
    anchor: datetime,
    instance: object = None,
) -> str:
    """Adapter for ``add_dead_bundles_activity_gap_rows`` (BU.3.3).
    DELETEs all transactions matching the first declared bundle_target;
    the L2FT dead_bundles_activity check then fires NOT EXISTS for that
    (aggregating_rail, bundle_target) pair.

    No tunable primitives — the operator's only knob is on/off (the
    picker is deterministic alphabetical-first). Same shape as the
    existing ``uncovered_rail`` / ``uncovered_template`` Coverage
    plants (BU.2b stage 3).
    """
    del anchor  # DELETE has no posting time
    from recon_gen.common.l2.demo_etl_gaps import (  # noqa: PLC0415
        add_dead_bundles_activity_gap_rows,
    )
    from recon_gen.common.sql.dialect import Dialect  # noqa: PLC0415

    inst = _require_instance(instance)
    return add_dead_bundles_activity_gap_rows(
        inst,
        prefix=prefix,
        dialect=dialect if isinstance(dialect, Dialect) else Dialect.DUCKDB,
    )


def _invoke_dead_metadata_plant(
    *,
    prefix: str,
    dialect: object,
    anchor: datetime,
    instance: object = None,
) -> str:
    """Adapter for ``add_dead_metadata_gap_rows`` (BU.3.4). DELETEs all
    transactions on the first L2-declared rail with non-empty
    ``metadata_keys`` so the rail-level NOT EXISTS guard in the L2FT
    ``dead_metadata`` check matches for every key declared on the rail.

    No tunable primitives — alphabetical-first picker; same shape as
    the existing ``uncovered_rail`` plant.
    """
    del anchor
    from recon_gen.common.l2.demo_etl_gaps import (  # noqa: PLC0415
        add_dead_metadata_gap_rows,
    )
    from recon_gen.common.sql.dialect import Dialect  # noqa: PLC0415

    inst = _require_instance(instance)
    return add_dead_metadata_gap_rows(
        inst,
        prefix=prefix,
        dialect=dialect if isinstance(dialect, Dialect) else Dialect.DUCKDB,
    )


def _invoke_dead_limit_schedule_plant(
    *,
    prefix: str,
    dialect: object,
    anchor: datetime,
    instance: object = None,
) -> str:
    """Adapter for ``add_dead_limit_schedule_gap_rows`` (BU.3.5).
    DELETEs all Debit transactions for the first (parent_role,
    rail_name) LimitSchedule cell so the L2FT dead_limit_schedules
    check's NOT EXISTS matches.

    No tunable primitives — alphabetical-first picker.
    """
    del anchor
    from recon_gen.common.l2.demo_etl_gaps import (  # noqa: PLC0415
        add_dead_limit_schedule_gap_rows,
    )
    from recon_gen.common.sql.dialect import Dialect  # noqa: PLC0415

    inst = _require_instance(instance)
    return add_dead_limit_schedule_gap_rows(
        inst,
        prefix=prefix,
        dialect=dialect if isinstance(dialect, Dialect) else Dialect.DUCKDB,
    )


# -- THE registry -----------------------------------------------------------


PLANT_REGISTRY: Final[tuple[PlantKindEntry, ...]] = (
    PlantKindEntry(
        kind="phantom_rail",
        category=PlantCategory.L2_TRIAGE,
        family="L2 Triage gaps",
        # BU.2a — bridge to the typed L2_Triage_Gaps.md section. The
        # registry kind is operator-vocabulary ("phantom_rail" reads
        # well in the Trainer URL); the underlying GapKind literal is
        # the parser key.
        section_kind="unmatched_rail",
        plant_function=_invoke_phantom_rail_plant,
        primitives=(
            PrimitiveIntField(
                name="count",
                label="Number of rows",
                help_text=(
                    "How many transaction rows to plant. Triage's "
                    "volume badge reads this count directly."
                ),
                default=3,
                min_value=1,
                max_value=100,
            ),
            PrimitiveStringField(
                name="rail_name",
                label="Rail name",
                help_text=(
                    "The rail_name value to plant. Must NOT match any "
                    "rail declared in your L2 (the whole point of the "
                    "demo). Default reads like a plausible legacy rail."
                ),
                default="legacy_card_swipe",
            ),
        ),
        tour_destination=TourDestination(
            primary_url="/etl/triage",
        ),
        dashboard_check=DashboardCheck(
            url_path="/etl/triage",
            expect_text_contains="legacy_card_swipe",
        ),
    ),
    PlantKindEntry(
        kind="phantom_template",
        category=PlantCategory.L2_TRIAGE,
        family="L2 Triage gaps",
        section_kind="unmatched_template",
        plant_function=_invoke_phantom_template_plant,
        primitives=(
            PrimitiveIntField(
                name="count",
                label="Number of rows",
                help_text=(
                    "How many transactions to plant with the unrecognized "
                    "template_name. Triage's volume badge reads this count."
                ),
                default=2,
                min_value=1,
                max_value=100,
            ),
        ),
        tour_destination=TourDestination(
            primary_url="/etl/triage",
        ),
        dashboard_check=DashboardCheck(
            url_path="/etl/triage",
            # Reads the demo emitter's hard-coded PHANTOM_TEMPLATE_NAME.
            # BV.3.2 fix — the L2 Triage card title comes from
            # SECTION_TITLE_BY_KIND["unmatched_template"] via the typed
            # source. Anti-drift gate ensures rename divergence trips.
            expect_text_contains="Unmatched template_name",
        ),
    ),
    PlantKindEntry(
        kind="missing_metadata_key",
        category=PlantCategory.L2_TRIAGE,
        family="L2 Triage gaps",
        section_kind="missing_metadata_key",
        plant_function=_invoke_missing_metadata_plant,
        # No tunable primitives — the plant picks a target template from
        # the L2 deterministically (first template that declares a
        # required transfer_key). The operator's only knob is "is the
        # plant on or off"; future BU.4 polish may surface the chosen
        # template name in the form for transparency.
        primitives=(),
        tour_destination=TourDestination(
            primary_url="/etl/triage",
        ),
        dashboard_check=DashboardCheck(
            url_path="/etl/triage",
            # BV.3.2 fix — section title from the typed catalogue, not
            # an ad-hoc substring of the underlying detect_gaps()
            # diagnosis prose.
            expect_text_contains="Missing required metadata key",
        ),
    ),
    # ------ L1 Conservation ------------------------------------------------
    # BU.2b stage 2 — L1 invariant kinds. Pattern: dashboard_check uses
    # the un-prefixed matview-suffix name (e.g. ``"drift"`` not
    # ``"<prefix>_drift"``); the parameterized e2e prefixes at runtime
    # using cfg.db_table_prefix. Same convention all L1 entries below
    # follow — keeps the registry portable across prefixes (sasquatch_pr
    # / spec_example / fuzz seeds) and means moving a deployment to a
    # new prefix doesn't require touching this file.
    PlantKindEntry(
        kind="drift",
        category=PlantCategory.L1_INVARIANT,
        family="L1 Conservation",
        plant_function=_invoke_drift_plant,
        primitives=(
            PrimitiveIntField(
                name="days_ago",
                label="Days ago",
                help_text=(
                    "Business-day offset from today for the drift "
                    "cell (0 = today, 1 = yesterday, ...)."
                ),
                default=5,
                min_value=0,
                max_value=90,
            ),
            PrimitiveStringField(
                name="delta_money",
                label="Drift amount ($)",
                help_text=(
                    "Stored - computed delta in dollars. Positive: "
                    "stored is HIGHER than the sum of postings. "
                    "Negative: stored is LOWER. Anything non-zero "
                    "surfaces on the Drift sheet."
                ),
                default="75.00",
            ),
        ),
        tour_destination=TourDestination(
            primary_url="/dashboards/l1_dashboard/sheets/l1-sheet-drift",
        ),
        dashboard_check=DashboardCheck(
            matview_name="drift",
            min_row_count=1,
        ),
    ),
    PlantKindEntry(
        kind="ledger_drift",
        category=PlantCategory.L1_INVARIANT,
        family="L1 Conservation",
        # ``ledger_drift`` is the control-account flavor of the same
        # DriftPlant shape (Σ leaves should match the control account);
        # plant + matview key off the same row. Same Drift sheet too.
        plant_function=_invoke_ledger_drift_plant,
        primitives=(
            PrimitiveIntField(
                name="days_ago",
                label="Days ago",
                help_text=(
                    "Business-day offset for the ledger-drift cell."
                ),
                default=5,
                min_value=0,
                max_value=90,
            ),
            PrimitiveStringField(
                name="delta_money",
                label="Drift amount ($)",
                help_text=(
                    "Stored - computed delta in dollars (control "
                    "account vs. Σ leaves)."
                ),
                default="75.00",
            ),
        ),
        tour_destination=TourDestination(
            primary_url="/dashboards/l1_dashboard/sheets/l1-sheet-drift",
        ),
        dashboard_check=DashboardCheck(
            matview_name="ledger_drift",
            min_row_count=1,
        ),
    ),
    PlantKindEntry(
        kind="overdraft",
        category=PlantCategory.L1_INVARIANT,
        family="L1 Conservation",
        plant_function=_invoke_overdraft_plant,
        primitives=(
            PrimitiveIntField(
                name="days_ago",
                label="Days ago",
                help_text="Business-day offset for the overdrawn cell.",
                default=6,
                min_value=0,
                max_value=90,
            ),
            PrimitiveStringField(
                name="money",
                label="Stored balance ($)",
                help_text=(
                    "MUST be negative — the Non-Negative Balance "
                    "SHOULD-constraint only fires when stored balance "
                    "< 0. Default $-1,500 reads as a plausible "
                    "overdraft amount."
                ),
                default="-1500.00",
            ),
        ),
        tour_destination=TourDestination(
            primary_url="/dashboards/l1_dashboard/sheets/l1-sheet-overdraft",
        ),
        dashboard_check=DashboardCheck(
            matview_name="overdraft",
            min_row_count=1,
        ),
    ),
    # ------ L1 Cap ---------------------------------------------------------
    PlantKindEntry(
        kind="limit_breach_outbound",
        category=PlantCategory.L1_INVARIANT,
        family="L1 Cap",
        # Outbound + inbound both surface as rows on the same
        # ``limit_breach`` matview with a ``direction`` column
        # distinguishing them; the typed handbook section is shared.
        section_kind="limit_breach",
        kind_qualifier="Outbound direction",
        plant_function=_invoke_limit_breach_outbound_plant,
        primitives=(
            PrimitiveIntField(
                name="days_ago",
                label="Days ago",
                help_text="Business-day offset for the cap-breach cell.",
                default=4,
                min_value=0,
                max_value=90,
            ),
        ),
        tour_destination=TourDestination(
            primary_url=(
                "/dashboards/l1_dashboard/sheets/l1-sheet-limit-breach"
            ),
        ),
        dashboard_check=DashboardCheck(
            matview_name="limit_breach",
            min_row_count=1,
        ),
    ),
    PlantKindEntry(
        kind="limit_breach_inbound",
        category=PlantCategory.L1_INVARIANT,
        family="L1 Cap",
        section_kind="limit_breach",
        kind_qualifier="Inbound direction",
        plant_function=_invoke_limit_breach_inbound_plant,
        primitives=(
            PrimitiveIntField(
                name="days_ago",
                label="Days ago",
                help_text="Business-day offset for the inbound-breach cell.",
                default=3,
                min_value=0,
                max_value=90,
            ),
        ),
        tour_destination=TourDestination(
            primary_url=(
                "/dashboards/l1_dashboard/sheets/l1-sheet-limit-breach"
            ),
        ),
        dashboard_check=DashboardCheck(
            matview_name="limit_breach",
            min_row_count=1,
        ),
    ),
    # CL.7 — balance_cadence_gap. Sits alongside the cap plants
    # because both are operator-facing "the institution didn't
    # report the right thing" surfaces (cap-breach + cadence-miss).
    PlantKindEntry(
        kind="balance_cadence_gap",
        category=PlantCategory.L1_INVARIANT,
        family="L1 Cap",
        plant_function=_invoke_balance_cadence_gap_plant,
        primitives=(
            PrimitiveIntField(
                name="days_ago",
                label="Days ago",
                help_text=(
                    "Business-day offset for the missing daily_balances "
                    "row. The plant picks the alphabetically-first "
                    "internal entity declaring "
                    "balance_cadence='explicit_daily' on this L2."
                ),
                default=2,
                min_value=0,
                max_value=90,
            ),
        ),
        tour_destination=TourDestination(
            primary_url=(
                "/dashboards/l1_dashboard/sheets/l1-sheet-exceptions"
            ),
        ),
        dashboard_check=DashboardCheck(
            matview_name="balance_cadence_gap",
            min_row_count=1,
        ),
    ),
    # ------ L1 Aging -------------------------------------------------------
    PlantKindEntry(
        kind="stuck_pending",
        category=PlantCategory.L1_INVARIANT,
        family="L1 Aging",
        plant_function=_invoke_stuck_pending_plant,
        primitives=(
            PrimitiveIntField(
                name="days_ago",
                label="Days ago",
                help_text=(
                    "Age of the pending leg in days. MUST exceed the "
                    "picked rail's max_pending_age (default 30 days "
                    "comfortably exceeds the typical PT4H–P7D range)."
                ),
                default=30,
                min_value=0,
                max_value=90,
            ),
            PrimitiveStringField(
                name="amount_money",
                label="Amount ($)",
                help_text=(
                    "Money amount on the stuck pending leg. Visual "
                    "magnitude only — does not affect whether the "
                    "SHOULD fires (age is the trigger)."
                ),
                default="450.00",
            ),
        ),
        tour_destination=TourDestination(
            primary_url=(
                "/dashboards/l1_dashboard/sheets/l1-sheet-pending-aging"
            ),
        ),
        dashboard_check=DashboardCheck(
            matview_name="stuck_pending",
            min_row_count=1,
        ),
    ),
    PlantKindEntry(
        kind="stuck_unbundled",
        category=PlantCategory.L1_INVARIANT,
        family="L1 Aging",
        plant_function=_invoke_stuck_unbundled_plant,
        primitives=(
            PrimitiveIntField(
                name="days_ago",
                label="Days ago",
                help_text=(
                    "Age of the unbundled leg in days. MUST exceed the "
                    "picked rail's max_unbundled_age."
                ),
                default=30,
                min_value=0,
                max_value=90,
            ),
            PrimitiveStringField(
                name="amount_money",
                label="Amount ($)",
                help_text="Money amount on the unbundled leg.",
                default="12.50",
            ),
        ),
        tour_destination=TourDestination(
            primary_url=(
                "/dashboards/l1_dashboard/sheets/l1-sheet-unbundled-aging"
            ),
        ),
        dashboard_check=DashboardCheck(
            matview_name="stuck_unbundled",
            min_row_count=1,
        ),
    ),
    # ------ L1 Chain coherence --------------------------------------------
    # All five chain-coherence kinds surface on L1 Exceptions
    # (no dedicated sheets per the INVARIANT_KIND_TO_SHEET map); shared
    # tour destination is the L1 Exceptions sheet. Sections are
    # shared too — xor_group_missed + xor_group_overlap both reference
    # ``xor_group_violation``; fan_in_missing + fan_in_extra share
    # ``fan_in_disagreement``; multi_xor_missed + multi_xor_overlap share
    # ``multi_xor_violation``.
    PlantKindEntry(
        kind="chain_parent_disagreement",
        category=PlantCategory.L1_INVARIANT,
        family="L1 Chain coherence",
        plant_function=_invoke_chain_parent_disagreement_plant,
        primitives=(
            PrimitiveIntField(
                name="days_ago",
                label="Days ago",
                help_text=(
                    "Business-day offset for the disagreeing-parent "
                    "legs (both land on the same day)."
                ),
                default=1,
                min_value=0,
                max_value=90,
            ),
        ),
        tour_destination=TourDestination(
            primary_url=(
                "/dashboards/l1_dashboard/sheets/l1-sheet-exceptions"
            ),
        ),
        dashboard_check=DashboardCheck(
            matview_name="chain_parent_disagreement",
            min_row_count=1,
        ),
    ),
    PlantKindEntry(
        kind="xor_group_missed",
        category=PlantCategory.L1_INVARIANT,
        family="L1 Chain coherence",
        section_kind="xor_group_violation",
        kind_qualifier="Missed-firing variant",
        plant_function=_invoke_xor_group_missed_plant,
        primitives=(
            PrimitiveIntField(
                name="days_ago",
                label="Days ago",
                help_text="Business-day offset for the XOR-missed firing.",
                default=0,
                min_value=0,
                max_value=90,
            ),
        ),
        tour_destination=TourDestination(
            primary_url=(
                "/dashboards/l1_dashboard/sheets/l1-sheet-exceptions"
            ),
        ),
        dashboard_check=DashboardCheck(
            matview_name="xor_group_violation",
            min_row_count=1,
        ),
    ),
    PlantKindEntry(
        kind="xor_group_overlap",
        category=PlantCategory.L1_INVARIANT,
        family="L1 Chain coherence",
        section_kind="xor_group_violation",
        kind_qualifier="Overlap-firing variant",
        plant_function=_invoke_xor_group_overlap_plant,
        primitives=(
            PrimitiveIntField(
                name="days_ago",
                label="Days ago",
                help_text="Business-day offset for the XOR-overlap firing.",
                default=1,
                min_value=0,
                max_value=90,
            ),
        ),
        tour_destination=TourDestination(
            primary_url=(
                "/dashboards/l1_dashboard/sheets/l1-sheet-exceptions"
            ),
        ),
        dashboard_check=DashboardCheck(
            matview_name="xor_group_violation",
            min_row_count=1,
        ),
    ),
    PlantKindEntry(
        kind="fan_in_missing_parent",
        category=PlantCategory.L1_INVARIANT,
        family="L1 Chain coherence",
        section_kind="fan_in_disagreement",
        kind_qualifier="Missing-parent variant",
        plant_function=_invoke_fan_in_missing_parent_plant,
        primitives=(
            PrimitiveIntField(
                name="days_ago",
                label="Days ago",
                help_text=(
                    "Business-day offset for the fan-in batch with "
                    "missing parent contribution(s)."
                ),
                default=4,
                min_value=0,
                max_value=90,
            ),
        ),
        tour_destination=TourDestination(
            primary_url=(
                "/dashboards/l1_dashboard/sheets/l1-sheet-exceptions"
            ),
        ),
        dashboard_check=DashboardCheck(
            matview_name="fan_in_disagreement",
            min_row_count=1,
        ),
    ),
    PlantKindEntry(
        kind="fan_in_extra_parent",
        category=PlantCategory.L1_INVARIANT,
        family="L1 Chain coherence",
        section_kind="fan_in_disagreement",
        kind_qualifier="Extra-parent variant",
        plant_function=_invoke_fan_in_extra_parent_plant,
        primitives=(
            PrimitiveIntField(
                name="days_ago",
                label="Days ago",
                help_text=(
                    "Business-day offset for the fan-in batch with "
                    "excess parent contribution(s). Only fires when "
                    "the picked chain declares expected_parent_count."
                ),
                default=3,
                min_value=0,
                max_value=90,
            ),
        ),
        tour_destination=TourDestination(
            primary_url=(
                "/dashboards/l1_dashboard/sheets/l1-sheet-exceptions"
            ),
        ),
        dashboard_check=DashboardCheck(
            matview_name="fan_in_disagreement",
            min_row_count=1,
        ),
    ),
    PlantKindEntry(
        kind="multi_xor_missed",
        category=PlantCategory.L1_INVARIANT,
        family="L1 Chain coherence",
        section_kind="multi_xor_violation",
        kind_qualifier="Missed-firing variant",
        plant_function=_invoke_multi_xor_missed_plant,
        primitives=(
            PrimitiveIntField(
                name="days_ago",
                label="Days ago",
                help_text=(
                    "Business-day offset for the parent firing with "
                    "zero XOR-sibling children."
                ),
                default=6,
                min_value=0,
                max_value=90,
            ),
        ),
        tour_destination=TourDestination(
            primary_url=(
                "/dashboards/l1_dashboard/sheets/l1-sheet-exceptions"
            ),
        ),
        dashboard_check=DashboardCheck(
            matview_name="multi_xor_violation",
            min_row_count=1,
        ),
    ),
    PlantKindEntry(
        kind="multi_xor_overlap",
        category=PlantCategory.L1_INVARIANT,
        family="L1 Chain coherence",
        section_kind="multi_xor_violation",
        kind_qualifier="Overlap-firing variant",
        plant_function=_invoke_multi_xor_overlap_plant,
        primitives=(
            PrimitiveIntField(
                name="days_ago",
                label="Days ago",
                help_text=(
                    "Business-day offset for the parent firing with "
                    "two overlapping XOR-sibling children."
                ),
                default=5,
                min_value=0,
                max_value=90,
            ),
        ),
        tour_destination=TourDestination(
            primary_url=(
                "/dashboards/l1_dashboard/sheets/l1-sheet-exceptions"
            ),
        ),
        dashboard_check=DashboardCheck(
            matview_name="multi_xor_violation",
            min_row_count=1,
        ),
    ),
    # ------ L1 Audit -------------------------------------------------------
    # Supersession Audit is diagnostic (not a SHOULD-constraint), so it
    # has no matview — the dashboard sheet queries the base
    # ``<prefix>_transactions`` table directly for entry-versioned rows.
    # DashboardCheck uses url_path + expect_text_contains for the same
    # reason the L2 Triage entries do.
    PlantKindEntry(
        kind="supersession_audit",
        category=PlantCategory.L1_INVARIANT,
        family="L1 Audit",
        plant_function=_invoke_supersession_audit_plant,
        primitives=(
            PrimitiveIntField(
                name="days_ago",
                label="Days ago",
                help_text=(
                    "Business-day offset for the original posting; "
                    "the correction lands a few minutes later same day."
                ),
                default=3,
                min_value=0,
                max_value=90,
            ),
            PrimitiveStringField(
                name="original_amount",
                label="Original amount ($)",
                help_text="Amount the first (incorrect) posting carried.",
                default="250.00",
            ),
            PrimitiveStringField(
                name="corrected_amount",
                label="Corrected amount ($)",
                help_text=(
                    "Amount the TechnicalCorrection posting carries. "
                    "Visible delta makes the demo's 'we got the amount "
                    "wrong, here's the rewrite' shape obvious."
                ),
                default="275.00",
            ),
        ),
        tour_destination=TourDestination(
            primary_url=(
                "/dashboards/l1_dashboard/sheets/l1-sheet-supersession-audit"
            ),
        ),
        dashboard_check=DashboardCheck(
            url_path=(
                "/dashboards/l1_dashboard/sheets/l1-sheet-supersession-audit"
            ),
            # The sheet's title is stable across renderings; the
            # planted row's specific values vary by L2 picker output.
            expect_text_contains="Supersession",
        ),
    ),
    # -- L2 Coverage (declared but unexercised — BU.2b stage 3) -------------
    PlantKindEntry(
        kind="uncovered_rail",
        category=PlantCategory.L2_COVERAGE,
        family="L2 Coverage gaps",
        section_kind="uncovered_rail",
        plant_function=_invoke_uncovered_rail_plant,
        # No tunable primitives — the emitter picks the alphabetically-
        # last L2-declared rail deterministically (a stable, demo-clear
        # choice). Operator's only knob is on/off.
        primitives=(),
        tour_destination=TourDestination(
            # BV.3.2 fix — Coverage panel lives at /etl/run (under the
            # Refresh-Data log section, not the Triage card list). My
            # BU.4 P2.9 redirect was wrong; reverting + BV.3.1 test
            # caught it.
            primary_url="/etl/run",
        ),
        dashboard_check=DashboardCheck(
            url_path="/etl/run",
            # The Coverage Rails panel renders the un-covered rail
            # with a "no rows" status badge — text varies but the
            # word "Coverage" is stable on the panel header.
            expect_text_contains="Coverage",
        ),
    ),
    PlantKindEntry(
        kind="uncovered_template",
        category=PlantCategory.L2_COVERAGE,
        family="L2 Coverage gaps",
        section_kind="uncovered_template",
        plant_function=_invoke_uncovered_template_plant,
        primitives=(),
        tour_destination=TourDestination(
            primary_url="/etl/run",  # BV.3.2 fix — see uncovered_rail above
        ),
        dashboard_check=DashboardCheck(
            url_path="/etl/run",
            expect_text_contains="Coverage",
        ),
    ),
    # ------ BU.3.1 — Expected EOD Balance (L1 Cap, no dedicated sheet) -----
    # The matview ``<prefix>_expected_eod_balance_breach`` filters
    # ``expected_eod_balance IS NOT NULL AND money <> expected_eod_balance``.
    # The plant emits ONE row into ``_daily_balances`` (no transactions)
    # with money = expected + variance; the variance row materializes.
    # Tour destination: L1 Exceptions (no per-kind sheet per
    # INVARIANT_KIND_TO_SHEET).
    PlantKindEntry(
        kind="expected_eod_balance_breach",
        category=PlantCategory.L1_INVARIANT,
        family="L1 Cap",
        plant_function=_invoke_expected_eod_balance_breach_plant,
        primitives=(
            PrimitiveIntField(
                name="days_ago",
                label="Days ago",
                help_text=(
                    "Business-day offset for the EOD-variance row."
                ),
                default=2,
                min_value=0,
                max_value=90,
            ),
            PrimitiveStringField(
                name="expected",
                label="Expected EOD ($)",
                help_text=(
                    "The per-row expected_eod_balance value the daily "
                    "snapshot SHOULD have hit. Demo magnitude only."
                ),
                default="100.00",
            ),
            PrimitiveStringField(
                name="variance",
                label="Variance ($)",
                help_text=(
                    "Stored - expected delta in dollars. Non-zero "
                    "surfaces on L1 Exceptions. Zero would silently "
                    "filter out (matview gates on money <> expected)."
                ),
                default="5.00",
            ),
        ),
        tour_destination=TourDestination(
            primary_url=(
                "/dashboards/l1_dashboard/sheets/l1-sheet-exceptions"
            ),
        ),
        dashboard_check=DashboardCheck(
            matview_name="expected_eod_balance_breach",
            min_row_count=1,
        ),
    ),
    # ------ BU.3.2-5 — L2FT Hygiene plants ---------------------------------
    # All four land on the same L2FT L2 Hygiene Exceptions sheet (the
    # unified row-per-violation table). L2FT checks are
    # dataset-at-query-time (no matview), so dashboard_check uses
    # url_path + expect_text_contains keyed on the check's stable
    # display title — same pattern as L2 Triage entries.
    PlantKindEntry(
        kind="chain_orphan",
        category=PlantCategory.L2FT_HYGIENE,
        family="L2FT Hygiene",
        section_kind="chain_orphans",
        plant_function=_invoke_chain_orphan_plant,
        primitives=(
            PrimitiveIntField(
                name="count",
                label="Number of orphan parents",
                help_text=(
                    "How many parent firings to plant without matching "
                    "child firings. The L2FT chain_orphans check's "
                    "orphan_count column reads this directly."
                ),
                default=3,
                min_value=1,
                max_value=100,
            ),
        ),
        tour_destination=TourDestination(
            primary_url=(
                "/dashboards/l2_flow_tracing/sheets/l2ft-sheet-l2-exceptions"
            ),
        ),
        dashboard_check=DashboardCheck(
            url_path=(
                "/dashboards/l2_flow_tracing/sheets/l2ft-sheet-l2-exceptions"
            ),
            # The unified L2 Exceptions table's check_type column is the
            # stable display string for chain_orphans.
            expect_text_contains="Chain Orphans",
        ),
    ),
    PlantKindEntry(
        kind="dead_bundles_activity",
        category=PlantCategory.L2FT_HYGIENE,
        family="L2FT Hygiene",
        section_kind="dead_bundles_activity",
        plant_function=_invoke_dead_bundles_activity_plant,
        # No tunable primitives — alphabetical-first picker chooses the
        # bundle_target deterministically. Operator's only knob is
        # on/off (same as the Coverage plants).
        primitives=(),
        tour_destination=TourDestination(
            primary_url=(
                "/dashboards/l2_flow_tracing/sheets/l2ft-sheet-l2-exceptions"
            ),
        ),
        dashboard_check=DashboardCheck(
            url_path=(
                "/dashboards/l2_flow_tracing/sheets/l2ft-sheet-l2-exceptions"
            ),
            expect_text_contains="Dead Bundles Activity",
        ),
    ),
    PlantKindEntry(
        kind="dead_metadata",
        category=PlantCategory.L2FT_HYGIENE,
        family="L2FT Hygiene",
        # Slug mismatch per BU.0 round-4 Notes: registry kind is
        # ``dead_metadata`` (operator-vocabulary, parallels
        # ``missing_metadata_key`` on the opposite direction) but the
        # handbook parser's auto-derived section slug is
        # ``dead_metadata_declarations`` (matches the Markdown title).
        # The bijectivity anti-drift test gates this.
        section_kind="dead_metadata_declarations",
        plant_function=_invoke_dead_metadata_plant,
        primitives=(),
        tour_destination=TourDestination(
            primary_url=(
                "/dashboards/l2_flow_tracing/sheets/l2ft-sheet-l2-exceptions"
            ),
        ),
        dashboard_check=DashboardCheck(
            url_path=(
                "/dashboards/l2_flow_tracing/sheets/l2ft-sheet-l2-exceptions"
            ),
            expect_text_contains="Dead Metadata",
        ),
    ),
    PlantKindEntry(
        kind="dead_limit_schedule",
        category=PlantCategory.L2FT_HYGIENE,
        family="L2FT Hygiene",
        section_kind="dead_limit_schedules",
        plant_function=_invoke_dead_limit_schedule_plant,
        primitives=(),
        tour_destination=TourDestination(
            primary_url=(
                "/dashboards/l2_flow_tracing/sheets/l2ft-sheet-l2-exceptions"
            ),
        ),
        dashboard_check=DashboardCheck(
            url_path=(
                "/dashboards/l2_flow_tracing/sheets/l2ft-sheet-l2-exceptions"
            ),
            expect_text_contains="Dead Limit Schedules",
        ),
    ),
)


# -- Registry helpers -------------------------------------------------------


def get_entry(kind: str) -> PlantKindEntry | None:
    """O(N) lookup — N is small (~21 at full scale)."""
    for entry in PLANT_REGISTRY:
        if entry.kind == kind:
            return entry
    return None


def entries_by_family() -> Mapping[str, tuple[PlantKindEntry, ...]]:
    """Group registry entries by family for the landing accordion."""
    groups: dict[str, list[PlantKindEntry]] = {}
    for entry in PLANT_REGISTRY:
        groups.setdefault(entry.family, []).append(entry)
    return {f: tuple(es) for f, es in groups.items()}


# Field placeholder for future tooling — silences unused-import warnings
# while the registry skeleton is small. Drops in BU.2b once the full
# 21-entry populate lands.
_RESERVED: Final[tuple[object, ...]] = (field,)
