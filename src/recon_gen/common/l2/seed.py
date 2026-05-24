"""Demo-seed primitives for any L2 instance.

Two seed layers compose to produce a full demo's richness:

1. **Baseline (Phase R, ``emit_baseline_seed``).** Walks every
   declared Rail / Chain / TransferTemplate / AggregatingRail in
   the L2 instance and emits a healthy 90-day rolling window of
   "things that are working as intended" — multi-leg transfers,
   bundled child legs, chain firings, opening-balance funding,
   per-account daily-balance materialization with weekend / holiday
   carry-forward (v8.5.4). Materializes ``AccountTemplate``
   instances at runtime so the integrator only has to declare the
   template once in YAML. This layer IS what reproduces "a full
   demo's richness" — the older "plants only" claim no longer
   applies.

2. **Plants (``emit_seed``).** Layered on top of the baseline. Each
   ``ScenarioPlant`` member is a typed dataclass that emits the
   minimum rows needed to surface a specific L1 invariant violation
   (drift / overdraft / limit-breach / stuck-pending / stuck-unbundled
   / supersession / template-cycle / transfer-template) or an
   Investigation-side anomaly (recipient fanout). Plant order is
   sorted by stable keys so ``data hash`` can pin the SHA256.

3. **Composed (``emit_full_seed``).** Concatenates baseline + plants.
   This is the entry point ``data apply`` calls when an integrator
   loads their L2 YAML and seeds the demo DB — full baseline +
   planted exception scenarios in one SQL script.

Loading a scenario via L2 YAML always goes through ``emit_full_seed``
(via ``cli/_helpers.py::build_full_seed_sql``), so the integrator
gets the full richness on every ``data apply``. The auto-derived
plant scenario comes from ``auto_scenario.default_scenario_for``
walking the L2 instance — there's no separate "scenario YAML";
plants fall out of the L2 shape via heuristics.

Public API:

- **Plant dataclasses**: ``TemplateInstance``, ``DriftPlant``,
  ``OverdraftPlant``, ``LimitBreachPlant``, ``StuckPendingPlant``,
  ``StuckUnbundledPlant``, ``SupersessionPlant``,
  ``TransferTemplatePlant``, ``InvFanoutPlant``,
  ``RailFiringPlant``.
- **Container**: ``ScenarioPlant`` (holds ``template_instances`` +
  every plant tuple + a reference ``today`` date).
- **Entry points**:
  - ``emit_seed(instance, scenarios)`` — plants only; deterministic
    output for hash-locking.
  - ``emit_baseline_seed(instance)`` — 90-day healthy baseline only.
  - ``emit_full_seed(instance, scenarios)`` — baseline + plants;
    what ``data apply`` calls.

What this module still deliberately does NOT do:

- Decide *what* plants to add. That's
  ``auto_scenario.default_scenario_for(instance)`` — heuristics that
  walk the L2 shape and pick plant inputs (which Rail to drift,
  which LimitSchedule to breach, etc.).
- Wire dialect-specific schema DDL. That's
  ``schema.emit_schema(instance)``. The seed assumes the schema
  shape ``emit_schema`` produces.
"""

from __future__ import annotations

import random
import zlib
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from recon_gen.common.as_of_frame import AsOfFrame
from recon_gen.common.sql import Dialect

from .primitives import (
    Account,
    AccountTemplate,
    Chain,
    ChainChildSpec,
    Identifier,
    L2Instance,
    Name,
    Period,
    Rail,
    SingleLegRail,
    TransferTemplate,
    TwoLegRail,
)


def _sql_timestamp_literal(iso_8601_str: str, dialect: Dialect) -> str:
    """Format an ISO-8601 timestamp string as a SQL literal per dialect.

    P.9a — both dialects' TIMESTAMP columns are now TZ-naive (the
    ``timestamp_type`` helper returns plain ``TIMESTAMP`` on both),
    so the trailing ``+HH:MM`` / ``Z`` offset on every seed timestamp
    string gets stripped here at the literal-formatter boundary
    (Oracle's plain ``TIMESTAMP`` literal rejects offsets; PG would
    accept then silently drop). Timezone normalization is the
    integrator's contract — see Schema_v6.md.

    PG: bare string literal with the ``T`` separator preserved
    (PG accepts both space + T separators).

    Oracle: typed ``TIMESTAMP 'YYYY-MM-DD HH:MI:SS'`` literal with a
    space separator (not ``T``).

    SQLite: bare ISO-8601 string literal — SQLite has no native
    TIMESTAMP type and stores datetimes as TEXT. The ``date()`` /
    ``datetime()`` / ``julianday()`` functions parse the value at
    read time. Use a space separator (not ``T``) so SQLite's
    datetime parser recognizes the value (``T`` is accepted but the
    space form keeps the seed output visually consistent across
    dialects when read in a SQLite shell).

    The same helper is used for every timestamp the seed emits —
    transactions.posting and daily_balances.business_day_start.
    """
    naive = _strip_tz_offset(iso_8601_str)
    if dialect is Dialect.POSTGRES:
        return "'" + naive.replace("'", "''") + "'"
    if dialect is Dialect.SQLITE:
        # SQLite stores TIMESTAMP as TEXT; ISO-8601 with a space
        # separator is the format ``date()`` / ``datetime()`` /
        # ``julianday()`` recognize unambiguously.
        sqlite_str = naive.replace("T", " ", 1).replace("'", "''")
        return "'" + sqlite_str + "'"
    oracle_str = naive.replace("T", " ", 1).replace("'", "''")
    return f"TIMESTAMP '{oracle_str}'"


def _strip_tz_offset(iso_8601_str: str) -> str:
    """Return the ISO-8601 string with any trailing ``+HH:MM`` / ``Z``
    offset removed. Idempotent on inputs that already lack an offset.
    """
    # Trailing 'Z'.
    if iso_8601_str.endswith("Z"):
        return iso_8601_str[:-1]
    # Trailing offset: scan for the rightmost '+' or '-' that follows
    # a digit and has the shape ``±HH:MM`` or ``±HHMM`` or ``±HH``.
    # The date itself doesn't have a sign in that position, so the
    # rightmost match is unambiguous.
    for sign_pos in range(len(iso_8601_str) - 1, -1, -1):
        ch = iso_8601_str[sign_pos]
        if ch in "+-" and sign_pos > 10:  # past the YYYY-MM-DD prefix
            return iso_8601_str[:sign_pos]
        if ch == "T":
            break
    return iso_8601_str


# -- Public scenario dataclasses ---------------------------------------------


@dataclass(frozen=True, slots=True)
class TemplateInstance:
    """One concrete materialization of an ``AccountTemplate``.

    The L2 instance declares the SHAPE of (e.g.) `CustomerDDA`; this
    record materializes one concrete customer DDA. The integrator's ETL
    is normally responsible for materialization at runtime; for the
    demo seed we declare them inline.
    """

    template_role: Identifier   # e.g. "CustomerDDA"
    account_id: Identifier      # e.g. "cust-900-0001-bigfoot-brews"
    name: Name                  # e.g. "Bigfoot Brews — DDA"


@dataclass(frozen=True, slots=True)
class DriftPlant:
    """A planted (account, business_day) cell where stored balance disagrees
    with computed balance by ``delta_money``.

    Positive delta: stored balance is HIGHER than the sum of postings.
    Negative delta: stored balance is LOWER than the sum of postings.

    Surfaces in the L1 Drift theorem as a non-zero ``Drift`` value for
    that account-day.

    Background postings on the drift day come from ``rail_name`` (a
    declared two-leg Rail in the L2 instance); the counter-leg uses
    ``counter_account_id`` (must be a declared external Account in the
    same instance). Both are resolved from ``instance`` at emit time
    so this dataclass never needs to know about specific persona
    fixtures.
    """

    account_id: Identifier
    days_ago: int
    delta_money: Decimal
    rail_name: Identifier
    counter_account_id: Identifier


@dataclass(frozen=True, slots=True)
class OverdraftPlant:
    """A planted (account, business_day) cell where stored balance is
    negative.

    Surfaces in L1's Non-Negative Stored Balance SHOULD-constraint as
    a violation for that account-day.
    """

    account_id: Identifier
    days_ago: int
    money: Decimal              # MUST be negative


@dataclass(frozen=True, slots=True)
class LimitBreachPlant:
    """A planted (account, business_day, rail) cell where the daily
    outbound flow exceeds the configured Outbound ``LimitSchedule.cap``.

    Surfaces in L1's Limit Breach SHOULD-constraint when
    ``OutboundFlow(account, rail, day) > limit``.

    The breaching debit posts on the customer side; the counter-leg
    uses ``counter_account_id`` (must be a declared external Account
    in the same instance), resolved from ``instance`` at emit time so
    this dataclass never hardcodes a specific persona's counterparty.
    """

    account_id: Identifier
    days_ago: int
    rail_name: Identifier
    amount: Decimal             # absolute value; must exceed the cap
    counter_account_id: Identifier


@dataclass(frozen=True, slots=True)
class InboundCapBreachPlant:
    """A planted (account, business_day, rail) cell where the daily
    inbound flow exceeds the configured Inbound ``LimitSchedule.cap``.

    Mirror of :class:`LimitBreachPlant` for the AB.1 Inbound direction
    — surfaces in L1's Limit Breach SHOULD-constraint when
    ``InboundFlow(account, rail, day) > limit`` (typical AML /
    structuring threshold). The breaching *credit* posts on the
    customer side (money IN); the counter-leg debits the external
    account (the funds-source). Direction column on the matview row
    will read ``'Inbound'`` so the dashboard / audit can distinguish.
    """

    account_id: Identifier
    days_ago: int
    rail_name: Identifier
    amount: Decimal             # absolute value; must exceed the cap
    counter_account_id: Identifier


@dataclass(frozen=True, slots=True)
class TwoTemplateChainPlant:
    """A planted healthy two-template chain firing (AB.2.6).

    Generates one parent leg_rail firing + child template leg_rail
    firings (all sharing one child Transfer per gap doc §3's first
    -firing-wins semantic, all carrying the same ``parent_transfer_id``).
    Cardinality = 1 in the AB.2.3 matview = NO violation row. Gives the
    L1 dashboard's PostedRequirements panel + the audit PDF a healthy
    two-template chain row to display, separate from the probabilistic
    baseline.
    """

    chain_parent_rail_name: Identifier
    child_template_name: Identifier
    days_ago: int


@dataclass(frozen=True, slots=True)
class ChainParentDisagreementPlant:
    """A planted L1 violation: two-template chain where leg_rail firings
    of one child Transfer claim *different* ``parent_transfer_id`` values
    (AB.2.6 / AB.2.3).

    First-firing-wins per gap doc §3 means subsequent legs MUST agree
    on the Parent — disagreement is an ETL bug (parent reference drift,
    cross-cycle contamination). The emitter generates 2+ leg_rail rows
    sharing one ``transfer_id`` + ``template_name`` but assigning
    different synthetic ``transfer_parent_id`` values, so the AB.2.3
    matview reads ``COUNT(DISTINCT parent_transfer_id) > 1`` and
    surfaces the row.
    """

    child_template_name: Identifier
    days_ago: int
    parent_a_transfer_id: str  # synthetic; doesn't need to resolve to a real Transfer
    parent_b_transfer_id: str  # synthetic; the *second* parent that disagrees with A


@dataclass(frozen=True, slots=True)
class XorVariantOverlapPlant:
    """AB.3.5b plant: a TransferTemplate Transfer where TWO members of
    one XOR group both fire — matches the AB.3.3 matview's
    ``firing_count >= 2`` branch.

    Emits two leg_rail rows sharing one ``transfer_id`` + ``template
    _name``, both ``rail_name`` values being members of
    ``target_xor_group_index``. The matview's LEFT JOIN per (transfer,
    group, member_rail) hits twice → ``COUNT(*) = 2`` → ``HAVING <>
    1`` → row surfaces with ``fired_rails='<a>,<b>'``. Pairs with
    ``XorVariantMissedFiringPlant`` so the demo dashboard surfaces
    BOTH branches of the matview's ``firing_count <> 1`` HAVING.

    Picker constraint: target group MUST have ≥2 distinct members.
    Validator C1d already enforces ≥2 at load time, so every declared
    XOR group qualifies. ``variant_a`` and ``variant_b`` MUST be
    distinct members of the targeted group.
    """

    template_name: Identifier
    target_xor_group_index: int
    days_ago: int
    variant_a_rail_name: Identifier
    variant_b_rail_name: Identifier


@dataclass(frozen=True, slots=True)
class XorVariantMissedFiringPlant:
    """AB.3.5 plant: a TransferTemplate Transfer where one XOR group
    has zero firings — matches the AB.3.3 matview's
    ``firing_count = 0`` branch.

    Emits a single ``witness`` leg_rail row carrying ``template_name``
    so the synthetic Transfer enters ``<prefix>_current_transactions``
    (and therefore the matview's ``template_transfers`` universe), but
    NO member of ``target_xor_group_index`` fires for this transfer_id.
    The matview's LEFT JOIN finds zero member-rail firings for
    ``(transfer_id, template, target_xor_group_index)`` →
    ``COUNT(*) = 0`` → ``HAVING <> 1`` → violation row surfaces with
    ``fired_rails=''``.

    Picker constraint: the chosen template MUST have ≥1 leg_rail
    outside the target XOR group, so the witness is real (a synthetic
    sentinel rail_name would be ambiguous — could be confused for an
    undeclared rail). Picker logic in ``_pick_xor_missed_firing_inputs``.
    """

    template_name: Identifier
    target_xor_group_index: int
    days_ago: int
    witness_rail_name: Identifier


@dataclass(frozen=True, slots=True)
class FanInChainPlant:
    """AB.4.5 plant: healthy fan-in chain firing.

    Per AB.4.0 lock: N parent firings share one child Transfer (the
    batched-payout pattern). The healthy case has ``parent_count`` =
    chain's ``expected_parent_count`` (or any value ≥2 when the
    chain leaves ``expected_parent_count`` unset for variable-batch
    flows). The AB.4.7 ``_fan_in_disagreement`` matview reads
    ``parent_count == expected`` (or ``parent_count >= 2`` unset)
    and emits no violation row — purpose is positive demo coverage.
    """

    chain_parent_rail_name: Identifier
    child_template_name: Identifier
    days_ago: int
    parent_count: int


@dataclass(frozen=True, slots=True)
class FanInChainMissingParentPlant:
    """AB.4.5 plant: fan-in batch with parent set SHORT of expected
    (orphan / incomplete).

    Emits ``parent_count`` parent firings (less than the chain's
    ``expected_parent_count``) sharing one child Transfer. The AB.4.7
    matview reads ``parent_count < expected`` and emits a row with
    ``disagreement_kind='missing'`` (or ``'orphan'`` if parent_count
    falls to 1 — the AB.4.0 lock's fallback when expected is unset).
    Models the ETL bug where a parent contribution never lands —
    e.g., one daily settlement of a monthly payout batch failed to
    post but the batch still closed.
    """

    chain_parent_rail_name: Identifier
    child_template_name: Identifier
    days_ago: int
    parent_count: int  # < chain.expected_parent_count


@dataclass(frozen=True, slots=True)
class FanInChainExtraParentPlant:
    """AB.4.5 plant: fan-in batch with parent set EXCEEDING expected.

    Emits ``parent_count`` parent firings (more than the chain's
    ``expected_parent_count``) sharing one child Transfer. The AB.4.7
    matview reads ``parent_count > expected`` and emits a row with
    ``disagreement_kind='extra'``. Models the ETL bug where an
    unrelated parent firing claimed membership in a batch it
    shouldn't have been part of — cross-batch contamination or stale
    parent reference.

    Only meaningful when the chain declares ``expected_parent_count``
    (otherwise the matview has no upper bound to flag against; the
    picker drops this plant when expected is unset).
    """

    chain_parent_rail_name: Identifier
    child_template_name: Identifier
    days_ago: int
    parent_count: int  # > chain.expected_parent_count


@dataclass(frozen=True, slots=True)
class MultiXorMissedPlant:
    """AB.6.6 plant: a chain parent firing with ZERO declared XOR
    siblings firing — matches the AB.6.5 matview's
    ``child_count = 0`` 'missed' branch.

    Models the ETL bug where chain.md's "multi-children = exactly one
    MUST fire" contract was violated: the parent fired but no child
    followed (all XOR alternatives were dropped on the floor). The
    AB.6.5 ``_multi_xor_violation`` matview reads
    ``COUNT(matched_child_name) = 0`` → ``HAVING <> 1`` → row surfaces
    with ``disagreement_kind='missed'``, ``fired_children=''``.

    Picker constraint (AB.6.6): the chain has ≥2 non-fan-in children
    and a Rail (not Template) parent so the plant emitter can
    synthesize a parent firing without nested-firing logic. Mirrors
    AB.2.6's parent-must-be-rail restriction.
    """

    chain_parent_rail_name: Identifier
    days_ago: int


@dataclass(frozen=True, slots=True)
class MultiXorOverlapPlant:
    """AB.6.6 plant: a chain parent firing where TWO declared XOR
    siblings fire — matches the AB.6.5 matview's ``child_count >= 2``
    'overlap' branch.

    Emits one parent firing (the chain.parent rail) plus child legs
    for variant_a + variant_b, both with ``transfer_parent_id`` set
    to the parent's ``transfer_id``. The AB.6.5 matview's
    ``fired_children_distinct`` CTE picks up both → ``COUNT = 2`` →
    ``HAVING <> 1`` → row surfaces with ``disagreement_kind='overlap'``,
    ``fired_children='<a>,<b>'`` (concat ordering dialect-specific).

    Pairs with ``MultiXorMissedPlant`` so the dashboard surfaces BOTH
    branches of the AB.6.5 matview's HAVING clause.
    """

    chain_parent_rail_name: Identifier
    variant_a_child_name: Identifier
    variant_b_child_name: Identifier
    days_ago: int


@dataclass(frozen=True, slots=True)
class StuckPendingPlant:
    """A planted Pending leg whose age exceeds the rail's `max_pending_age`.

    Surfaces in L1's `<prefix>_stuck_pending` view (M.2b.8) when
    `EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - posting)) >
    rail.max_pending_age_seconds`. Pick a rail with a max_pending_age
    set + a `days_ago` value comfortably past the cap.
    """

    account_id: Identifier
    days_ago: int
    rail_name: Identifier
    amount: Decimal


@dataclass(frozen=True, slots=True)
class FailedTransactionPlant:
    """A planted leg with ``status='Failed'`` (X.1.i).

    The L1 schema's ``status`` column is open-set — any string is a
    valid terminal state. The tool reasons explicitly about
    ``Pending`` / ``Posted`` (drives Aging, Conservation,
    Completion); every other status (Failed, Cancelled, Rejected, ...)
    is collapsed to ``Other`` in the L2FT Rails dataset's CASE
    projection so the static dropdown enum matches what the column
    produces.

    Plant a Failed leg per scenario so the dropdown's ``Other``
    option has matching seed rows — the X.1.g per-dropdown e2e test
    asserts every advertised value narrows the table to a non-empty
    subset, and a status enum without a corresponding plant tripped
    the test on first deploy.
    """

    account_id: Identifier
    days_ago: int
    rail_name: Identifier
    amount: Decimal


@dataclass(frozen=True, slots=True)
class StuckUnbundledPlant:
    """A planted Posted leg with `bundle_id IS NULL` whose age exceeds
    the rail's `max_unbundled_age`.

    Surfaces in L1's `<prefix>_stuck_unbundled` view (M.2b.9) when the
    leg's age past `posting` exceeds the per-rail cap. Per validator
    R8, the rail MUST appear in some AggregatingRail's bundles_activity
    — the seed picks a rail that satisfies this.
    """

    account_id: Identifier
    days_ago: int
    rail_name: Identifier
    amount: Decimal


@dataclass(frozen=True, slots=True)
class SupersessionPlant:
    """A planted logical-key (transaction.id) with multiple `entry`
    versions, simulating a TechnicalCorrection rewrite of a posted leg.

    Surfaces in M.2b.12's Supersession Audit detail tables. Emits two
    transaction rows with the same `id`: the first ("original") posts
    `original_amount`; the second ("correction") posts `corrected_amount`
    a few minutes later carrying `supersedes='TechnicalCorrection'`.
    PostgreSQL's BIGSERIAL `entry` column auto-assigns the entry
    versioning, so the second insert lands at a higher entry value.
    """

    account_id: Identifier
    days_ago: int
    rail_name: Identifier
    original_amount: Decimal
    corrected_amount: Decimal


@dataclass(frozen=True, slots=True)
class TransferTemplatePlant:
    """A planted firing of a declared TransferTemplate.

    Plants one shared Transfer (single ``transfer_id``) made up of
    legs whose ``template_name`` points back to the template. Each leg
    carries the same ``transfer_key`` metadata values (per SPEC: "every
    firing of a leg_rails rail with the same transfer_key Metadata
    values posts to the same shared Transfer"); the seed emits
    synthetic values keyed off ``firing_seq`` so two firings of the
    same template don't collapse to one shared Transfer.

    M.3.10g first cut handled only ``TwoLegRail`` first leg_rails
    (debit + credit summing to ``expected_net = 0`` in one firing).
    Extended to also handle ``SingleLegRail`` first leg_rails — emits
    one leg per firing in the rail's ``leg_direction`` (``Variable``
    treated as ``Debit`` for plant purposes; closing-leg semantics
    aren't material to surfacing data on the L2FT TT explorer).
    Single-leg firings surface as 'Imbalanced' against
    ``expected_net = 0`` (one bare leg can't sum to zero) — accurate
    L1 representation of a single-leg cycle without its sibling
    legs. Multi-leg-per-firing SingleLegRail cycles (e.g. on a
    shared transfer_id by transfer_key) are still deferred.

    ``source_account_id`` and ``destination_account_id`` may each be
    either a ``TemplateInstance.account_id`` (a materialized customer)
    OR an L2 ``Account.id`` (a singleton or external counterparty).
    For SingleLegRail templates only ``source_account_id`` is used
    (the leg account); the picker sets ``destination_account_id`` to
    the same value for shape consistency, and the emit helper
    ignores it. The emit helper resolves each at seed time — so a
    customer-DDA→external rail and an external→clearing rail both
    fit this single plant shape.

    ``chain_children`` (M.3.10h) — a tuple of (child_rail_name,
    account_id) pairs pre-resolved by the auto-scenario picker. For
    each pair, the emit helper plants ONE additional child leg whose
    ``rail_name`` is the child + ``transfer_parent_id`` points at this
    plant's shared transfer_id, so the L2 chain detection SQL sees a
    matched child for every declared chain edge. Empty tuple = no
    chain children fire (orphan firing — every declared chain edge
    surfaces as a missing child). The picker mixes these per template
    to exercise both matched + orphan code paths in one seed.
    """

    template_name: Identifier
    days_ago: int
    amount: Decimal
    source_account_id: Identifier
    destination_account_id: Identifier
    firing_seq: int   # 1, 2, ... — disambiguates firings of the same template
    chain_children: tuple[tuple[Identifier, Identifier], ...] = ()


@dataclass(frozen=True, slots=True)
class RailFiringPlant:
    """A planted Posted firing of a single Rail (M.4.2 broad-mode plant kind).

    The L1-invariant plant types only fire rails the auto-scenario picks
    to surface a SHOULD violation (one drift account, one overdraft
    account, one limit-breach pair, etc.). Most declared rails see zero
    firings under that picker — which is *correct* L2-hygiene behavior
    but leaves the L2 Flow Tracing dashboard's Rails / Chains /
    Transfer Templates sheets reading "dead" for every rail the picker
    didn't choose.

    Broad mode (M.4.2) plants additional ordinary firings — no SHOULD
    violation, just "this rail fired, here's the data" — across every
    declared rail whose role(s) actually resolve to a materialized
    account. The L1 surface stays clean (no new drift / overdraft /
    breach rows); the L2 surface gains visible content.

    Two-leg rails plant 2 legs (debit on ``account_id_a``, credit on
    ``account_id_b``); single-leg rails plant 1 leg (on ``account_id_a``,
    direction per ``Rail.leg_direction``). ``account_id_b`` is None for
    single-leg rails.

    ``transfer_parent_id`` is set when this firing is the child end of
    a Required chain entry — points at one of the parent rail's
    ``transfer_id``s so the L1 invariant view's chain-orphan detection
    sees a matched pair. Defaults to None for standalone firings.

    ``extra_metadata`` carries values for rail.metadata_keys fields NOT
    auto-derived from a containing TransferTemplate's transfer_key.
    The emit helper unions them with auto-derived TransferKey values
    so the resulting JSON column is well-formed for the L2 Flow Tracing
    metadata cascade.

    ``template_name`` (M.4.2a) is set when this firing's rail is a
    ``leg_rails`` entry of some TransferTemplate — the L2 Flow Tracing
    ``tt-instances`` + ``tt-legs`` datasets read rows by
    ``template_name``, so leg-rail broad firings need this field
    populated to surface on the Transfer Templates sheet alongside
    the structured ``TransferTemplatePlant`` firings. ``None`` for
    standalone rails (most of them).
    """

    rail_name: Identifier
    days_ago: int
    firing_seq: int
    amount: Decimal
    account_id_a: Identifier
    account_id_b: Identifier | None = None
    transfer_parent_id: str | None = None
    extra_metadata: tuple[tuple[str, str], ...] = ()
    template_name: Identifier | None = None


@dataclass(frozen=True, slots=True)
class InvFanoutPlant:
    """A planted "fanout" — N senders all credit ONE leaf-internal
    recipient on the same day (N.4.h, fuzzer Investigation coverage).

    Drives the Investigation matview surface (N.3.b):
    - ``<prefix>_inv_pair_rolling_anomalies`` — N (sender, recipient,
      day) pair-rolling rows; the recipient survives the matview's
      ``account_scope='internal' AND account_parent_role IS NOT NULL``
      filter so the rolling-window aggregation has data to operate on.
    - ``<prefix>_inv_money_trail_edges`` — N depth-0 (root) edges from
      sender → recipient via the recursive-CTE walk over
      ``transfer_parent_id``.

    Each "transfer" is a 2-leg multi-leg event (debit on sender +
    credit on recipient summing to zero) so the matview's
    ``signed_amount`` JOIN finds matched legs. ``rail_name`` is one
    declared rail (Z.B 2026-05-15: rail name IS the type identifier
    after the symmetric collapse).

    Recipient MUST resolve to a leaf-internal account (a
    ``TemplateInstance`` materialized from an ``AccountTemplate`` with
    a non-NULL ``parent_role``) — the matview's recipient-side filter
    requires it. Senders MAY be external counterparties or singleton
    internals; the emitter denormalizes their account fields onto the
    sender legs without further validation.
    """

    recipient_account_id: Identifier
    sender_account_ids: tuple[Identifier, ...]
    days_ago: int
    rail_name: Identifier
    amount_per_transfer: Decimal


@dataclass(frozen=True, slots=True)
class ScenarioPlant:
    """The full set of planted scenarios + materialized template instances.

    Defaults to today (UTC midnight) as the reference date; ``days_ago``
    on each plant subtracts from this.
    """

    template_instances: tuple[TemplateInstance, ...]
    drift_plants: tuple[DriftPlant, ...] = ()
    overdraft_plants: tuple[OverdraftPlant, ...] = ()
    limit_breach_plants: tuple[LimitBreachPlant, ...] = ()
    inbound_cap_breach_plants: tuple[InboundCapBreachPlant, ...] = ()
    two_template_chain_plants: tuple[TwoTemplateChainPlant, ...] = ()
    chain_parent_disagreement_plants: tuple[ChainParentDisagreementPlant, ...] = ()
    xor_variant_missed_firing_plants: tuple[XorVariantMissedFiringPlant, ...] = ()
    xor_variant_overlap_plants: tuple[XorVariantOverlapPlant, ...] = ()
    fan_in_chain_plants: tuple[FanInChainPlant, ...] = ()
    fan_in_chain_missing_parent_plants: tuple[FanInChainMissingParentPlant, ...] = ()
    fan_in_chain_extra_parent_plants: tuple[FanInChainExtraParentPlant, ...] = ()
    multi_xor_missed_plants: tuple[MultiXorMissedPlant, ...] = ()
    multi_xor_overlap_plants: tuple[MultiXorOverlapPlant, ...] = ()
    stuck_pending_plants: tuple[StuckPendingPlant, ...] = ()
    failed_transaction_plants: tuple[FailedTransactionPlant, ...] = ()
    stuck_unbundled_plants: tuple[StuckUnbundledPlant, ...] = ()
    supersession_plants: tuple[SupersessionPlant, ...] = ()
    transfer_template_plants: tuple[TransferTemplatePlant, ...] = ()
    rail_firing_plants: tuple[RailFiringPlant, ...] = ()
    inv_fanout_plants: tuple[InvFanoutPlant, ...] = ()
    today: date = field(
        # AQ.3 funnel: defaults route through AsOfFrame.live() — the sole
        # blessed wall-clock site. Locked seeds + tests always override.
        default_factory=lambda: AsOfFrame.live().as_of,
    )


# -- Public emit_seed --------------------------------------------------------


def emit_seed(
    instance: L2Instance,
    scenarios: ScenarioPlant,
    *,
    prefix: str,
    dialect: Dialect = Dialect.POSTGRES,
) -> str:
    """Emit the full SQL INSERT script for the planted scenarios.

    The output is a single SQL string ready for
    ``psycopg2.cursor.execute`` (Postgres) or for the per-statement
    runner in ``cli._execute_script`` (Oracle, via oracledb's
    cursor.execute). Scenarios are emitted in deterministic order
    (sorted by account_id then days_ago) so the per-dialect hash-lock
    can pin the output bytes.

    P.5.b — emits **one INSERT per row**, terminated with ``;``. Both
    PG and Oracle accept this form. Multi-row ``INSERT INTO foo
    VALUES (...), (...)`` (the M.2 PG-only form) is unsupported on
    Oracle (which uses ``INSERT ALL`` instead); per-row INSERT is
    the simpler portability choice and the perf cost is negligible
    for the demo's ~few-hundred-row scale.

    Z.C — ``prefix`` is the cfg.db_table_prefix.
    """
    template_by_role = {t.role: t for t in instance.account_templates}
    parent_singleton_by_role = _parent_singletons(instance)

    # -- Build transaction rows --
    txn_rows: list[str] = []
    txn_counter = _Counter(start=1)

    # Each scenario plant emits its own rows; sort first for determinism.
    for p in sorted(scenarios.limit_breach_plants, key=_breach_key):
        txn_rows.extend(
            _emit_limit_breach_rows(
                p, instance, scenarios, template_by_role,
                parent_singleton_by_role, txn_counter, dialect,
            )
        )

    # AB.1 — Inbound cap breaches mirror Outbound: customer-side
    # CREDIT leg breaches the InboundFlow cap, external counter-leg
    # debits the funds source.
    for p in sorted(scenarios.inbound_cap_breach_plants, key=_inbound_breach_key):
        txn_rows.extend(
            _emit_inbound_cap_breach_rows(
                p, instance, scenarios, template_by_role,
                parent_singleton_by_role, txn_counter, dialect,
            )
        )

    # AB.2.6 — TwoTemplateChainPlant: healthy two-template chain firing
    # with cardinality=1 on the AB.2.3 chain_parent_disagreement
    # matview (no violation row produced).
    for p in sorted(scenarios.two_template_chain_plants, key=_two_template_chain_key):
        txn_rows.extend(
            _emit_two_template_chain_rows(
                p, instance, scenarios, template_by_role, txn_counter, dialect,
            )
        )

    # AB.2.6 — ChainParentDisagreementPlant: child Transfer with 2
    # distinct parent_transfer_id values across its leg_rail firings.
    # Surfaces on the AB.2.3 chain_parent_disagreement matview as an
    # ETL-bug L1 violation.
    for p in sorted(
        scenarios.chain_parent_disagreement_plants,
        key=_chain_parent_disagreement_key,
    ):
        txn_rows.extend(
            _emit_chain_parent_disagreement_rows(
                p, instance, scenarios, template_by_role, txn_counter, dialect,
            )
        )

    # AB.3.5 — XorVariantMissedFiringPlant: synthetic Transfer where
    # one XOR group has zero firings. Surfaces on the AB.3.3
    # xor_group_violation matview with firing_count=0.
    for p in sorted(
        scenarios.xor_variant_missed_firing_plants,
        key=_xor_missed_firing_key,
    ):
        txn_rows.extend(
            _emit_xor_variant_missed_firing_rows(
                p, instance, scenarios, template_by_role, txn_counter, dialect,
            )
        )

    # AB.3.5b — XorVariantOverlapPlant: synthetic Transfer where two
    # members of one XOR group both fire. Surfaces on the AB.3.3
    # xor_group_violation matview with firing_count>=2.
    for p in sorted(
        scenarios.xor_variant_overlap_plants,
        key=_xor_overlap_key,
    ):
        txn_rows.extend(
            _emit_xor_variant_overlap_rows(
                p, instance, scenarios, template_by_role, txn_counter, dialect,
            )
        )

    # AB.4.5 — FanInChainPlant: healthy fan-in firing (parent_count =
    # chain's expected_parent_count). No matview row.
    for fip in sorted(scenarios.fan_in_chain_plants, key=_fan_in_chain_key):
        txn_rows.extend(
            _emit_fan_in_chain_plant_rows(
                fip.chain_parent_rail_name,
                fip.child_template_name,
                fip.days_ago,
                fip.parent_count,
                plant_tag="fanin-h",
                instance=instance,
                scenarios=scenarios,
                template_by_role=template_by_role,
                counter=txn_counter,
                dialect=dialect,
            )
        )

    # AB.4.5 — FanInChainMissingParentPlant: parent_count < expected.
    # AB.4.7 matview reads disagreement_kind='missing' (or 'orphan').
    for mp in sorted(
        scenarios.fan_in_chain_missing_parent_plants,
        key=_fan_in_missing_parent_key,
    ):
        txn_rows.extend(
            _emit_fan_in_chain_plant_rows(
                mp.chain_parent_rail_name,
                mp.child_template_name,
                mp.days_ago,
                mp.parent_count,
                plant_tag="fanin-m",
                instance=instance,
                scenarios=scenarios,
                template_by_role=template_by_role,
                counter=txn_counter,
                dialect=dialect,
            )
        )

    # AB.4.5 — FanInChainExtraParentPlant: parent_count > expected.
    # AB.4.7 matview reads disagreement_kind='extra'.
    for xp in sorted(
        scenarios.fan_in_chain_extra_parent_plants,
        key=_fan_in_extra_parent_key,
    ):
        txn_rows.extend(
            _emit_fan_in_chain_plant_rows(
                xp.chain_parent_rail_name,
                xp.child_template_name,
                xp.days_ago,
                xp.parent_count,
                plant_tag="fanin-x",
                instance=instance,
                scenarios=scenarios,
                template_by_role=template_by_role,
                counter=txn_counter,
                dialect=dialect,
            )
        )

    # AB.6.6 — MultiXorMissedPlant: parent firing with zero declared
    # XOR siblings firing. Surfaces on the AB.6.5 multi_xor_violation
    # matview with disagreement_kind='missed'.
    for mxm in sorted(
        scenarios.multi_xor_missed_plants,
        key=_multi_xor_missed_key,
    ):
        txn_rows.extend(
            _emit_multi_xor_missed_rows(
                mxm, instance, scenarios, template_by_role, txn_counter, dialect,
            )
        )

    # AB.6.6 — MultiXorOverlapPlant: parent firing with TWO declared
    # XOR siblings firing. Surfaces on the AB.6.5 multi_xor_violation
    # matview with disagreement_kind='overlap'.
    for mxo in sorted(
        scenarios.multi_xor_overlap_plants,
        key=_multi_xor_overlap_key,
    ):
        txn_rows.extend(
            _emit_multi_xor_overlap_rows(
                mxo, instance, scenarios, template_by_role, txn_counter, dialect,
            )
        )

    for p in sorted(scenarios.drift_plants, key=_drift_key):
        txn_rows.extend(
            _emit_drift_background_rows(
                p, instance, scenarios, template_by_role,
                parent_singleton_by_role, txn_counter, dialect,
            )
        )

    for p in sorted(scenarios.stuck_pending_plants, key=_stuck_pending_key):
        txn_rows.extend(
            _emit_stuck_pending_rows(
                p, scenarios, template_by_role, txn_counter, dialect,
            )
        )

    for p in sorted(scenarios.failed_transaction_plants, key=_failed_transaction_key):
        txn_rows.extend(
            _emit_failed_transaction_rows(
                p, scenarios, template_by_role, txn_counter, dialect,
            )
        )

    for p in sorted(scenarios.stuck_unbundled_plants, key=_stuck_unbundled_key):
        txn_rows.extend(
            _emit_stuck_unbundled_rows(
                p, scenarios, template_by_role, txn_counter, dialect,
            )
        )

    for p in sorted(scenarios.supersession_plants, key=_supersession_key):
        txn_rows.extend(
            _emit_supersession_rows(
                p, scenarios, template_by_role, txn_counter, dialect,
            )
        )

    for p in sorted(scenarios.transfer_template_plants, key=_tt_key):
        txn_rows.extend(
            _emit_transfer_template_rows(
                p, instance, scenarios, template_by_role, txn_counter,
                dialect,
            )
        )

    for p in sorted(scenarios.rail_firing_plants, key=_rail_firing_key):
        txn_rows.extend(
            _emit_rail_firing_rows(
                p, instance, scenarios, template_by_role, txn_counter,
                dialect,
            )
        )

    for p in sorted(scenarios.inv_fanout_plants, key=_inv_fanout_key):
        txn_rows.extend(
            _emit_inv_fanout_rows(
                p, instance, scenarios, template_by_role, txn_counter,
                dialect,
            )
        )

    # Overdraft scenarios don't need extra transaction rows — the
    # daily_balances row alone (negative money) drives the exception.

    # -- Build daily_balances rows --
    #
    # Each scenario plant emits its own daily_balances row at its plant
    # day. We deliberately do NOT emit a baseline daily_balance for
    # "today" — under L1 SPEC, ComputedBalance is cumulative through
    # business_day_end (sum of ALL Posted transactions, not same-day),
    # so a $0 baseline today against any account with planted
    # transactions would surface a spurious drift row. Accounts without
    # planted transactions (context-only template instances) get NO
    # daily_balance row — they're invisible to the drift / overdraft /
    # expected_eod views, which is the correct semantic.
    db_rows: list[str] = []

    role_offsets = instance.role_business_day_offsets
    for p in sorted(scenarios.drift_plants, key=_drift_key):
        db_rows.append(
            _emit_drift_balance_row(
                p, scenarios, template_by_role, role_offsets, dialect,
            )
        )

    for p in sorted(scenarios.overdraft_plants, key=_overdraft_key):
        db_rows.append(
            _emit_overdraft_balance_row(
                p, scenarios, template_by_role, role_offsets, dialect,
            )
        )

    # Per-row INSERTs (one per row, terminated with ``;``). Both PG +
    # Oracle accept this form; PG's multi-row VALUES (...) , (...) is
    # unsupported on Oracle. The earlier multi-row form was an M.2 PG-
    # only optimization; per-row is the simpler portability choice.
    txn_cols = (
        "(id, account_id, account_name, account_role, account_scope, "
        "account_parent_role, amount_money, amount_direction, status, "
        "posting, transfer_id, transfer_completion, "
        "transfer_parent_id, rail_name, template_name, bundle_id, "
        "supersedes, origin, metadata)"
    )
    txn_insert = (
        "\n".join(
            f"INSERT INTO {prefix}_transactions {txn_cols} VALUES\n  {row};"
            for row in txn_rows
        )
    ) if txn_rows else "-- (no transactions planted)"

    db_cols = (
        "(account_id, account_name, account_role, account_scope, "
        "account_parent_role, expected_eod_balance, business_day_start, "
        "business_day_end, money, metadata, supersedes)"
    )
    db_insert = (
        "\n".join(
            f"INSERT INTO {prefix}_daily_balances {db_cols} VALUES\n  {row};"
            for row in db_rows
        )
    ) if db_rows else "-- (no daily_balances planted)"

    return f"""\
-- =====================================================================
-- L2 instance: {prefix} — demo seed
-- Generated by recon_gen.common.l2.seed.emit_seed
-- Reference date: {scenarios.today.isoformat()}
-- Plants:
--   {len(scenarios.template_instances)} template instances
--   {len(scenarios.drift_plants)} drift scenarios
--   {len(scenarios.overdraft_plants)} overdraft scenarios
--   {len(scenarios.limit_breach_plants)} limit-breach scenarios (Outbound)
--   {len(scenarios.inbound_cap_breach_plants)} limit-breach scenarios (Inbound, AB.1)
--   {len(scenarios.rail_firing_plants)} rail firings (broad mode)
-- =====================================================================

{txn_insert}

{db_insert}
"""


# -- Public emit_baseline_seed (Phase R) -------------------------------------
#
# Companion to ``emit_seed`` above. Where ``emit_seed`` plants a small set of
# scenario-driven anomalies, ``emit_baseline_seed`` produces a 3-month
# healthy baseline of hundreds-to-thousands of leg rows per Rail. Phase R's
# dashboards then have realistic exception signal sitting in realistic noise.
#
# R.1.f spec is the design doc — see PLAN.md "R.1.f spec — Generator output
# shape". Headline numbers (volume per Rail, lognormal amount distribution,
# RNG sub-stream layout, account starting balances, multi-leg + chain
# ordering) all come from there. The R.7.e backlog item lifts the spec out
# of PLAN.md into a docs-site reference page once the implementation
# stabilizes.
#
# Implementation lands in steps R.2.a (this skeleton) → R.2.e:
#   - R.2.a (this commit): entry point + helper signatures + RNG layout +
#     business-day calendar + classification table; emits a valid SQL
#     header with empty INSERT bodies.
#   - R.2.b: per-Rail leg loop; volume heuristic + lognormal amount sampler
#     + time-of-day distribution + account-balance state machine.
#   - R.2.c: multi-leg transfer assembly (single-leg, two-leg, aggregating
#     children-first then EOD/EOM bundling parent).
#   - R.2.d: chain firings (Required ~95% completion, Optional ~50%).
#   - R.2.e: daily-balance materialization for every (account, business_day).
#


# Per R.1.f §4: same constant the existing test_demo_data.py uses, for
# legacy hash continuity. Per-Rail RNG is BASE ^ crc32(rail_name); the XOR
# guarantees each Rail's stream is independent of every other Rail's even
# when one Rail is renamed.
_BASELINE_BASE_SEED = 42

# X.4.h.6 — the rolling-window length (in calendar days) the baseline
# generator emits legs for, anchored at ``today``. Public so consumers
# (the Studio trainer-mode timeline UI) can render the same window the
# generator uses without duplicating the literal. ``emit_baseline_seed``
# accepts a ``window_days`` override; this is the default.
DEFAULT_BASELINE_WINDOW_DAYS = 90


def _seed_for_rail(
    rail_name: Identifier | str,
    base_seed: int = _BASELINE_BASE_SEED,
) -> int:
    """Return the per-Rail RNG seed for the baseline emitter (R.1.f §4).

    Threading one ``random.Random(_seed_for_rail(rail.name))`` instance
    through every helper that touches a given Rail keeps the per-Rail
    streams isolated — renaming or removing one Rail can't perturb another
    Rail's emitted bytes. Cross-Rail randomness (account picks, starting
    balances) uses a separate ``random.Random(base_seed)`` instance.

    ``base_seed`` (X.4.h.0.b) lets the data-shaping panel override the
    root seed; default keeps the legacy hash-lock continuity.
    """
    return base_seed ^ (
        zlib.crc32(str(rail_name).encode("utf-8")) & 0xFFFFFFFF
    )


@dataclass(slots=True)
class _BaselineState:
    """Mutable state threaded through the baseline emission loop.

    Carries the immutable window context (anchor + business-day calendar)
    + the running account balance state machine the per-Rail leg loop
    walks through. Per-(account, day) closing balances accumulate here so
    R.2.e can materialize ``daily_balances`` without re-walking the legs.

    The ``balances`` map seeds at generator init from R.1.f §5's per-role
    starting-balance distribution. Each Rail leg the emitter posts updates
    the relevant account's running balance; at end-of-day every account
    that posted at least one leg snapshots its closing balance into
    ``eod_balances``.
    """

    anchor: date
    window_days: int
    business_days: tuple[date, ...]
    # Materialized template-instance accounts the per-Rail loop draws
    # source/destination accounts from. Populated at generator init by
    # ``_materialize_baseline_template_instances``.
    template_instances: tuple[TemplateInstance, ...] = ()
    # account_id -> running signed cumulative balance (positive = money in).
    balances: dict[Identifier, Decimal] = field(
        default_factory=lambda: {},
    )
    # (account_id, business_day) -> closing balance at the end of that day.
    eod_balances: dict[tuple[Identifier, date], Decimal] = field(
        default_factory=lambda: {},
    )
    # (child_rail_name, business_day) -> aggregating-rail bundle_id for
    # any child leg posted on that day (R.2.c). Pre-computed before the
    # per-Rail leg loop so the leg emitter can stamp bundle_id at emit
    # time without a second pass. Keys absent from the map → bundle_id
    # stays NULL (unbundled — caught by L1's stuck_unbundled view if the
    # rail's max_unbundled_age elapses).
    bundle_map: dict[tuple[Identifier, date], str] = field(
        default_factory=lambda: {},
    )
    # Per-Rail firing log (R.2.d). Each entry is (transfer_id,
    # business_day, amount) — the chain-firing pass uses this to pick
    # parent firings to attach children to. Populated by both the
    # per-Rail leg loop (R.2.b) and the aggregating-rail emitter (R.2.c).
    firings: dict[Identifier, list[tuple[str, date, Decimal]]] = field(
        default_factory=lambda: {},
    )
    # Per-account leg log (post-R.2.e fix). Each entry is
    # (posting_iso, business_day, signed_amount). After all rails +
    # chains have emitted, the daily-balance materializer walks this
    # log sorted by posting and computes the correct cumulative EOD
    # balance per (account, day). Per-leg `eod_balances` snapshots in
    # the leg-emit sites would over-write each other when rails iterate
    # in name order across all days (rail B's day-1 leg snapshots a
    # state that already includes rail A's day-1-through-N
    # contributions); the deferred-walk fix avoids that bug.
    account_leg_log: dict[
        Identifier, list[tuple[str, date, Decimal]]
    ] = field(default_factory=lambda: {})
    # Snapshot of `balances` taken right after _initialize_starting_balances
    # populates them. The deferred daily-balance walk reads from here as
    # the per-account starting point; the running `balances` dict has been
    # mutated by the leg loop and no longer reflects the starting state.
    initial_balances: dict[Identifier, Decimal] = field(
        default_factory=lambda: {},
    )
    # V.5.a — per-(source-account, transfer_type, business_day) outbound
    # accumulator used by the leg loop to enforce the L1 limit_breach
    # invariant against LimitSchedule caps. The matview groups by
    # (account_id, business_day, transfer_type) on Posted Debit legs and
    # flags rows where SUM(ABS(amount_money)) > cap. The amount sampler's
    # per-firing clamp is necessary but not sufficient — multiple firings
    # against the same source on the same day must collectively stay under
    # the daily cap. The leg loop reads `remaining_cap = cap - accumulated`
    # before each firing, skips when `remaining_cap < $50` (avoids comically
    # small firings), and falls through to `_baseline_amount_sample` with
    # `cap=remaining_cap` otherwise. Aggregating rails are out of scope
    # here — they post one parent per period, the cap-vs-aggregate
    # invariant doesn't apply.
    daily_outbound_by_account_type: dict[
        tuple[Identifier, str, date], Decimal,
    ] = field(default_factory=lambda: {})


def emit_baseline_seed(
    instance: L2Instance,
    *,
    prefix: str,
    window_days: int = 90,
    anchor: date | None = None,
    dialect: Dialect = Dialect.POSTGRES,
    skip_rails: frozenset[Identifier] = frozenset(),
    only_rails: frozenset[Identifier] | None = None,
    base_seed: int | None = None,
) -> str:
    """Emit a 3-month healthy-baseline INSERT script for the L2 instance.

    Output shape mirrors ``emit_seed``: one SQL string ready for
    ``psycopg2.cursor.execute`` (Postgres) or ``cli._execute_script``
    (Oracle). The script targets the same ``<prefix>_transactions`` +
    ``<prefix>_daily_balances`` tables the schema emitter creates.

    Args:
      instance: the L2 model instance — every Rail / Chain / TransferTemplate
        / LimitSchedule it declares becomes runtime evidence in the seed.
      window_days: rolling window length (default 90 days). Generator emits
        legs for every business day in ``[anchor - window_days, anchor]``.
      anchor: the "today" date the rolling window ends on. Defaults to UTC
        ``datetime.now().date()`` at call time. Pin a specific anchor in
        tests to keep the SHA256 hash-lock deterministic across runs.
      dialect: SQL dialect for timestamp literals + INSERT shape (PG vs
        Oracle). Same flag the legacy ``emit_seed`` accepts.
      skip_rails: X.4.g.10 — rail names to skip in the per-rail leg
        loop. Used by the deploy pipeline's `scope: uncovered_rails`
        mode to fill baseline only for rails the operator's external
        DB hasn't already populated. Default empty (no rails skipped)
        keeps byte-identical-to-locked-seeds output.
      only_rails: X.4.i.1 — inverse of ``skip_rails``. When set, ONLY
        rails whose name appears in the set are emitted; everything
        else is silently skipped. Used by the deploy pipeline's
        ``scope: only_template`` mode to emit baseline restricted to
        the template's leg-rails dependency closure. ``None`` (default)
        means "no narrowing" (preserves locked-seed byte-identity).
        Mutually exclusive with ``skip_rails`` in spirit but tested
        independently — if the caller passes both, the rail must
        survive both filters (in ``only_rails`` AND not in ``skip_rails``).
      base_seed: X.4.h.0.b — root RNG seed for the baseline emitter.
        ``None`` (default) uses ``_BASELINE_BASE_SEED = 42`` — the
        legacy constant the locked seeds were generated against, so
        the absent-arg case stays byte-identical. Studio's data-shaping
        panel writes ``cfg.test_generator.seed`` here when the trainer
        scrubs to a different layout (different seed → different plant
        positions across days, same seed → byte-identical output).
        Per-rail RNGs derive from this via the existing
        ``_seed_for_rail(rail) = base_seed ^ crc32(rail_name)`` rule
        (rename-resilient per-rail isolation preserved).

    Returns:
      A SQL script string. R.2.a (this commit) returns a valid header +
      empty INSERT bodies; R.2.b–e fill in the per-Rail legs, chains, and
      daily-balance rows.
    """
    effective_base_seed = (
        _BASELINE_BASE_SEED if base_seed is None else int(base_seed)
    )
    if anchor is None:
        # AQ.3 funnel: ad-hoc-run fallback routes through AsOfFrame.live()
        # — the sole blessed wall-clock site. Locked seeds + tests always
        # pass anchor=LOCKED_ANCHOR.
        anchor = AsOfFrame.live().as_of

    template_by_role = {t.role: t for t in instance.account_templates}

    business_days = _business_days_in_window(anchor, window_days)

    # Materialize per-template baseline accounts (R.2.b §1). Customer-DDA
    # templates get 20 instances; merchant + others get 5. The leg loop
    # picks source/destination accounts from this set per Rail.
    template_instances = _materialize_baseline_template_instances(
        instance, template_by_role,
    )

    state = _BaselineState(
        anchor=anchor,
        window_days=window_days,
        business_days=tuple(business_days),
        template_instances=template_instances,
    )

    # Per R.1.f §5 — sample starting balances per account_role from the
    # per-role lognormal table. Cross-Rail randomness (account picks +
    # starting balances) uses a single shared RNG keyed off the base seed.
    #
    # IMPORTANT: state.balances gets non-zero starting amounts, but
    # state.initial_balances is left EMPTY for the daily-balance walk.
    # The L1 drift matview computes ``stored - SUM(signed_amount)`` and
    # treats starting balance as zero — every account that doesn't have
    # an "opening balance" transaction MUST start at zero in the
    # daily_balances output to avoid false-positive drifts. The lognormal
    # starting balances live on for any future feature that wants them
    # (e.g., overdraft thresholds keyed off starting balance).
    init_rng = random.Random(effective_base_seed)
    _initialize_starting_balances(state, instance, template_by_role, init_rng)

    # Pre-compute the bundle map (R.2.c). For every aggregating Rail,
    # walk its bundles_activity refs and assign a deterministic bundle_id
    # for each (child_rail, business_day) tuple. The per-Rail leg loop
    # then stamps bundle_id at emit time so child rows land bundled out
    # of the gate (no need for a second supersession pass).
    _populate_bundle_map(state, instance)

    # Opening-balance pass: emit one funding transaction per template
    # instance with a non-zero starting balance from R.1.f §5. Without
    # this, customers start at zero in the cumulative-from-the-window-
    # start daily-balance walk and the first few business days
    # mechanically overdraft when outbounds happen before inbounds
    # accumulate. Opening transactions land at the very start of the
    # window so subsequent activity stays positive on average.
    opening_counter = _Counter(start=1)
    opening_rows = _emit_opening_balance_rows(
        instance, state, template_by_role, opening_counter, dialect,
    )

    # Per-Rail emission loop. Sort by name so SHA256 hash-lock stays
    # deterministic (Python's set/dict iteration order is insertion-ordered
    # but L2Instance.rails is already a tuple; sort defensively).
    # Non-aggregating rails go through _emit_baseline_for_rail (R.2.b);
    # aggregating rails go through _emit_baseline_for_aggregating_rail
    # (R.2.c) which knows about the children-first / EOD-bundling pattern.
    txn_rows: list[str] = list(opening_rows)
    txn_counter = _Counter(start=1)
    unit_firing_legs = _unit_firing_leg_rails(instance)
    for rail in sorted(instance.rails, key=lambda r: str(r.name)):
        # X.4.g.10 — operator's external data already covers this rail;
        # skip its baseline emit so we don't duplicate transactions.
        # state.firings stays empty for this rail, so chains / cascade
        # credits / daily balances naturally produce nothing for it
        # downstream too.
        if rail.name in skip_rails:
            continue
        # X.4.i.1 — only_template mode narrows the per-rail loop to the
        # template's dependency closure. Same downstream zero-firings
        # behavior for excluded rails as the skip_rails branch above.
        if only_rails is not None and rail.name not in only_rails:
            continue
        # AJ.4b — the internal balance-maintenance rail is a label-only
        # rail for cascade/opening scaffolding; it never fires on its own
        # (an independent firing would pump the clearing GL it credits and
        # show false drift). Its only legs come from the cascade + opening
        # emitters, which tag it explicitly.
        if str(rail.name) == _BALANCE_MAINTENANCE_RAIL:
            continue
        # AL (Gap J): leg_rails of a template that declares template-level
        # E8 fire ONLY as the balanced unit in _emit_baseline_template_
        # firings — firing them standalone here too would double-emit +
        # uncouple the legs (false drift / ignored E8 band). Gated on
        # template-E8 alone, NOT chain-parenthood: a chain-parent template
        # without E8 has INDEPENDENT legs that must keep firing here (Gap J
        # follow-up, v11.9.3). Operator skip_rails / only_rails handled above.
        if rail.name in unit_firing_legs:
            continue
        rail_rng = random.Random(_seed_for_rail(rail.name, effective_base_seed))
        if rail.aggregating:
            rail_rows = _emit_baseline_for_aggregating_rail(
                rail, instance, state, template_by_role,
                rail_rng, txn_counter, dialect,
            )
        else:
            rail_rows = _emit_baseline_for_rail(
                rail, instance, state, template_by_role,
                rail_rng, txn_counter, dialect,
            )
        txn_rows.extend(rail_rows)

    # AG.1 (Gap B): synthesize Template-parent firings BEFORE the chain
    # overlay. For every TransferTemplate referenced as a Chain's
    # `parent`, allocate firings (one per business day, shared
    # transfer_id, template_name stamped) and record under the template
    # name in state.firings so the chain overlay below picks them up
    # as parent firings. Pre-fix: Template-parent chains silently
    # emitted zero rows (state.firings[template] was always empty),
    # breaking chain_parent_disagreement + L2FT chain_orphans for these
    # shapes.
    template_firing_rows = _emit_baseline_template_firings(
        instance, state, txn_counter, dialect,
        base_seed=effective_base_seed,
        skip_rails=skip_rails, only_rails=only_rails,
    )
    txn_rows.extend(template_firing_rows)

    # Chain firings overlay (R.2.d). For every Chain the L2 declares,
    # emit matching parent + child legs at the declared completion rate.
    chain_rows = _emit_baseline_chains(
        instance, state, template_by_role, txn_counter, dialect,
        base_seed=effective_base_seed,
    )
    txn_rows.extend(chain_rows)

    # V.5.b — Cascade credits for intermediate clearing accounts. After
    # rails + chains have populated state.firings, walk the cascade
    # patterns (aggregating-rail bundled-child cascades, TT single-leg
    # variable cascades, MerchantPayout cascade, ZBA sub-account
    # funding) and emit paired credit legs so the cumulative-from-
    # window-start balance walk doesn't mechanically drive these
    # accounts negative once their starting cushion is exhausted.
    # Updates state.account_leg_log so the daily-balance materializer
    # picks up the cascade credits.
    cascade_rows = _emit_baseline_cascade_credits(
        instance, state, txn_counter, dialect,
        base_seed=effective_base_seed,
    )
    txn_rows.extend(cascade_rows)

    # Daily-balance materialization (R.2.e). For every (account,
    # business_day) where the leg loop touched the account, snapshot the
    # closing balance into ``daily_balances``. Drift matview sees
    # ``stored - SUM(signed_amount) = 0`` for the baseline (only R.3 plants
    # introduce drift on top).
    db_rows = _emit_baseline_daily_balances(
        state, instance, template_by_role, dialect,
    )

    txn_cols = (
        "(id, account_id, account_name, account_role, account_scope, "
        "account_parent_role, amount_money, amount_direction, status, "
        "posting, transfer_id, transfer_completion, "
        "transfer_parent_id, rail_name, template_name, bundle_id, "
        "supersedes, origin, metadata)"
    )
    txn_insert = (
        "\n".join(
            f"INSERT INTO {prefix}_transactions {txn_cols} VALUES\n  {row};"
            for row in txn_rows
        )
    ) if txn_rows else "-- (no baseline transactions yet — R.2.b in progress)"

    db_cols = (
        "(account_id, account_name, account_role, account_scope, "
        "account_parent_role, expected_eod_balance, business_day_start, "
        "business_day_end, money, metadata, supersedes)"
    )
    db_insert = (
        "\n".join(
            f"INSERT INTO {prefix}_daily_balances {db_cols} VALUES\n  {row};"
            for row in db_rows
        )
    ) if db_rows else "-- (no baseline daily_balances yet — R.2.e in progress)"

    return f"""\
-- =====================================================================
-- L2 instance: {prefix} — Phase R healthy baseline seed
-- Generated by recon_gen.common.l2.seed.emit_baseline_seed
-- Anchor: {anchor.isoformat()}  ({window_days}-day rolling window)
-- Business days in window: {len(business_days)}
-- Rails declared: {len(instance.rails)}
-- Chains declared: {len(instance.chains)}
-- Transactions emitted: {len(txn_rows)}
-- Daily balances emitted: {len(db_rows)}
-- =====================================================================

{txn_insert}

{db_insert}
"""


# -- Public emit_full_seed (Phase R) -----------------------------------------
#
# Combines emit_baseline_seed + emit_seed: baseline first (a healthy 90-day
# rolling window of leg activity), then planted scenarios overlaid on top.
# CLI ``demo apply`` uses this so deployed dashboards see realistic
# exception signal sitting in realistic baseline noise (R.3 — plants now
# additive rather than constituting the whole seed).


def emit_truncate_sql(
    instance: L2Instance, *, prefix: str, dialect: Dialect = Dialect.POSTGRES,
) -> str:
    """Emit TRUNCATE statements for the per-prefix base + matview tables.

    Schema-preserving teardown: wipes every row from
    ``<prefix>_transactions`` and ``<prefix>_daily_balances`` (the two
    base tables every dataset reads from). The matviews built on top
    will become empty on the next REFRESH; no need to TRUNCATE them
    directly (and Postgres + Oracle have asymmetric semantics for
    TRUNCATE on matviews anyway).

    Postgres uses ``TRUNCATE ... RESTART IDENTITY CASCADE`` so the
    BIGSERIAL ``entry`` column resets to 1 on the next INSERT (matches
    the seed's deterministic-anchor contract). Oracle has no
    RESTART IDENTITY syntax — uses plain ``TRUNCATE TABLE``; the
    integrator can re-create the IDENTITY column if exact serial
    parity matters. SQLite has no ``TRUNCATE`` statement at all —
    uses ``DELETE FROM <table>`` plus a ``DELETE FROM
    sqlite_sequence WHERE name = '<table>'`` to reset the
    AUTOINCREMENT counter (the closest equivalent to PG's RESTART
    IDENTITY). The sqlite_sequence table only exists once an
    AUTOINCREMENT column has been written; the DELETE is gated by
    a presence check via ``WHERE EXISTS (SELECT 1 FROM
    sqlite_master WHERE name='sqlite_sequence')`` so a wipe on a
    fresh schema (no rows yet, no sqlite_sequence row) is a no-op
    rather than an error.

    Returns one SQL string. Idempotent — TRUNCATE on an empty table
    is a no-op. Use ``data clean -o FILE`` for the CLI surface.

    Z.C — ``prefix`` is the cfg.db_table_prefix.
    """
    p = prefix
    if dialect is Dialect.POSTGRES:
        body = (
            f"TRUNCATE TABLE {p}_transactions RESTART IDENTITY CASCADE;\n"
            f"TRUNCATE TABLE {p}_daily_balances RESTART IDENTITY CASCADE;\n"
        )
    elif dialect is Dialect.SQLITE:
        # SQLite has no TRUNCATE — DELETE empties the table and
        # sqlite_sequence reset reclaims the AUTOINCREMENT counter
        # so the next INSERT starts at entry=1 (matches PG's RESTART
        # IDENTITY semantics). The sqlite_sequence presence check
        # avoids "no such table" on a fresh schema.
        body = (
            f"DELETE FROM {p}_transactions;\n"
            f"DELETE FROM {p}_daily_balances;\n"
            f"DELETE FROM sqlite_sequence "
            f"WHERE name IN ('{p}_transactions', '{p}_daily_balances') "
            f"AND EXISTS (SELECT 1 FROM sqlite_master "
            f"WHERE name='sqlite_sequence');\n"
        )
    else:
        # Oracle: plain TRUNCATE; CASCADE in Oracle deletes child rows
        # in referencing tables but our schema has no FKs, so plain
        # TRUNCATE suffices.
        body = (
            f"TRUNCATE TABLE {p}_transactions;\n"
            f"TRUNCATE TABLE {p}_daily_balances;\n"
        )
    header = (
        f"-- =====================================================================\n"
        f"-- L2 instance: {p} — wipe seeded rows (schema-preserving)\n"
        f"-- Generated by recon_gen.common.l2.seed.emit_truncate_sql\n"
        f"-- After running these TRUNCATEs, REFRESH MATERIALIZED VIEW for every\n"
        f"-- dependent matview to clear the cached rows. Or run\n"
        f"--   `data refresh --execute`  after a fresh `data apply --execute`.\n"
        f"-- To remove the schema entirely, run `schema clean --execute`.\n"
        f"-- =====================================================================\n"
    )
    return header + "\n" + body


def emit_full_seed(
    instance: L2Instance,
    scenarios: ScenarioPlant,
    *,
    prefix: str,
    baseline_window_days: int = 90,
    anchor: date | None = None,
    dialect: Dialect = Dialect.POSTGRES,
    base_seed: int | None = None,
) -> str:
    """Emit baseline + plants concatenated as a single SQL script.

    R.3.a — wires R.2's ``emit_baseline_seed`` and the legacy
    ``emit_seed`` together so the deployed demo gets a 3-month healthy
    baseline with planted exception scenarios layered on top. Plants
    use independent transfer_ids (``tr-drift-*``, ``tr-overdraft-*``,
    etc.), so they never collide with baseline ``tr-base-*`` ids.

    Args:
      instance: the L2 model instance.
      scenarios: planted scenarios (typically from
        ``auto_scenario.default_scenario_for(instance).scenario``).
      baseline_window_days: rolling window length for the baseline.
      anchor: anchor date for the baseline window. Defaults to UTC
        ``datetime.now().date()``. The plants' own anchor lives on
        ``scenarios.today`` and may differ — both anchors should
        normally be the same, set by the caller.
      dialect: SQL dialect for both layers.
      base_seed: X.4.h.0.b — root RNG seed for the baseline emitter.
        ``None`` (default) preserves byte-identity with the locked
        seeds (uses ``_BASELINE_BASE_SEED = 42``). Plants are built
        from deterministic per-kind fixed seeds inside the scenario
        builder so they're unaffected; only the 90-day baseline
        leg / chain / cascade RNGs reseed.

    Returns:
      A SQL script string: baseline INSERTs followed by plant INSERTs,
      ready for ``psycopg2.cursor.execute`` (PG) or ``cli._execute_script``
      (Oracle).
    """
    baseline_sql = emit_baseline_seed(
        instance,
        prefix=prefix,
        window_days=baseline_window_days,
        anchor=anchor,
        dialect=dialect,
        base_seed=base_seed,
    )
    # AY.4.d — plants emit via the spine pipeline (was: per-kind dispatch
    # in `emit_seed`). The adapter materializes one ViolationGenerator per
    # plant; ScenarioContext.compose(dry_run=True) captures their dbapi
    # writes as (sql, params) pairs; render_captured_sql substitutes
    # placeholders with dialect-appropriate literals.
    #
    # Production-seed rows carry `metadata.scenario_id` post-AY.4.d
    # (AV.5 contract) — `ScenarioContext.cleanup` can now surgically
    # tear down a deployment's seed without sidecar bookkeeping.
    #
    # Byte drift from the OLD `emit_seed` path is EXPECTED (per AY.0
    # design lock + AY.5 re-lock); the spine emit is simpler (no
    # chain-completion side-effects, no transfer_key cascade) so the
    # locked-seed test fails loudly here. AY.5 re-locks the byte
    # files against the new pipeline.
    from recon_gen.common.spine import (
        ScenarioContext,
        dry_run_capture,
        render_captured_sql,
        scenario_to_generators,
    )
    generators = scenario_to_generators(
        scenarios, instance, anchor=anchor, prefix=prefix,
    )
    cap = dry_run_capture(dialect)
    ctx = ScenarioContext(
        scenario_id=f"build-full-seed-{prefix}",
        prefix=prefix,
        dialect=dialect,
    )
    # WHY (re: the ignore on the next line) — pyright doesn't narrow
    # ViolationGenerator to ClaimedAccountsGenerator via Protocol
    # structural compatibility at this seam (each adapter-returned
    # generator does implement `claimed_accounts` + scenario_id-on-emit
    # per c.1/c.2 contract, but the Protocol membership isn't inferred).
    # DryRunCapture instance satisfies the dbapi Connection shape at
    # runtime even though the static type is `_DryRunBase`. Both
    # narrowings are runtime-safe; spine unit tests cover the behavior.
    captured = ctx.compose(cap, *generators, dry_run=True)  # type: ignore[arg-type]: see preceding WHY comment block on protocol narrowing
    plants_sql = (
        render_captured_sql(captured, dialect=dialect)
        if captured else ""
    )

    body = f"{baseline_sql}\n\n{plants_sql}"
    # X.1.k — stamp a SHA256 header on every emit so any saved-to-disk
    # SQL output identifies itself. The hash is deterministic against
    # the body bytes (everything below this line). The locked-SQL
    # checker (`data lock --check`) compares the entire file (including
    # this header) byte-for-byte, so the in-file hash and the file's
    # real hash always travel together.
    import hashlib
    body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return f"-- SHA256: {body_hash}\n{body}"


# -- Baseline helpers (Phase R) ---------------------------------------------


def _business_days_in_window(
    anchor: date, window_days: int,
) -> list[date]:
    """Return every Mon-Fri date in ``[anchor - window_days, anchor]``.

    Per R.1.f §3: weekends drop to 0 firings for ALL rails. US bank
    holidays are dropped here too when the optional ``holidays`` package
    is importable; without it the calendar drops only weekends and the
    handful of holidays that fell inside the 90-day window land as
    extra-quiet days (acceptable for the demo since exact list isn't
    load-bearing). Returned list is sorted ascending.
    """
    # ``holidays`` package is optional — we only use the membership check
    # (``date in us_holidays``), so any container with ``__contains__`` of
    # ``date`` works. Annotated as ``Container[object]`` because the
    # holidays package itself is untyped (no stubs); pyright would
    # otherwise flag the import as Unknown.
    from typing import Container

    empty_holidays: set[object] = set()
    us_holidays: Container[object] = empty_holidays
    try:
        import holidays as _holidays_pkg  # type: ignore[import-not-found,import-untyped]: optional dep, third-party library lacks PEP 561 stubs

        us_holidays = _holidays_pkg.US(  # type: ignore[no-untyped-call,unused-ignore]: third-party method has no type annotations
            years=range(anchor.year - 1, anchor.year + 1),
        )
    except ImportError:
        pass

    start = anchor - timedelta(days=window_days)
    days: list[date] = []
    cursor = start
    while cursor <= anchor:
        # Mon=0 ... Sun=6; skip Sat (5) + Sun (6).
        if cursor.weekday() < 5 and cursor not in us_holidays:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


from enum import StrEnum
from typing import Literal


class _RailKind(StrEnum):
    """Classification for the per-Rail volume + amount lookup (R.1.f §1+§2).

    The classifier inspects ``rail.transfer_type`` plus the rail's
    aggregating / cadence flags + source/destination role expressions
    to pick a kind. Heuristic — not exhaustive across novel L2
    instances; sasquatch_pr + spec_example are the calibration set.
    """

    CUSTOMER_INBOUND = "customer_inbound"
    CUSTOMER_OUTBOUND = "customer_outbound"
    CUSTOMER_FEE = "customer_fee"
    INTERNAL_TRANSFER = "internal_transfer"
    AGGREGATING_DAILY = "aggregating_daily"
    AGGREGATING_MONTHLY = "aggregating_monthly"
    AGGREGATING_INTRADAY = "aggregating_intraday"
    CONCENTRATION = "concentration"
    CARD_SALE = "card_sale"
    MERCHANT_PAYOUT = "merchant_payout"
    EXTERNAL_CARD_SETTLEMENT = "external_card_settlement"
    ACH_RETURN = "ach_return"
    # AG.4 (Gap D): a system-wide payroll/batch credit — one ACH file
    # that fans out to N customers, fired ~once per pay period. Distinct
    # from CUSTOMER_INBOUND (per-customer scaled) because the batch is a
    # single system event, not per-account activity.
    PAYROLL_BATCH = "payroll_batch"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class _RailKindParams:
    """Per-kind volume + amount + time-of-day constants (R.1.f §1+§2+§3)."""

    # Average firings per business day, scaled by ``scaling_kind`` count.
    daily_target_per_unit: float
    # What entity the volume scales by — customer-account count, merchant
    # count, or 1 (system-wide rails like sweeps).
    scaling_kind: Literal["customer", "merchant", "system"]
    # Lognormal parameters for amount sampling; sample = exp(N(mu, sigma)).
    amount_mu: float
    amount_sigma: float
    # Time-of-day band for posting. (start_hour, end_hour) ET. Generator
    # samples uniformly inside the band per R.1.f §3.
    time_band: tuple[int, int]


_RAIL_KIND_PARAMS: dict[_RailKind, _RailKindParams] = {
    _RailKind.CUSTOMER_INBOUND: _RailKindParams(
        daily_target_per_unit=4.0, scaling_kind="customer",
        amount_mu=6.5, amount_sigma=1.2, time_band=(9, 15),
    ),
    _RailKind.CUSTOMER_OUTBOUND: _RailKindParams(
        daily_target_per_unit=2.0, scaling_kind="customer",
        amount_mu=6.5, amount_sigma=1.2, time_band=(9, 17),
    ),
    _RailKind.CUSTOMER_FEE: _RailKindParams(
        # 1 firing/customer/month → roughly 1/22 business-days.
        daily_target_per_unit=1.0 / 22.0, scaling_kind="customer",
        amount_mu=2.5, amount_sigma=0.4, time_band=(9, 17),
    ),
    _RailKind.INTERNAL_TRANSFER: _RailKindParams(
        daily_target_per_unit=1.0, scaling_kind="system",
        amount_mu=8.0, amount_sigma=1.5, time_band=(9, 17),
    ),
    _RailKind.AGGREGATING_DAILY: _RailKindParams(
        daily_target_per_unit=1.0, scaling_kind="system",
        amount_mu=11.0, amount_sigma=0.8, time_band=(17, 19),
    ),
    _RailKind.AGGREGATING_INTRADAY: _RailKindParams(
        daily_target_per_unit=4.0, scaling_kind="system",
        amount_mu=10.5, amount_sigma=0.8, time_band=(9, 17),
    ),
    _RailKind.AGGREGATING_MONTHLY: _RailKindParams(
        # Fires only on last business day of month — handled in time logic.
        daily_target_per_unit=1.0, scaling_kind="system",
        amount_mu=9.5, amount_sigma=0.5, time_band=(17, 19),
    ),
    _RailKind.CONCENTRATION: _RailKindParams(
        daily_target_per_unit=1.0, scaling_kind="system",
        amount_mu=12.0, amount_sigma=0.7, time_band=(15, 17),
    ),
    _RailKind.CARD_SALE: _RailKindParams(
        daily_target_per_unit=8.0, scaling_kind="merchant",
        amount_mu=4.5, amount_sigma=0.9, time_band=(10, 22),
    ),
    _RailKind.MERCHANT_PAYOUT: _RailKindParams(
        daily_target_per_unit=1.0, scaling_kind="merchant",
        amount_mu=9.0, amount_sigma=1.1, time_band=(9, 15),
    ),
    _RailKind.EXTERNAL_CARD_SETTLEMENT: _RailKindParams(
        daily_target_per_unit=1.0, scaling_kind="system",
        amount_mu=11.5, amount_sigma=0.6, time_band=(15, 17),
    ),
    _RailKind.ACH_RETURN: _RailKindParams(
        # ~5% of customer-inbound rate; the actual rate scales off
        # CustomerInboundACH so this is a lower bound.
        daily_target_per_unit=0.2, scaling_kind="customer",
        amount_mu=6.5, amount_sigma=1.2, time_band=(9, 17),
    ),
    _RailKind.PAYROLL_BATCH: _RailKindParams(
        # AG.4 (Gap D): system-wide batch fired ~once per pay period
        # (bi-weekly ≈ 1 firing per 10 business days = 0.1/day). Large
        # aggregate amount (the whole payroll file); early-morning ACH
        # settlement window. NOT per-customer scaled — that was the
        # over-match bug (a single batch wrongly fired ~80×/day).
        daily_target_per_unit=0.1, scaling_kind="system",
        amount_mu=11.5, amount_sigma=0.9, time_band=(6, 9),
    ),
    _RailKind.OTHER: _RailKindParams(
        daily_target_per_unit=1.0, scaling_kind="system",
        amount_mu=7.0, amount_sigma=1.0, time_band=(9, 17),
    ),
}


def _classify_rail(rail: Rail) -> _RailKind:
    """Map a Rail to a ``_RailKind`` for volume + amount lookup.

    Inspection order: aggregating + cadence first (highest signal),
    then transfer_type substring. Heuristic — falls back to OTHER if
    no rule matches; OTHER's defaults are intentionally conservative
    so an unclassified Rail still gets some baseline volume.
    """
    if rail.aggregating:
        cadence = (rail.cadence or "").lower()
        if "monthly" in cadence:
            return _RailKind.AGGREGATING_MONTHLY
        if "intraday" in cadence:
            return _RailKind.AGGREGATING_INTRADAY
        # daily-eod, daily-bod, weekly-* → all bucket to daily.
        return _RailKind.AGGREGATING_DAILY

    # Z.B (2026-05-15): rail.name IS the type identifier under the
    # symmetric collapse. Z.C.7 follow-on (2026-05-15): rewired from
    # snake_case exact match (legacy `transfer_type`) to substring
    # match on the CamelCase rail.name (e.g. CustomerInboundACH,
    # MerchantPayoutWire, CustomerInboundACHReturnNSF). Order matters:
    # `return` must come before `inbound` so an ACH return doesn't get
    # misclassified as a customer inbound. Heuristic falls back to OTHER
    # on mismatch.
    tt = str(rail.name).lower()
    if "return" in tt:
        return _RailKind.ACH_RETURN
    if "concentration" in tt:
        return _RailKind.CONCENTRATION
    if "cardsale" in tt or ("merchant" in tt and "card" in tt and "sale" in tt):
        return _RailKind.CARD_SALE
    if "cardsettlement" in tt or ("externalcard" in tt) or tt == "card_settlement":
        return _RailKind.EXTERNAL_CARD_SETTLEMENT
    if "payout" in tt:
        return _RailKind.MERCHANT_PAYOUT
    if "fee" in tt:
        return _RailKind.CUSTOMER_FEE
    if "inbound" in tt or "deposit" in tt:
        # AG.4 (Gap D): a rail containing "inbound"/"deposit" that is
        # ALSO a payroll/batch is a system-wide batch (one ACH file
        # fanning out to N customers), not per-customer activity. Route
        # it to PAYROLL_BATCH (system-wide ~1/pay-period) instead of
        # letting the per-customer CUSTOMER_INBOUND scaling fire it
        # ~80×/day. Guard is intentionally narrow (inbound/deposit AND
        # payroll/batch) so it can't reclassify a plain CustomerInboundACH.
        if "payroll" in tt or "batch" in tt:
            return _RailKind.PAYROLL_BATCH
        return _RailKind.CUSTOMER_INBOUND
    if "outbound" in tt or "withdrawal" in tt:
        return _RailKind.CUSTOMER_OUTBOUND
    if "internal" in tt or "charge" in tt or "subledger" in tt:
        return _RailKind.INTERNAL_TRANSFER
    return _RailKind.OTHER


# Per R.1.f §5: per-account_role kind starting-balance distribution.
# Tuple = (mu, sigma) for lognormal; None = $0 starting balance.
_StartingBalanceParams = tuple[float, float] | None
_STARTING_BALANCE_BY_ROLE_KIND: dict[str, _StartingBalanceParams] = {
    # R.4 tuning: bumped customer_dda from (8.5, 1.0) → (11.0, 0.5) so
    # customers fund at median ~$60k. With outbound activity at median
    # ~$665/transfer × 2 firings/business day, $60k cushion keeps
    # overdrafts rare (planted scenarios + occasional large outbounds
    # on small accounts only). Lower starting balance produces ~300
    # overdraft rows over 90 days — looks like a broken bank, not a
    # bank with occasional exceptions.
    "customer_dda": (11.0, 0.5),      # median ~$60,000
    "merchant_dda": (12.5, 0.5),      # median ~$268,000
    # Internal GL + concentration accounts must absorb daily sweep
    # cascades — money flows in via customer outbounds + sweeps from
    # sub-accounts, then out via concentration → FRB. Cumulative net
    # can swing wide, so cushion needs to comfortably cover one
    # window's worth of activity (~$30M+ at the sasquatch_pr scale).
    "internal_gl": (17.5, 0.3),       # median ~$40M
    "concentration": (17.5, 0.3),     # median ~$40M
    # Suspense accounts net to zero EOD by design but absorb intra-day
    # swings as transfers cascade through. Small cushion (~$1M) keeps
    # baseline noise from showing every accounting moment as an
    # overdraft; planted overdrafts on these accounts still surface.
    "internal_suspense": (13.5, 0.5), # median ~$1M
    "external": None,                 # we don't track external balances
    "other": None,
}


def _classify_role(role: Identifier | str) -> str:
    """Map a role name to a starting-balance kind (R.1.f §5).

    Heuristic substring match — covers sasquatch_pr + spec_example role
    names. Falls back to "other" → $0 starting balance.
    """
    r = str(role).lower()
    if "concentration" in r and "master" in r:
        return "concentration"
    if "merchant" in r:
        return "merchant_dda"
    if "customer" in r:
        return "customer_dda"
    if "suspense" in r or "recon" in r or r.startswith("zba"):
        return "internal_suspense"
    if (
        "external" in r or "counter" in r or "card_network" in r
        or "fed" in r or "frb" in r
    ):
        return "external"
    if (
        "gl" in r or "cash" in r or "settlement" in r or "due" in r
        or "clearing" in r or "ach_orig" in r
    ):
        return "internal_gl"
    return "other"


def _baseline_target_leg_count(
    rail: Rail, kind: _RailKind, customer_count: int, merchant_count: int,
    business_day_count: int,
) -> int:
    """Compute the per-Rail target firing count over the window (R.1.f §1).

    Returns an integer count of FIRINGS (each firing emits 1 or 2 legs
    depending on rail shape; the per-firing count is independent of the
    target). The actual per-day count is randomized via Poisson sampling
    in the leg loop.
    """
    params = _RAIL_KIND_PARAMS[kind]
    if params.scaling_kind == "customer":
        scale = customer_count
    elif params.scaling_kind == "merchant":
        scale = merchant_count
    else:
        scale = 1

    if kind is _RailKind.AGGREGATING_MONTHLY:
        # Fires only on last business day of month → ~3 in 90 days.
        return 3
    return max(1, int(business_day_count * params.daily_target_per_unit * scale))


# AF (E8): approximate count of each Period within a business-day window.
# business_day is exact; the others approximate using standard banking
# ratios (5 business days/week, 10/pay-period [bi-weekly], 21/month).
_BUSINESS_DAYS_PER_PERIOD: dict[Period, int] = {
    "business_day": 1,
    "week": 5,
    "pay_period": 10,
    "month": 21,
}


def _periods_in_window(period: Period, business_day_count: int) -> int:
    """AF (E8): how many whole ``period``s fit in a window of
    ``business_day_count`` business days. Floor-divided, min 1 — a
    window shorter than one period still gets one period's worth of
    firings so the rail isn't silent."""
    per = _BUSINESS_DAYS_PER_PERIOD[period]
    return max(1, business_day_count // per)


def _pick_firings_count(
    entity: Rail | TransferTemplate,
    *,
    business_day_count: int,
    rng: random.Random,
    fallback: int,
) -> int:
    """AF (E8): total firing count over the window.

    When ``entity.firings_typical_per_period`` is set, sample a
    per-period count uniform-randomly from the declared range and scale
    by the number of periods in the window (count-per-period × periods
    = total-over-window). The per-day distribution is then handled by
    the caller's existing Poisson spread, so the declared band shows up
    as the aggregate-per-period the operator intended.

    When the field is absent, return ``fallback`` (the per-kind
    heuristic from ``_baseline_target_leg_count``) WITHOUT consuming any
    ``rng`` state — so pre-AF L2 instances stay byte-identical to their
    locked seeds (no rng-stream drift for rails that don't declare the
    field).
    """
    ftp = entity.firings_typical_per_period
    if ftp is None:
        return fallback
    lo, hi = ftp.count_range
    per_period = rng.randint(lo, hi)
    return per_period * _periods_in_window(ftp.period, business_day_count)


def _baseline_amount_sample(
    rng: random.Random,
    kind: _RailKind,
    cap: Decimal | None = None,
    rail: Rail | None = None,
) -> Decimal:
    """Sample one amount per R.1.f §2's per-kind lognormal table — OR
    per AB.5 (E7) per-rail ``amount_typical_range`` when the rail
    declares one.

    AB.5 (E7): when ``rail`` is passed AND ``rail.amount_typical_range``
    is set, samples log-uniformly within the declared range — financial
    flows cluster at the low end of typical bands so log-uniform
    reproduces that shape. Per AB.5.0 lock: log-uniform default. When
    the rail leaves ``amount_typical_range`` unset (or ``rail`` is
    None), falls through to the per-kind lognormal heuristic.

    ``cap``: optional ``LimitSchedule.cap`` ceiling. When set, a sample
    that exceeds the cap is **clamped + resampled** rather than truncated
    so the underlying distribution shape stays clean (truncation would
    pile mass at the cap). Resample retries up to 5 times; falls back to
    ``cap * 0.95`` so the loop always terminates. The cap interaction
    with `amount_typical_range`: log-uniform samples that exceed the
    cap also resample; if every retry hits the cap (e.g., cap < range
    minimum), falls back to ``min(cap * 0.95, range.max)`` so the
    plant amount stays inside the declared range when possible.
    """
    # AB.5 (E7) — per-rail typical-range path.
    if rail is not None and rail.amount_typical_range is not None:
        lo, hi = rail.amount_typical_range
        import math
        log_lo = math.log(float(lo))
        log_hi = math.log(float(hi))
        for _ in range(5):
            raw = math.exp(rng.uniform(log_lo, log_hi))
            amount = Decimal(f"{raw:.2f}")
            if cap is None or amount <= cap:
                return amount
        # Cap blew through every retry; pin to min(cap * 0.95, range.max).
        if cap is not None:
            ceiling = min(float(cap) * 0.95, float(hi))
            return Decimal(f"{ceiling:.2f}")
        return Decimal(f"{float(hi):.2f}")

    params = _RAIL_KIND_PARAMS[kind]
    for _ in range(5):
        raw = rng.lognormvariate(params.amount_mu, params.amount_sigma)
        amount = Decimal(f"{raw:.2f}")
        if cap is None or amount <= cap:
            return amount
    # Fallback after 5 misses on the cap.
    return Decimal(f"{float(cap) * 0.95:.2f}") if cap else Decimal("0.00")


def _baseline_time_of_day(
    rng: random.Random, kind: _RailKind, day: date,
) -> str:
    """Sample a posting time-of-day inside the kind's R.1.f §3 band.

    Returns an ISO-8601 timestamp (UTC) for the given business day at a
    sampled time inside the kind's ``time_band``. Time-of-day band is
    a uniform draw inside the band; the seconds field uses minute-level
    granularity which is enough for the dashboards' chronological sort.
    """
    params = _RAIL_KIND_PARAMS[kind]
    start_hour, end_hour = params.time_band
    # Uniform inside the band — minute-level granularity.
    minutes_in_band = (end_hour - start_hour) * 60
    offset = rng.randrange(minutes_in_band)
    hour = start_hour + (offset // 60)
    minute = offset % 60
    return f"{day.isoformat()}T{hour:02d}:{minute:02d}:00+00:00"


def _materialize_baseline_template_instances(
    instance: L2Instance,
    template_by_role: dict[Identifier, AccountTemplate],
) -> tuple[TemplateInstance, ...]:
    """Materialize per-template baseline instances for the leg loop.

    Per-template counts:
      - Customer-DDA-like template (role classified as ``customer_dda``):
        20 instances. Big enough to drive realistic per-day volume.
      - Merchant-DDA-like (``merchant_dda``): 5 instances.
      - Anything else: 5 instances.

    Honors each template's ``instance_id_template`` /
    ``instance_name_template`` when set; falls back to the legacy
    ``cust-001`` / ``Customer 1`` naming otherwise. The synthesized
    set is sorted by ``account_id`` to keep emission order stable.

    Index offset: indices start at ``_BASELINE_INDEX_START`` (11), NOT
    1, so baseline accounts never collide with plant accounts (which
    use indices 1-N from ``default_scenario_for``'s ``_materialize_
    instances``). Without this offset, plant rows and baseline rows
    would compete for the same ``daily_balances(account_id, day)`` PK
    — last write wins, the loser's row goes missing, and the L1 drift
    matview flags the SUM-vs-stored mismatch as a false positive.
    """
    instances: list[TemplateInstance] = []
    for role, tmpl in sorted(template_by_role.items(), key=lambda kv: str(kv[0])):
        role_kind = _classify_role(role)
        if role_kind == "customer_dda":
            n_instances = 20
        else:
            n_instances = 5

        id_tmpl = tmpl.instance_id_template or "cust-{n:03d}"
        name_tmpl = tmpl.instance_name_template or "Customer {n}"

        for offset in range(n_instances):
            n = _BASELINE_INDEX_START + offset
            instances.append(TemplateInstance(
                template_role=role,
                account_id=Identifier(id_tmpl.format(role=str(role), n=n)),
                name=Name(name_tmpl.format(role=str(role), n=n)),
            ))
    instances.sort(key=lambda ti: str(ti.account_id))
    _ = instance  # silence unused — reserved for future role-based filtering
    return tuple(instances)


# Plant indices use 1..N (typically 1, 2) per default_scenario_for's
# _materialize_instances. Baseline instances start at 11 so the two
# pools are disjoint — no daily_balances(account_id, day) PK collisions
# between plant and baseline rows. See _materialize_baseline_template_
# instances for context.
_BASELINE_INDEX_START = 11


def _initialize_starting_balances(
    state: _BaselineState,
    instance: L2Instance,
    template_by_role: dict[Identifier, AccountTemplate],
    rng: random.Random,
) -> None:
    """Seed per-account starting balances from R.1.f §5's per-role table.

    Walks every materialized template instance + every singleton account
    and assigns a starting balance per its role's classification. Roles
    that classify to ``None`` (external counterparties, internal-suspense)
    get a $0 starting balance. Iteration order is sorted by account_id
    so the RNG draws happen in a deterministic sequence per anchor.
    """
    # Template-instance accounts.
    for ti in sorted(state.template_instances, key=lambda i: str(i.account_id)):
        role_kind = _classify_role(ti.template_role)
        params = _STARTING_BALANCE_BY_ROLE_KIND.get(role_kind)
        if params is None:
            state.balances[ti.account_id] = Decimal("0.00")
            continue
        mu, sigma = params
        raw = rng.lognormvariate(mu, sigma)
        state.balances[ti.account_id] = Decimal(f"{raw:.2f}")

    # Singleton accounts from instance.accounts.
    for a in sorted(instance.accounts, key=lambda a: str(a.id)):
        role_kind = _classify_role(a.role or a.id)
        params = _STARTING_BALANCE_BY_ROLE_KIND.get(role_kind)
        if params is None:
            state.balances[a.id] = Decimal("0.00")
            continue
        mu, sigma = params
        raw = rng.lognormvariate(mu, sigma)
        state.balances[a.id] = Decimal(f"{raw:.2f}")
    _ = template_by_role  # not needed — role classification is by name


def _eligible_accounts_for_role(
    role_expr: tuple[Identifier, ...],
    state: _BaselineState,
    instance: L2Instance,
) -> list[_ResolvedAccount]:
    """Return every account whose role is in ``role_expr``.

    Walks both materialized template instances AND singleton accounts.
    Sorted by ``account_id`` for deterministic picker output.
    """
    role_set = {str(r) for r in role_expr}
    out: list[_ResolvedAccount] = []
    for ti in state.template_instances:
        if str(ti.template_role) in role_set:
            out.append(_resolve_any_account(
                ti.account_id, instance, _SCENARIO_FOR_RESOLVE(state),
                _TEMPLATE_BY_ROLE_FOR_RESOLVE(instance),
            ))
    for a in instance.accounts:
        role = str(a.role) if a.role is not None else str(a.id)
        if role in role_set:
            out.append(_ResolvedAccount(
                account_id=a.id,
                account_name=a.name or Name(str(a.id)),
                account_role=a.role or Identifier(str(a.id)),
                account_scope=a.scope,
                account_parent_role=a.parent_role,
            ))
    out.sort(key=lambda r: str(r.account_id))
    return out


# Adapter helpers so the baseline emitter can reuse _resolve_any_account
# (which was written for the existing emit_seed path that takes a
# ScenarioPlant). Cheaper than duplicating the resolve logic.
def _SCENARIO_FOR_RESOLVE(state: _BaselineState) -> "ScenarioPlant":
    return ScenarioPlant(
        template_instances=state.template_instances,
        today=state.anchor,
    )


def _TEMPLATE_BY_ROLE_FOR_RESOLVE(
    instance: L2Instance,
) -> dict[Identifier, AccountTemplate]:
    return {t.role: t for t in instance.account_templates}


def _emit_baseline_for_rail(
    rail: Rail,
    instance: L2Instance,
    state: _BaselineState,
    template_by_role: dict[Identifier, AccountTemplate],
    rng: random.Random,
    counter: _Counter,
    dialect: Dialect,
) -> list[str]:
    """Emit per-Rail leg rows for the rolling window.

    R.2.b implements the loop for **non-aggregating** rails (single-leg
    + two-leg, ``aggregating=False``). Aggregating rails return ``[]``
    here — R.2.c adds the children-first + EOD bundling parent on top.

    Steps per R.1.f §1-3:
      1. Classify the Rail; look up volume + amount + time params.
      2. Compute target leg count over the window via the heuristic.
      3. Distribute firings across business days (Poisson around daily
         target). Monthly_eom rails fire only on the last business day
         of each month — handled in R.2.c.
      4. Per firing: pick source/destination accounts via role expr,
         sample amount + time-of-day.
      5. Emit one or two ``_txn_row`` calls per firing, update
         ``state.balances`` for each leg.
      6. At end of each business day, snapshot every touched account's
         running balance into ``state.eod_balances`` so R.2.e can
         materialize ``daily_balances`` rows.
    """
    if rail.aggregating:
        # Aggregating rails go through _emit_baseline_for_aggregating_rail
        # (R.2.c) which handles the children-first + EOD bundling pattern.
        return []

    kind = _classify_rail(rail)
    business_days = state.business_days
    if not business_days:
        return []

    # Customer + merchant counts for volume scaling. Materialized template
    # instances drive the count (the baseline uses 20 customers, 5
    # merchants per the materializer).
    customer_count = sum(
        1 for ti in state.template_instances
        if _classify_role(ti.template_role) == "customer_dda"
    )
    merchant_count = sum(
        1 for ti in state.template_instances
        if _classify_role(ti.template_role) == "merchant_dda"
    )
    customer_count = max(1, customer_count)
    merchant_count = max(1, merchant_count)

    heuristic_total = _baseline_target_leg_count(
        rail, kind, customer_count, merchant_count, len(business_days),
    )
    # AF (E8): operator-declared firings_typical_per_period overrides the
    # per-kind heuristic. No rng consumption when the field is absent, so
    # pre-AF rails stay byte-identical to their locked seeds.
    target_total = _pick_firings_count(
        rail,
        business_day_count=len(business_days),
        rng=rng,
        fallback=heuristic_total,
    )
    daily_target = target_total / len(business_days)

    # Resolve eligible accounts for source + destination.
    if isinstance(rail, TwoLegRail):
        src_accounts = _eligible_accounts_for_role(
            rail.source_role, state, instance,
        )
        dst_accounts = _eligible_accounts_for_role(
            rail.destination_role, state, instance,
        )
        if not src_accounts or not dst_accounts:
            return []
    else:
        # SingleLegRail
        leg_accounts = _eligible_accounts_for_role(
            rail.leg_role, state, instance,
        )
        if not leg_accounts:
            return []
        src_accounts = leg_accounts
        dst_accounts = []

    # Resolve LimitSchedule cap if any applies (used to clamp the
    # amount sampler — R.1.f §2's clamp+resample contract).
    cap_by_parent_role = _baseline_cap_lookup(rail, instance)

    # Resolve Origin per L2 rule O1.
    if isinstance(rail, TwoLegRail):
        src_origin = (
            str(rail.source_origin) if rail.source_origin is not None
            else (str(rail.origin) if rail.origin is not None else "InternalInitiated")
        )
        dst_origin = (
            str(rail.destination_origin) if rail.destination_origin is not None
            else (str(rail.origin) if rail.origin is not None else "InternalInitiated")
        )
    else:
        src_origin = (
            str(rail.origin) if rail.origin is not None
            else "InternalInitiated"
        )
        dst_origin = src_origin  # unused for single-leg

    rows: list[str] = []
    rail_slug = _baseline_rail_slug(rail.name)

    for day in business_days:
        # Poisson sample of firings on this day around the daily target.
        # rng.poisson would be ideal but Python's random doesn't expose
        # it; Knuth's algorithm or a rough Gaussian approximation works.
        n_firings = _poisson_sample(rng, daily_target)
        if n_firings <= 0:
            continue

        for firing_seq in range(n_firings):
            n = counter.next()
            transfer_id = f"tr-base-{rail_slug}-{n:06d}"
            txn_id = f"tx-base-{rail_slug}-{n:06d}"
            posting = _baseline_time_of_day(rng, kind, day)

            src = src_accounts[rng.randrange(len(src_accounts))]
            cap = cap_by_parent_role.get(str(src.account_parent_role) if src.account_parent_role else "")
            # V.5.a — per-(source-account, transfer_type, day) cap tracker.
            # The amount sampler clamps each individual draw to `cap`; the
            # matview however groups SUM(ABS(amount)) across same-day same-
            # type legs from the same source. Only internal-scope sources
            # with a non-null parent_role land in the matview, so the
            # accumulator only needs to track those.
            cap_key: tuple[Identifier, str, date] | None = None
            cap_accumulated: Decimal = Decimal(0)
            if (
                cap is not None
                and str(src.account_scope) == "internal"
                and src.account_parent_role is not None
            ):
                cap_key = (src.account_id, str(rail.name), day)
                cap_accumulated = state.daily_outbound_by_account_type.get(
                    cap_key, Decimal(0),
                )
                remaining_cap = cap - cap_accumulated
                if remaining_cap < Decimal("50"):
                    continue  # daily cap exhausted; emitting <$50 is silly
                amount = _baseline_amount_sample(
                    rng, kind, cap=remaining_cap, rail=rail,
                )
            else:
                amount = _baseline_amount_sample(
                    rng, kind, cap=cap, rail=rail,
                )

            metadata = _baseline_metadata(rail, n, firing_seq)
            # R.2.c bundle stamp: if this child rail is bundled by some
            # aggregating Rail today, the leg's bundle_id is the bundle
            # transfer_id pre-computed by _populate_bundle_map.
            bundle_id = state.bundle_map.get((rail.name, day))
            # R.2.d firing log: record this firing so the chain pass can
            # attach children to it.
            state.firings.setdefault(rail.name, []).append(
                (transfer_id, day, amount),
            )

            if isinstance(rail, TwoLegRail):
                dst = dst_accounts[rng.randrange(len(dst_accounts))]
                rows.append(_txn_row(
                    id_=f"{txn_id}-src",
                    account_id=src.account_id,
                    account_name=src.account_name,
                    account_role=src.account_role,
                    account_scope=src.account_scope,
                    account_parent_role=src.account_parent_role,
                    money=-amount,
                    direction="Debit",
                    posting=posting,
                    transfer_id=transfer_id,
                    rail_name=rail.name,
                    origin=src_origin,
                    metadata=metadata,
                    bundle_id=bundle_id,
                    dialect=dialect,
                ))
                rows.append(_txn_row(
                    id_=txn_id,
                    account_id=dst.account_id,
                    account_name=dst.account_name,
                    account_role=dst.account_role,
                    account_scope=dst.account_scope,
                    account_parent_role=dst.account_parent_role,
                    money=amount,
                    direction="Credit",
                    posting=posting,
                    transfer_id=transfer_id,
                    rail_name=rail.name,
                    origin=dst_origin,
                    metadata=metadata,
                    bundle_id=bundle_id,
                    dialect=dialect,
                ))
                # Record legs in the deferred-walk log; daily-balance
                # materializer recomputes cumulative balances at emit
                # end (avoids the rail-iteration overwrite bug).
                state.account_leg_log.setdefault(src.account_id, []).append(
                    (posting, day, -amount),
                )
                state.account_leg_log.setdefault(dst.account_id, []).append(
                    (posting, day, amount),
                )
                if cap_key is not None:
                    state.daily_outbound_by_account_type[cap_key] = (
                        cap_accumulated + amount
                    )
            else:
                assert isinstance(rail, SingleLegRail)
                if rail.leg_direction == "Credit":
                    direction, signed = "Credit", amount
                else:
                    direction, signed = "Debit", -amount
                rows.append(_txn_row(
                    id_=txn_id,
                    account_id=src.account_id,
                    account_name=src.account_name,
                    account_role=src.account_role,
                    account_scope=src.account_scope,
                    account_parent_role=src.account_parent_role,
                    money=signed,
                    direction=direction,
                    posting=posting,
                    transfer_id=transfer_id,
                    rail_name=rail.name,
                    origin=src_origin,
                    metadata=metadata,
                    bundle_id=bundle_id,
                    dialect=dialect,
                ))
                state.account_leg_log.setdefault(src.account_id, []).append(
                    (posting, day, signed),
                )
                # Track only Debit legs against the cap (matview filters on
                # amount_direction='Debit'). Credit single-legs don't add
                # to outbound aggregates.
                if cap_key is not None and direction == "Debit":
                    state.daily_outbound_by_account_type[cap_key] = (
                        cap_accumulated + amount
                    )

    _ = template_by_role  # accounts already resolved via state.template_instances
    return rows


def _emit_opening_balance_rows(
    instance: L2Instance,
    state: _BaselineState,
    template_by_role: dict[Identifier, AccountTemplate],
    counter: _Counter,
    dialect: Dialect,
) -> list[str]:
    """Plant per-template-instance opening balance transactions.

    For every materialized template instance with a non-zero starting
    balance, emit one 2-leg "opening" Transfer at the very start of the
    window: source = first declared external counterparty Account
    (negative amount), destination = the customer (positive amount).
    Records the credit leg in account_leg_log so the daily-balance
    walk sees the customer balance start at the funded amount, not $0.

    Without this, customers with high outbound volume mechanically
    overdraft on the first business day they fire — overdraft matview
    fills with false positives clustered at window-start.

    Picks the first 2-leg inbound rail whose destination_role matches
    the customer template's role to use for transfer_type/rail_name
    metadata; skips if none exists. The opening uses an existing rail
    so the L2FT Hygiene Exceptions sheet's "Unmatched Transfer Type"
    check stays green.
    """
    if not state.business_days:
        return []
    opening_day = state.business_days[0]
    opening_ts = (
        f"{opening_day.isoformat()}T00:00:01+00:00"  # 1s past midnight
    )

    # Pick a default external counterparty: any external-scope account.
    external_account: _ResolvedAccount | None = None
    for a in sorted(instance.accounts, key=lambda a: str(a.id)):
        if str(a.scope) == "external":
            external_account = _ResolvedAccount(
                account_id=a.id,
                account_name=a.name or Name(str(a.id)),
                account_role=a.role or Identifier(str(a.id)),
                account_scope=a.scope,
                account_parent_role=a.parent_role,
            )
            break
    if external_account is None:
        return []  # No external counterparty — can't fund openings

    # Pick the first two-leg rail per destination role. Used purely
    # for transfer_type/rail_name labeling on the opening row; doesn't
    # change leg semantics. Aggregating rails included since some
    # internal singletons (e.g., ConcentrationMaster) only have
    # aggregating inbound rails — using one as a label is fine.
    rail_for_role: dict[Identifier, TwoLegRail] = {}
    for rail in sorted(instance.rails, key=lambda r: str(r.name)):
        if not isinstance(rail, TwoLegRail):
            continue
        for dest in rail.destination_role:
            if dest not in rail_for_role:
                rail_for_role[dest] = rail

    # AJ.4b — opening-balance legs are demo funding scaffolding; tag them
    # with the internal balance-maintenance rail (when declared) so they
    # don't count as firings of the funding rail used only as a label.
    bm = _balance_maintenance_rail(instance)

    # Build account list: template instances + internal-scope singletons.
    # Both pools need opening capital so the cumulative-from-zero balance
    # walk doesn't show false-positive overdrafts on the bank's GL +
    # concentration accounts.
    fundable: list[_ResolvedAccount] = []
    for ti in sorted(state.template_instances, key=lambda i: str(i.account_id)):
        tmpl = template_by_role.get(ti.template_role)
        if tmpl is None:
            continue
        fundable.append(_ResolvedAccount(
            account_id=ti.account_id,
            account_name=ti.name,
            account_role=ti.template_role,
            account_scope=tmpl.scope,
            account_parent_role=tmpl.parent_role,
        ))
    for a in sorted(instance.accounts, key=lambda a: str(a.id)):
        if str(a.scope) != "internal":
            continue
        fundable.append(_ResolvedAccount(
            account_id=a.id,
            account_name=a.name or Name(str(a.id)),
            account_role=a.role or Identifier(str(a.id)),
            account_scope=a.scope,
            account_parent_role=a.parent_role,
        ))

    rows: list[str] = []
    for acct in fundable:
        starting = state.balances.get(acct.account_id, Decimal("0"))
        if starting <= Decimal("0"):
            continue
        rail = rail_for_role.get(acct.account_role)
        if rail is None:
            continue
        n = counter.next()
        transfer_id = f"tr-base-opening-{n:04d}"
        txn_id = f"tx-base-opening-{n:04d}"
        src_origin = (
            str(rail.source_origin) if rail.source_origin is not None
            else (str(rail.origin) if rail.origin is not None else "ExternalForcePosted")
        )
        dst_origin = (
            str(rail.destination_origin) if rail.destination_origin is not None
            else (str(rail.origin) if rail.origin is not None else "ExternalForcePosted")
        )
        metadata = _baseline_metadata(rail, n, 0)
        rows.append(_txn_row(
            id_=f"{txn_id}-src",
            account_id=external_account.account_id,
            account_name=external_account.account_name,
            account_role=external_account.account_role,
            account_scope=external_account.account_scope,
            account_parent_role=external_account.account_parent_role,
            money=-starting,
            direction="Debit",
            posting=opening_ts,
            transfer_id=transfer_id,
            rail_name=bm if bm is not None else rail.name,
            origin=src_origin,
            metadata=metadata,
            dialect=dialect,
        ))
        rows.append(_txn_row(
            id_=txn_id,
            account_id=acct.account_id,
            account_name=acct.account_name,
            account_role=acct.account_role,
            account_scope=acct.account_scope,
            account_parent_role=acct.account_parent_role,
            money=starting,
            direction="Credit",
            posting=opening_ts,
            transfer_id=transfer_id,
            rail_name=bm if bm is not None else rail.name,
            origin=dst_origin,
            metadata=metadata,
            dialect=dialect,
        ))
        # Record the credit leg so the daily-balance walk sees this
        # account start at `starting` not $0. External-scope source
        # account legs are not tracked (we don't compute balances for
        # external counterparties).
        state.account_leg_log.setdefault(acct.account_id, []).append(
            (opening_ts, opening_day, starting),
        )

    return rows


def _populate_bundle_map(
    state: _BaselineState, instance: L2Instance,
) -> None:
    """Pre-compute bundle_id assignments for every (child_rail, day) pair.

    R.2.c — for each aggregating Rail with declared ``bundles_activity``,
    walk the firing schedule (every business day for daily_eod /
    intraday rails; last-business-day-of-month for monthly_eom) and
    assign a deterministic bundle_id. The per-Rail leg loop then
    stamps that bundle_id onto child legs at emit time so the L1
    stuck_unbundled view stays clean for the baseline.

    Bundle_id format: ``tr-base-bundle-<agg_rail_slug>-<day_seq:04d>``.
    Same shape as a normal transfer_id so the schema sees it as a
    valid Transfer reference.
    """
    last_business_day_per_month = _last_business_day_per_month(state.business_days)

    for rail in instance.rails:
        if not rail.aggregating:
            continue
        if not rail.bundles_activity:
            continue
        agg_slug = _baseline_rail_slug(rail.name)
        cadence = (rail.cadence or "").lower()

        if "monthly" in cadence:
            # Monthly_eom rails fire once at month-end and retroactively
            # bundle EVERY child posted that month. Walk every business
            # day in the window and assign each to the bundle_id keyed
            # off the upcoming month-end firing.
            firing_days = tuple(last_business_day_per_month)
            for day_seq, eom_day in enumerate(firing_days):
                bundle_id = f"tr-base-bundle-{agg_slug}-{day_seq:04d}"
                # Every business day in (year, month) maps to this bundle.
                for d in state.business_days:
                    if d.year == eom_day.year and d.month == eom_day.month:
                        for child_ref in rail.bundles_activity:
                            state.bundle_map[
                                (Identifier(str(child_ref)), d)
                            ] = bundle_id
        else:
            # daily-eod, daily-bod, intraday-* — bundle each business
            # day's children into that day's own bundle_id.
            firing_days = state.business_days
            for day_seq, day in enumerate(firing_days):
                bundle_id = f"tr-base-bundle-{agg_slug}-{day_seq:04d}"
                for child_ref in rail.bundles_activity:
                    # bundles_activity may name a Rail OR TransferTemplate
                    # OR TransferType. Match against rail names; the
                    # other cases would need more sophisticated resolution
                    # but don't appear in the calibration L2 instances yet.
                    state.bundle_map[
                        (Identifier(str(child_ref)), day)
                    ] = bundle_id


def _last_business_day_per_month(
    business_days: tuple[date, ...],
) -> list[date]:
    """Return the last business day in each month covered by ``business_days``.

    Walks the sorted list and notes the last entry seen for each
    (year, month) pair. Used by aggregating monthly_eom rails which
    fire only at month-end.
    """
    last_by_month: dict[tuple[int, int], date] = {}
    for d in business_days:
        last_by_month[(d.year, d.month)] = d
    return sorted(last_by_month.values())


def _emit_baseline_for_aggregating_rail(
    rail: Rail,
    instance: L2Instance,
    state: _BaselineState,
    template_by_role: dict[Identifier, AccountTemplate],
    rng: random.Random,
    counter: _Counter,
    dialect: Dialect,
) -> list[str]:
    """Emit the EOD/EOM parent legs of an aggregating Rail (R.2.c).

    Per R.1.f §6: aggregating rails post their parent at 17:00-19:00 ET
    after children accumulate during the day; monthly_eom rails fire
    only on the last business day of each month. The parent's
    transfer_id matches the bundle_id pre-stamped on the day's
    children (computed in ``_populate_bundle_map``), so the dashboard's
    bundle-membership joins resolve cleanly.

    Currently supports:
      - SingleLegRail: emits one leg per firing.
      - TwoLegRail: emits two legs summing to zero per firing.

    Amount is sampled from the kind's lognormal table (same machinery
    as the per-firing sampler in R.2.b). It does NOT exactly match the
    sum of bundled children — the baseline approximation is acceptable
    given the conservation invariant flags Transfers, not Bundles.
    """
    assert rail.aggregating, (
        "_emit_baseline_for_aggregating_rail called with non-aggregating rail"
    )

    kind = _classify_rail(rail)
    last_business_day_per_month = _last_business_day_per_month(state.business_days)
    cadence = (rail.cadence or "").lower()
    firing_days: tuple[date, ...]
    if "monthly" in cadence:
        firing_days = tuple(last_business_day_per_month)
    else:
        firing_days = state.business_days

    if not firing_days:
        return []

    if isinstance(rail, TwoLegRail):
        src_accounts = _eligible_accounts_for_role(
            rail.source_role, state, instance,
        )
        dst_accounts = _eligible_accounts_for_role(
            rail.destination_role, state, instance,
        )
        if not src_accounts or not dst_accounts:
            return []
        src_origin = (
            str(rail.source_origin) if rail.source_origin is not None
            else (str(rail.origin) if rail.origin is not None else "InternalInitiated")
        )
        dst_origin = (
            str(rail.destination_origin) if rail.destination_origin is not None
            else (str(rail.origin) if rail.origin is not None else "InternalInitiated")
        )
    else:
        assert isinstance(rail, SingleLegRail)
        leg_accounts = _eligible_accounts_for_role(
            rail.leg_role, state, instance,
        )
        if not leg_accounts:
            return []
        src_accounts = leg_accounts
        dst_accounts = []
        src_origin = (
            str(rail.origin) if rail.origin is not None
            else "InternalInitiated"
        )
        dst_origin = src_origin

    cap_by_parent_role = _baseline_cap_lookup(rail, instance)
    rail_slug = _baseline_rail_slug(rail.name)
    rows: list[str] = []

    for day_seq, day in enumerate(firing_days):
        # Parent transfer_id matches the bundle_id stamped on this day's
        # children (see _populate_bundle_map).
        bundle_transfer_id = f"tr-base-bundle-{rail_slug}-{day_seq:04d}"
        n = counter.next()
        # EOD time band per R.1.f §3 — the kind's time_band already
        # reflects 17:00-19:00 for aggregating_daily / aggregating_monthly.
        posting = _baseline_time_of_day(rng, kind, day)

        src = src_accounts[rng.randrange(len(src_accounts))]
        cap = cap_by_parent_role.get(
            str(src.account_parent_role) if src.account_parent_role else ""
        )
        amount = _baseline_amount_sample(rng, kind, cap=cap, rail=rail)
        metadata = _baseline_metadata(rail, n, 0)
        # R.2.d firing log: aggregating-rail parents are also chain
        # parents in some L2 instances.
        state.firings.setdefault(rail.name, []).append(
            (bundle_transfer_id, day, amount),
        )

        if isinstance(rail, TwoLegRail):
            dst = dst_accounts[rng.randrange(len(dst_accounts))]
            txn_id = f"tx-base-{rail_slug}-{n:06d}"
            rows.append(_txn_row(
                id_=f"{txn_id}-src",
                account_id=src.account_id,
                account_name=src.account_name,
                account_role=src.account_role,
                account_scope=src.account_scope,
                account_parent_role=src.account_parent_role,
                money=-amount,
                direction="Debit",
                posting=posting,
                transfer_id=bundle_transfer_id,
                rail_name=rail.name,
                origin=src_origin,
                metadata=metadata,
                dialect=dialect,
            ))
            rows.append(_txn_row(
                id_=txn_id,
                account_id=dst.account_id,
                account_name=dst.account_name,
                account_role=dst.account_role,
                account_scope=dst.account_scope,
                account_parent_role=dst.account_parent_role,
                money=amount,
                direction="Credit",
                posting=posting,
                transfer_id=bundle_transfer_id,
                rail_name=rail.name,
                origin=dst_origin,
                metadata=metadata,
                dialect=dialect,
            ))
            state.account_leg_log.setdefault(src.account_id, []).append(
                (posting, day, -amount),
            )
            state.account_leg_log.setdefault(dst.account_id, []).append(
                (posting, day, amount),
            )
        else:
            assert isinstance(rail, SingleLegRail)
            if rail.leg_direction == "Credit":
                direction, signed = "Credit", amount
            else:
                direction, signed = "Debit", -amount
            txn_id = f"tx-base-{rail_slug}-{n:06d}"
            rows.append(_txn_row(
                id_=txn_id,
                account_id=src.account_id,
                account_name=src.account_name,
                account_role=src.account_role,
                account_scope=src.account_scope,
                account_parent_role=src.account_parent_role,
                money=signed,
                direction=direction,
                posting=posting,
                transfer_id=bundle_transfer_id,
                rail_name=rail.name,
                origin=src_origin,
                metadata=metadata,
                dialect=dialect,
            ))
            state.account_leg_log.setdefault(src.account_id, []).append(
                (posting, day, signed),
            )

    _ = template_by_role
    return rows


def _baseline_rail_slug(rail_name: Identifier) -> str:
    """Convert a Rail name to a short kebab-case ID slug.

    Used in transfer_id / id_ prefixes so rows from different Rails are
    visually distinguishable in the deployed dashboards.
    """
    return "".join(
        c if c.isalnum() else "-"
        for c in str(rail_name).lower()
    ).strip("-")[:32] or "rail"


def _baseline_cap_lookup(
    rail: Rail, instance: L2Instance,
) -> dict[str, Decimal]:
    """Return ``{parent_role: cap}`` for every LimitSchedule matching this Rail.

    Per R.1.f §2: the amount sampler clamps + resamples on cap-exceeding
    draws. The map is keyed by ``parent_role`` so the per-firing picker
    can look up the cap for the source-account's parent role.
    """
    out: dict[str, Decimal] = {}
    for ls in instance.limit_schedules:
        if str(ls.rail) == str(rail.name):
            out[str(ls.parent_role)] = ls.cap
    return out


def _baseline_metadata(
    rail: Rail, n: int, firing_seq: int,
) -> dict[str, str]:
    """Build per-firing metadata satisfying the rail's declared keys.

    Each declared metadata_key gets a synthetic per-firing value
    (``<rail>-firing-NNNNNN``) so two firings of the same rail produce
    distinct values. Rails declaring ``metadata_value_examples`` use
    those values cycling through; the broader-mode plant (R.3) can
    override per-firing.
    """
    out: dict[str, str] = {}
    for key in rail.metadata_keys:
        out[str(key)] = f"{rail.name}-firing-{n:06d}"
    # Per-key example values when declared (M.4.2b mechanism).
    for key, values in rail.metadata_value_examples:
        if values:
            out[str(key)] = values[firing_seq % len(values)]
    return out


def _poisson_sample(rng: random.Random, mean: float) -> int:
    """Sample from Poisson(mean) using Knuth's algorithm.

    Python's ``random`` doesn't expose Poisson; Knuth's iterative
    algorithm is fine for the small means we use (≤ 50). For larger
    means we'd switch to a normal approximation, but the per-day
    targets in R.1.f §1 stay well below that.
    """
    if mean <= 0:
        return 0
    if mean > 50:
        # Normal approximation for large means (sweep accumulators).
        sample = rng.gauss(mean, mean ** 0.5)
        return max(0, int(round(sample)))
    import math

    L = math.exp(-mean)
    k = 0
    p = 1.0
    while True:
        k += 1
        p *= rng.random()
        if p <= L:
            return k - 1


def _unit_firing_template_names(instance: L2Instance) -> set[Identifier]:
    """Templates that emit a coupled UNIT firing — one shared Transfer per
    firing carrying all leg_rails — in ``_emit_baseline_template_firings``.
    Two triggers: a template referenced as a Chain ``parent`` (AG.1 — the
    parent firing the chain overlay threads children onto), OR a template
    declaring ``firings_typical_per_period`` (template-level E8).

    NOTE the asymmetry with ``_unit_firing_leg_rails`` (the per-rail-loop
    SKIP set): that's gated on template-E8 ALONE. A chain-parent template
    without template-E8 unit-fires HERE (for linkage) yet its legs ALSO
    fire independently in the per-rail loop — chain-parenthood is a linkage
    property, not a 'these legs are one balanced event' claim (Gap J
    follow-up, v11.9.3)."""
    tmpl_names = {t.name for t in instance.transfer_templates}
    names: set[Identifier] = {
        chain.parent for chain in instance.chains
        if chain.parent in tmpl_names
    }
    names.update(
        t.name for t in instance.transfer_templates
        if t.firings_typical_per_period is not None
    )
    return names


def _unit_firing_leg_rails(instance: L2Instance) -> set[Identifier]:
    """The leg_rails the baseline per-rail loop SKIPS — they fire ONLY as
    part of a coupled unit firing. Gated on template-level E8
    (``firings_typical_per_period``) opt-in, NOT chain-parenthood: a
    template's legs are a single balanced atomic event only when it
    explicitly declares the E8 band. A chain-parent template WITHOUT
    template-E8 still unit-fires for AG.1 chain linkage (see
    ``_unit_firing_template_names``), but its leg_rails are INDEPENDENT
    events (distinct per-leg volumes / amounts) that must keep firing in
    the per-rail loop — keying the skip off chain-parenthood collapsed
    those independent volumes into one shared per-firing count (Gap J
    follow-up, v11.9.3)."""
    legs: set[Identifier] = set()
    for t in instance.transfer_templates:
        if t.firings_typical_per_period is not None:
            legs.update(t.leg_rails)
    return legs


def _emit_baseline_template_firings(
    instance: L2Instance,
    state: _BaselineState,
    counter: _Counter,
    dialect: Dialect,
    *,
    base_seed: int = _BASELINE_BASE_SEED,
    skip_rails: frozenset[Identifier] = frozenset(),
    only_rails: frozenset[Identifier] | None = None,
) -> list[str]:
    """AG.1 (Gap B) + AL (Gap J): emit balanced UNIT firings for every
    unit-firing TransferTemplate — Chain parents AND templates declaring
    ``firings_typical_per_period`` (``_unit_firing_template_names``). The
    per-rail loop skips the leg_rails of E8-declaring templates only
    (``_unit_firing_leg_rails``), so those fire solely here; a chain-parent
    template without E8 also unit-fires here for linkage but its legs fire
    independently in the per-rail loop too (Gap J follow-up, v11.9.3).

    Templates don't have their own R.2.b firing loop — leg_rails fire
    independently in ``_emit_baseline_for_rail`` and groupings happen at
    chain-child time (``_emit_chain_child_template_legs``). That left a
    blind spot: when a Chain's ``parent`` is a TransferTemplate name,
    ``state.firings.get(chain.parent, [])`` in ``_emit_baseline_chains``
    returned ``[]`` because nothing wrote template-keyed firings. The
    chain emit silently ``continue``'d and the chain produced ZERO rows.
    Cascading effects:

    - L1 ``chain_parent_disagreement`` matview never fires for these
      shapes (no chain-emit rows → no template_name + transfer_parent_id
      pairs to compare).
    - L2FT ``chain_orphans`` dataset false-positives every parent firing
      as orphan (children never link back).

    This helper synthesizes one Template firing per business day for
    every template appearing as a chain parent. Each firing allocates
    ONE shared transfer_id (``tr-base-tmpl-NNNNNN``) and emits each
    leg_rail's row via ``_emit_chain_child_leg`` with ``parent_transfer_id
    =None`` (the template IS the root, no parent ref). Firing-level
    amount + posting time come from the FIRST leg_rail's
    ``_classify_rail`` kind so the daily-balance walk + cap budget see
    consistent shape.

    Records ``(shared_transfer_id, day, amount)`` in
    ``state.firings[template.name]`` BEFORE emitting legs so any
    downstream user of ``state.firings`` mid-pass sees them. The
    subsequent ``_emit_baseline_chains`` pass picks them up as parent
    firings of any Chain whose ``parent`` is this template name.

    AB.3.4 XOR groups are honored — for each firing, one member of each
    XOR group fires (keyed on the shared transfer_id, so the pick is
    stable across leg iterations).

    Templates referenced ONLY as chain children are NOT fired here —
    they emit via ``_emit_chain_child_template_legs`` during
    ``_emit_baseline_chains`` with ``parent_transfer_id`` set correctly.
    """
    if not instance.transfer_templates:
        return []

    templates_by_name = {t.name: t for t in instance.transfer_templates}
    unit_firing_templates = _unit_firing_template_names(instance)
    if not unit_firing_templates:
        return []

    rails_by_name = {r.name: r for r in instance.rails}
    rows: list[str] = []

    for tmpl_name in sorted(unit_firing_templates, key=str):
        template = templates_by_name[tmpl_name]
        if not template.leg_rails:
            continue
        first_leg = rails_by_name.get(template.leg_rails[0])
        if first_leg is None:
            continue
        # AL (Gap J): the per-rail loop now skips unit-firing-template legs
        # (they fire only as this unit), so "did the first leg fire in the
        # rail loop?" is no longer a valid operator-filter proxy — honor
        # skip_rails / only_rails (X.4.g.10 / X.4.i.1) directly instead.
        if first_leg.name in skip_rails:
            continue
        if only_rails is not None and first_leg.name not in only_rails:
            continue

        kind = _classify_rail(first_leg)
        # Per-template RNG keyed off base_seed + template name — changes
        # to other templates don't ripple into this template's firings.
        tmpl_rng = random.Random(
            base_seed ^ (zlib.crc32(str(tmpl_name).encode("utf-8")) & 0x7FFFFFFF),
        )

        # AF (E8): when the template declares firings_typical_per_period,
        # the total firing count comes from the declared band; else the
        # AG.1 default of one firing per business day. _pick_firings_count
        # consumes no rng when the field is absent, so AG.1's locked seed
        # stays byte-identical for templates that don't declare it. Total
        # firings are round-robined across business days (firing i lands
        # on business_days[i % N]) — for the default total == N this
        # reproduces the original one-per-day order exactly.
        n_business_days = len(state.business_days)
        total_firings = _pick_firings_count(
            template,
            business_day_count=n_business_days,
            rng=tmpl_rng,
            fallback=n_business_days,
        )

        for firing_i in range(total_firings):
            day = state.business_days[firing_i % n_business_days]
            n_shared = counter.next()
            shared_transfer_id = f"tr-base-tmpl-{n_shared:06d}"

            # Firing-level amount is sampled once from the first
            # leg_rail's distribution and passed to each leg emit as
            # parent_amount — the legs themselves sample their own
            # leg-level amounts internally for the row's `money` value;
            # this firing-level amount is what state.firings carries
            # for downstream chain-emit pickup.
            firing_amount = _baseline_amount_sample(
                tmpl_rng, kind, cap=None, rail=first_leg,
            )

            # Record firing BEFORE emitting legs so any read-during-emit
            # of state.firings (defensive future-proofing) sees it.
            state.firings.setdefault(tmpl_name, []).append(
                (shared_transfer_id, day, firing_amount),
            )

            # AB.3.4 XOR suppression keyed on the firing's shared id.
            xor_suppressed = _xor_suppressed_members(
                template, firing_id=shared_transfer_id,
            )

            for leg_rail_name in template.leg_rails:
                if str(leg_rail_name) in xor_suppressed:
                    continue
                leg_rail = rails_by_name.get(leg_rail_name)
                if leg_rail is None:
                    continue
                leg_rows = _emit_chain_child_leg(
                    leg_rail, instance, state,
                    None,  # parent_transfer_id — template is the root
                    day, firing_amount,
                    counter, tmpl_rng, dialect,
                    shared_transfer_id=shared_transfer_id,
                    template_name=tmpl_name,
                )
                rows.extend(leg_rows)
    return rows


def _emit_baseline_chains(
    instance: L2Instance,
    state: _BaselineState,
    template_by_role: dict[Identifier, AccountTemplate],
    counter: _Counter,
    dialect: Dialect,
    *,
    base_seed: int = _BASELINE_BASE_SEED,
) -> list[str]:
    """Emit chain-firing rows (parent → child) for every declared Chain.

    R.2.d implementation, post-Z.A grammar collapse:
      - For each Chain row, look up the parent's firings from
        ``state.firings``. For each parent firing, deterministically
        pick one child from ``chain.children`` (hash of the parent's
        transfer_id); singleton-children rows always pick that one
        child.
      - Roll a completion check: singleton ≈95% (Z.A "required"
        semantics — parent firing always invokes this child), multi
        ≈50% (Z.A "XOR" semantics — exactly one sibling fires per
        parent invocation, but optional in the failure-injection
        sense).
      - Emit a child leg with ``transfer_parent_id = parent_transfer_id``,
        sampled from the child rail's lognormal kind; time-of-day
        shifts to one hour after the parent's posting band.

    Children whose rail isn't in ``instance.rails`` (Chain may also
    name a TransferTemplate) are skipped — full TransferTemplate
    chain emission is out of scope for R.2.d's first land.
    """
    if not instance.chains:
        return []

    rails_by_name = {r.name: r for r in instance.rails}
    rng = random.Random(base_seed ^ 0xCC11A)
    rows: list[str] = []

    # Iterate Chains in deterministic order. Under Z.A multiple Chain
    # rows MAY share a parent (disjoint XOR groups), so the secondary
    # sort key is the sorted-children CSV — same composite key the
    # editor uses to address chain rows.
    for chain in sorted(
        instance.chains,
        key=lambda c: (str(c.parent), ",".join(sorted(str(ch.name) for ch in c.children))),
    ):
        parent_firings = state.firings.get(chain.parent, [])
        if not parent_firings:
            continue

        # AB.6 (mixed-cardinality): a chain may carry both fan_in
        # children (N parents → 1 shared child Transfer) AND non-fan_in
        # children (XOR alternation: each parent picks one of these).
        # Both buckets emit independently; a single parent firing may
        # contribute to BOTH (one XOR pick + one batch contribution per
        # fan_in entry).
        fan_in_children = tuple(c for c in chain.children if c.fan_in)
        non_fan_in_children = tuple(c for c in chain.children if not c.fan_in)

        # AB.4.4: emit batched-firing rows for every fan_in entry.
        # `_emit_fan_in_chain_firings` accumulates parents into batches
        # of `expected_parent_count` and emits all-legs-share-transfer_id
        # rows; AB.4.3 transfer_parents matview reads DISTINCT(child,
        # parent); AB.4.7 fan_in_disagreement joins against the entry's
        # expected_parent_count.
        for fan_in_child in fan_in_children:
            rows.extend(_emit_fan_in_chain_firings(
                chain, fan_in_child, parent_firings,
                instance, state, counter, rng, dialect,
            ))

        # No non-fan_in children → no XOR/required emission for this chain.
        if not non_fan_in_children:
            continue

        is_required = len(non_fan_in_children) == 1

        for parent_transfer_id, parent_day, parent_amount in parent_firings:
            # AG.2: per-firing XOR pick via _baseline_xor_child_pick.
            # Returns the lone non-fan_in child for singleton chains and
            # one of the non-fan_in entries (RNG-keyed on chain.parent +
            # parent_transfer_id) for multi-children chains. None when
            # the children list is entirely fan_in — defensive skip; the
            # fan_in path above already handled them.
            child_name_picked = _baseline_xor_child_pick(
                chain, parent_transfer_id, base_seed,
            )
            if child_name_picked is None:
                continue
            child_name = child_name_picked

            # AG.2 (Gap C): singleton-children chains keep the 5% baseline
            # orphan noise — `<prefix>_chain_orphans` reads this as the
            # required-but-missed baseline shape (intentional per Z.A
            # grammar). Multi-children chains MUST fire exactly one child
            # per parent invocation per chain.md XOR contract — no
            # completion roll, the pick IS the fire. Pre-AG.2 the multi
            # path rolled 50% and produced `_multi_xor_violation` rows
            # tagged 'missed' on healthy baseline.
            if is_required and rng.random() > 0.95:
                continue  # parent fired but required child did not — orphan exception

            child_rail = rails_by_name.get(child_name)
            if child_rail is None:
                # AB.2.5: chain.children entry isn't a Rail — check if
                # it's a TransferTemplate name (template-as-chain-child
                # case, gap doc §3). Each leg_rail of the chain-child
                # template fires once per chain invocation, all sharing
                # ONE child Transfer (lookup-or-create on transfer_id,
                # first-firing-wins per gap doc §3). All leg firings
                # carry the same parent_transfer_id so the AB.2.3
                # chain_parent_disagreement matview reads cardinality=1
                # for the healthy case.
                child_template = next(
                    (t for t in instance.transfer_templates if t.name == child_name),
                    None,
                )
                if child_template is None:
                    # Unknown name (validator should have caught at R5).
                    continue
                child_rows = _emit_chain_child_template_legs(
                    child_template, instance, state,
                    parent_transfer_id, parent_day, parent_amount,
                    counter, rng, dialect,
                )
                rows.extend(child_rows)
                continue

            child_rows = _emit_chain_child_leg(
                child_rail, instance, state,
                parent_transfer_id, parent_day, parent_amount,
                counter, rng, dialect,
            )
            rows.extend(child_rows)

    _ = template_by_role
    return rows


def _baseline_xor_child_pick(
    chain: Chain, parent_transfer_id: str, base_seed: int,
) -> Identifier | None:
    """AG.2 (Gap C): pick exactly one non-fan_in child per parent firing
    for the chain.md multi-children XOR contract.

    chain.md prose: "Two or more children = XOR alternation. Exactly one
    of the listed children MUST fire per parent invocation." Pre-AG.2,
    ``_emit_baseline_chains`` rolled a 50% completion threshold on
    multi-children chains, so ~half of parent firings emitted zero
    children — ``<prefix>_multi_xor_violation`` reported them as
    ``disagreement_kind='missed'`` on what should be a healthy
    baseline. This helper centralizes the per-firing pick so the
    completion threshold is bound to chain shape (singleton vs multi)
    at one site, not threaded through ad-hoc rng calls.

    Returns ``None`` when the chain has no non-fan_in children — a
    chain whose children list is entirely fan_in has no XOR pick to
    make. fan_in firings happen via ``_emit_fan_in_chain_firings`` per
    AB.4 semantics, orthogonal to this picker.

    Mirrors AB.3's ``_xor_suppressed_members`` keying pattern:
    deterministic ``crc32(chain.parent | parent_transfer_id)`` keyed
    RNG so the pick is rename-resilient on child names and stable
    across re-runs at the same base_seed. Singleton (1 non-fan_in
    child) returns the lone child by definition — the helper still
    routes through here so the caller doesn't need to special-case it.
    """
    non_fan_in = [c for c in chain.children if not c.fan_in]
    if not non_fan_in:
        return None
    if len(non_fan_in) == 1:
        return non_fan_in[0].name
    pick_seed = base_seed ^ (
        zlib.crc32(
            f"{chain.parent}|{parent_transfer_id}".encode("utf-8"),
        ) & 0x7FFFFFFF
    )
    pick_rng = random.Random(pick_seed)
    return pick_rng.choice(non_fan_in).name


def _xor_suppressed_members(
    template: TransferTemplate, *, firing_id: str,
) -> set[str]:
    """AB.3.4: per-firing XOR resolution for a TransferTemplate.

    For each entry in ``template.leg_rail_xor_groups``, deterministically
    pick one member by ``zlib.crc32`` over ``template_name|group_index
    |firing_id`` and return the *other* members so callers can suppress
    them. Returns an empty set when the template declares no XOR groups
    (every pre-AB.3 template is byte-equivalent through this helper).

    The picker keys off ``firing_id`` — caller passes the chain parent's
    ``parent_transfer_id``, which is stable across seeds AND across
    ``scope:`` changes (re-running with a different scope doesn't shift
    which variant fires for any given chain invocation, per AB.3.0
    lock). Picker is independent of the seeded ``random.Random`` state
    so RNG-consuming changes elsewhere in the seed don't ripple into
    XOR pick decisions.
    """
    if not template.leg_rail_xor_groups:
        return set()
    suppressed: set[str] = set()
    template_name = str(template.name)
    for gi, group in enumerate(template.leg_rail_xor_groups):
        key = f"{template_name}|{gi}|{firing_id}"
        h = zlib.crc32(key.encode("utf-8")) & 0x7FFFFFFF
        picked = str(group[h % len(group)])
        for member in group:
            m = str(member)
            if m != picked:
                suppressed.add(m)
    return suppressed


def _emit_chain_child_template_legs(
    child_template: TransferTemplate,
    instance: L2Instance,
    state: _BaselineState,
    parent_transfer_id: str,
    parent_day: date,
    parent_amount: Decimal,
    counter: _Counter,
    rng: random.Random,
    dialect: Dialect,
) -> list[str]:
    """AB.2.5: emit one chain-firing where the child is a TransferTemplate.

    First-firing-wins per gap doc §3: every leg_rail of ``child_template``
    fires once per chain invocation, all sharing one child Transfer
    (`transfer_id`) and the same `parent_transfer_id`. The AB.2.3
    `<prefix>_chain_parent_disagreement` matview groups by transfer_id
    and asserts `COUNT(DISTINCT parent_transfer_id) <= 1` — healthy
    firings emitted here read cardinality=1 (no violation).

    Each leg_rail emits via `_emit_chain_child_leg` semantics but with
    the shared ``transfer_id`` and ``template_name`` injected, so
    multiple leg rows aggregate into one Transfer in the matview's
    GROUP BY. Per-leg amount samples + per-leg account selection stay
    independent (each leg_rail has its own role bindings).

    AB.3.4: when ``child_template.leg_rail_xor_groups`` is non-empty,
    exactly one member of each XOR group fires per chain invocation;
    the other members are suppressed via ``_xor_suppressed_members``.
    The AB.3.3 ``<prefix>_xor_group_violation`` matview then reads
    ``firing_count = 1`` per (transfer_id, template, group_index) for
    healthy baseline firings — no violation. Templates with empty
    ``leg_rail_xor_groups`` see no behavioral change (suppressed set
    is empty).
    """
    rails_by_name = {r.name: r for r in instance.rails}
    # One shared transfer_id for the child Transfer — every leg_rail
    # firing of this chain invocation reuses it (first-firing-wins).
    n_shared = counter.next()
    shared_transfer_id = f"tr-base-chain-tmpl-{n_shared:06d}"

    xor_suppressed = _xor_suppressed_members(
        child_template, firing_id=parent_transfer_id,
    )

    rows: list[str] = []
    for leg_rail_name in child_template.leg_rails:
        if str(leg_rail_name) in xor_suppressed:
            continue
        leg_rail = rails_by_name.get(leg_rail_name)
        if leg_rail is None:
            # Validator R3 should reject this at load time; defensive skip.
            continue
        leg_rows = _emit_chain_child_leg(
            leg_rail, instance, state,
            parent_transfer_id, parent_day, parent_amount,
            counter, rng, dialect,
            shared_transfer_id=shared_transfer_id,
            template_name=child_template.name,
        )
        rows.extend(leg_rows)
    return rows


def _emit_fan_in_chain_firings(
    chain: Chain,
    fan_in_child: ChainChildSpec,
    parent_firings: list[tuple[str, date, Decimal]],
    instance: L2Instance,
    state: _BaselineState,
    counter: _Counter,
    rng: random.Random,
    dialect: Dialect,
) -> list[str]:
    """AB.4.4 (AB.6 per-child): emit chain firings where N parent
    firings share one child Transfer (the batched-payout pattern),
    for a single per-child fan_in entry.

    AB.6 (2026-05-19) — caller passes the ``fan_in_child`` explicitly
    so the main chain loop can iterate every fan_in entry under a
    mixed-cardinality chain (AB.6.6.sasq's fold lock landed: one
    chain can carry both XOR-alternation children AND a fan-in
    child — the MerchantSettlementCycle + MerchantWeeklyPayoutBatch
    shape).

    Per AB.4.0 lock: parents are grouped into batches of size
    ``fan_in_child.expected_parent_count`` (default 2 when unset —
    the minimum non-orphan). Each batch:

    - Allocates ONE shared ``child_transfer_id`` (``tr-base-fanin-NNNNNN``).
    - Each parent in the batch emits a FULL set of the child template's
      leg_rails, with each leg's ``transfer_parent_id`` = that parent's
      transfer_id and ``transfer_id`` = the batch's shared id.
    - DISTINCT over (child_transfer_id, transfer_parent_id) in the
      AB.4.3 ``_transfer_parents`` matview yields ``batch_size`` rows
      per child Transfer (the multi-parent set).

    Per AB.6 validator C8a, every per-child entry with ``fan_in=True``
    resolves to a TransferTemplate — enforced at load time. Defensive
    skip if somehow a non-template snuck through.

    XOR suppression: per AB.3.4, the XOR pick is keyed off the firing
    id. For fan_in entries, the firing id is the shared
    ``child_transfer_id`` (all parents in the batch produce one
    logical firing of the child template — the picks need to agree
    across parents so the resulting batch has consistent variant
    membership).

    Partial-tail batches (fewer than ``batch_size`` parents at the
    end) are SKIPPED in baseline emission — they'd be orphan-shaped
    rows that the plant path (AB.4.5) covers instead. The baseline's
    job is to populate the healthy case.
    """
    if not fan_in_child.fan_in:
        # Defensive: caller is responsible for filtering to fan_in entries.
        return []
    child_name = fan_in_child.name
    child_template = next(
        (t for t in instance.transfer_templates if t.name == child_name),
        None,
    )
    if child_template is None:
        return []
    rails_by_name = {r.name: r for r in instance.rails}

    # Default batch size = 2 (the minimum non-orphan) when
    # expected_parent_count is unset (variable-batch-size flow).
    batch_size = fan_in_child.expected_parent_count or 2
    rows: list[str] = []

    n_parents = len(parent_firings)
    n_batches = n_parents // batch_size  # drop partial tail
    for batch_idx in range(n_batches):
        batch = parent_firings[
            batch_idx * batch_size:(batch_idx + 1) * batch_size
        ]
        n_shared = counter.next()
        shared_transfer_id = f"tr-base-fanin-{n_shared:06d}"

        xor_suppressed = _xor_suppressed_members(
            child_template, firing_id=shared_transfer_id,
        )

        for parent_transfer_id, parent_day, parent_amount in batch:
            for leg_rail_name in child_template.leg_rails:
                if str(leg_rail_name) in xor_suppressed:
                    continue
                leg_rail = rails_by_name.get(leg_rail_name)
                if leg_rail is None:
                    # Validator R3 catches at load time; defensive skip.
                    continue
                leg_rows = _emit_chain_child_leg(
                    leg_rail, instance, state,
                    parent_transfer_id, parent_day, parent_amount,
                    counter, rng, dialect,
                    shared_transfer_id=shared_transfer_id,
                    template_name=child_template.name,
                )
                rows.extend(leg_rows)
    return rows


def _emit_chain_child_leg(
    child_rail: Rail,
    instance: L2Instance,
    state: _BaselineState,
    parent_transfer_id: str | None,
    parent_day: date,
    parent_amount: Decimal,
    counter: _Counter,
    rng: random.Random,
    dialect: Dialect,
    *,
    shared_transfer_id: str | None = None,
    template_name: Identifier | None = None,
) -> list[str]:
    """Emit one child Transfer's legs linked to the parent firing.

    The child Transfer fires on the same business day as the parent,
    one hour after the parent's posting band per R.1.f §6. Amount is
    sampled from the child rail's lognormal kind (or the parent's
    amount × 0.5 if the child rail isn't classifiable). Honors the
    L2's LimitSchedule cap on the child via clamp+resample.

    AB.2.5: when ``shared_transfer_id`` is provided (template-as-chain
    -child case), the leg(s) use that transfer_id instead of generating
    a fresh one — multiple leg_rail firings of the same chain invocation
    aggregate into one Transfer via shared id (first-firing-wins per
    gap doc §3). ``template_name`` is set on every emitted row so the
    AB.2.3 chain_parent_disagreement matview's `template_name IS NOT
    NULL` filter catches them. txn_id stays per-leg so individual
    transactions remain addressable.

    AG.1: ``parent_transfer_id`` is ``None`` when this helper is reused
    from ``_emit_baseline_template_firings`` to emit Template-parent
    leg_rail rows — the template IS the root of the chain hierarchy so
    its own legs carry no parent ref. ``_txn_row`` writes the column as
    SQL NULL in that case.
    """
    kind = _classify_rail(child_rail)
    rail_slug = _baseline_rail_slug(child_rail.name)
    n = counter.next()
    transfer_id = shared_transfer_id or f"tr-base-chain-{rail_slug}-{n:06d}"
    txn_id = f"tx-base-chain-{rail_slug}-{n:06d}"

    # Chain child posts ~1 hour after the parent's natural band end —
    # use 18:00 as a reasonable default (after most parent bands close
    # but before EOD bundling).
    posting = f"{parent_day.isoformat()}T18:00:00+00:00"

    # Resolve eligible accounts. If child rail has no eligible
    # accounts, can't emit — return empty.
    if isinstance(child_rail, TwoLegRail):
        src_accounts = _eligible_accounts_for_role(
            child_rail.source_role, state, instance,
        )
        dst_accounts = _eligible_accounts_for_role(
            child_rail.destination_role, state, instance,
        )
        if not src_accounts or not dst_accounts:
            return []
    else:
        assert isinstance(child_rail, SingleLegRail)
        leg_accounts = _eligible_accounts_for_role(
            child_rail.leg_role, state, instance,
        )
        if not leg_accounts:
            return []
        src_accounts = leg_accounts
        dst_accounts = []

    cap_by_parent_role = _baseline_cap_lookup(child_rail, instance)
    src = src_accounts[rng.randrange(len(src_accounts))]
    cap = cap_by_parent_role.get(
        str(src.account_parent_role) if src.account_parent_role else "",
    )
    # V.5.a — same per-(source-account, transfer_type, day) cap tracker
    # as the per-Rail loop. Chain children share the cap budget with the
    # rest of the day's outbound on the same source so the matview's
    # SUM-over-day comparison stays under cap.
    cap_key: tuple[Identifier, str, date] | None = None
    cap_accumulated: Decimal = Decimal(0)
    if (
        cap is not None
        and str(src.account_scope) == "internal"
        and src.account_parent_role is not None
    ):
        cap_key = (src.account_id, str(child_rail.name), parent_day)
        cap_accumulated = state.daily_outbound_by_account_type.get(
            cap_key, Decimal(0),
        )
        remaining_cap = cap - cap_accumulated
        if remaining_cap < Decimal("50"):
            return []  # daily cap exhausted; skip this child firing
        amount = _baseline_amount_sample(
            rng, kind, cap=remaining_cap, rail=child_rail,
        )
    else:
        # Sample from the child rail's distribution. Slightly conservative
        # vs the parent_amount so the chain looks like a downstream
        # transfer of part of the parent.
        amount = _baseline_amount_sample(rng, kind, cap=cap, rail=child_rail)
    _ = parent_amount  # reserved — could constrain to <= parent in the future

    metadata = _baseline_metadata(child_rail, n, 0)

    if isinstance(child_rail, TwoLegRail):
        dst = dst_accounts[rng.randrange(len(dst_accounts))]
        src_origin = (
            str(child_rail.source_origin) if child_rail.source_origin is not None
            else (str(child_rail.origin) if child_rail.origin is not None else "InternalInitiated")
        )
        dst_origin = (
            str(child_rail.destination_origin) if child_rail.destination_origin is not None
            else (str(child_rail.origin) if child_rail.origin is not None else "InternalInitiated")
        )
        rows = [
            _txn_row(
                id_=f"{txn_id}-src",
                account_id=src.account_id,
                account_name=src.account_name,
                account_role=src.account_role,
                account_scope=src.account_scope,
                account_parent_role=src.account_parent_role,
                money=-amount,
                direction="Debit",
                posting=posting,
                transfer_id=transfer_id,
                rail_name=child_rail.name,
                origin=src_origin,
                metadata=metadata,
                template_name=template_name,
                transfer_parent_id=parent_transfer_id,
                dialect=dialect,
            ),
            _txn_row(
                id_=txn_id,
                account_id=dst.account_id,
                account_name=dst.account_name,
                account_role=dst.account_role,
                account_scope=dst.account_scope,
                account_parent_role=dst.account_parent_role,
                money=amount,
                direction="Credit",
                posting=posting,
                transfer_id=transfer_id,
                rail_name=child_rail.name,
                origin=dst_origin,
                metadata=metadata,
                template_name=template_name,
                transfer_parent_id=parent_transfer_id,
                dialect=dialect,
            ),
        ]
        state.account_leg_log.setdefault(src.account_id, []).append(
            (posting, parent_day, -amount),
        )
        state.account_leg_log.setdefault(dst.account_id, []).append(
            (posting, parent_day, amount),
        )
        if cap_key is not None:
            state.daily_outbound_by_account_type[cap_key] = (
                cap_accumulated + amount
            )
    else:
        assert isinstance(child_rail, SingleLegRail)
        if child_rail.leg_direction == "Credit":
            direction, signed = "Credit", amount
        else:
            direction, signed = "Debit", -amount
        leg_origin = (
            str(child_rail.origin) if child_rail.origin is not None
            else "InternalInitiated"
        )
        rows = [_txn_row(
            id_=txn_id,
            account_id=src.account_id,
            account_name=src.account_name,
            account_role=src.account_role,
            account_scope=src.account_scope,
            account_parent_role=src.account_parent_role,
            money=signed,
            direction=direction,
            posting=posting,
            transfer_id=transfer_id,
            rail_name=child_rail.name,
            origin=leg_origin,
            metadata=metadata,
            template_name=template_name,
            transfer_parent_id=parent_transfer_id,
            dialect=dialect,
        )]
        state.account_leg_log.setdefault(src.account_id, []).append(
            (posting, parent_day, signed),
        )
        if cap_key is not None and direction == "Debit":
            state.daily_outbound_by_account_type[cap_key] = (
                cap_accumulated + amount
            )

    # Record the chain child as its own firing too — supports nested
    # chains in future L2 instances.
    state.firings.setdefault(child_rail.name, []).append(
        (transfer_id, parent_day, amount),
    )

    return rows


# -- V.5.b — Cascade credits for intermediate clearing accounts --------------
#
# Several internal "clearing" / "suspense" / "sub-account" roles in
# real-world cascades only see one side of a downstream debit during
# baseline emission — the matching credit-side leg lives in a
# TransferTemplate cycle that the baseline emitter doesn't materialize,
# or in a per-(merchant, day) settlement leg that's structurally
# implicit. The leg-loop emits the debit; the cumulative-from-window-
# start daily-balance walk then drives those accounts negative once
# their starting balance cushion is exhausted, surfacing as L1
# overdraft false positives.
#
# Patterns (per V.5 design):
#   - Aggregating-rail bundled child cascades: e.g. ACHOriginationDailySweep's
#     `bundles_activity: [CustomerOutboundACH]`. The aggregating rail's
#     EOD parent debits ACHOrigSettlement → CashDueFRB. But there's no
#     upstream credit landing on ACHOrigSettlement — `CustomerOutboundACH`
#     goes CustomerDDA → ExternalCounterparty in baseline, never crediting
#     the settlement GL.
#   - TransferTemplate leg cascades: MerchantPayoutACH/Wire/Check debit
#     `MerchantPayableClearing`; the matching credit shape is what the
#     user calls a `CardSaleDailySettlement` (per-merchant-per-day).
#     Same idea for InternalTransferSuspenseClose debiting
#     `InternalTransferSuspense` — the matching credit lives in the
#     `InternalTransferCycle` template that baseline doesn't materialize.
#   - ZBA sub-account funding: ZBASweep debits the sub-account; nothing
#     credits it. Per user's V.5 design Q2, model a daily inbound from
#     the funds-pool that matches the daily ZBASweep volume.
#
# Helper structure: one pass after rails + chains have populated
# state.firings. For each cascade pattern, walk the matching firings
# in (rail_name, transfer_id) sort order and emit a paired credit leg
# ~30 minutes BEFORE the original debit (so the cumulative-sum walk
# shows clearing GL going positive then back to zero on the same day).
# Counter-leg posts to a chosen external counterparty so the cascade
# leg pair nets to zero.
#
# Determinism: dedicated RNG seed `_BASELINE_BASE_SEED ^ 0xCA5CAD`. The
# helper only reads from state.firings + state.account_leg_log; it
# emits new rows + appends to account_leg_log so the deferred daily-
# balance walk picks them up.


_BALANCE_MAINTENANCE_RAIL = "InternalBalanceMaintenance"


def _balance_maintenance_rail(instance: L2Instance) -> Identifier | None:
    """AJ.4b — the declared internal balance-maintenance rail, if any.

    Cascade-credit + opening-balance legs are demo balance-maintenance
    scaffolding (they net to zero / fund starting balances so the demo's
    cumulative-balance walk stays positive). They MUST carry a declared
    ``rail_name`` to satisfy the ``unmatched_rail_name`` invariant — but
    tagging them with a real money-movement rail makes every firing-count
    analysis (``chain_orphans`` / ``multi_xor_violation`` / the L2FT rails
    sheet) count them as firings of that rail (the 2615-row
    ``ACHOriginationDailySweep`` false-orphan flood). The fix: tag them
    with a dedicated internal rail (by convention named
    ``InternalBalanceMaintenance``) that is NOT a chain parent, so they
    pass rail-conformance without masquerading as firings.

    Returns ``None`` for L2s that don't declare it — those keep the legacy
    behavior (legs tagged with the cascaded / funding rail). The bundled
    fixtures declare it; the contract is opt-in so arbitrary / fuzzed L2s
    don't break.
    """
    for r in instance.rails:
        if str(r.name) == _BALANCE_MAINTENANCE_RAIL:
            return r.name
    return None


def _emit_baseline_cascade_credits(
    instance: L2Instance,
    state: _BaselineState,
    counter: _Counter,
    dialect: Dialect,
    *,
    base_seed: int = _BASELINE_BASE_SEED,
) -> list[str]:
    """V.5.b — emit paired credit legs for cascade-debit patterns.

    Walks ``state.firings`` for each known cascade trigger and emits a
    matching credit leg into the cascade target account. Counter-leg
    debits a chosen external counterparty so the pair nets to zero.

    Triggers (per V.5 design):

    1. **Aggregating-rail bundled-child cascades** — for every
       aggregating Rail with ``bundles_activity``, walk firings of each
       bundled rail and emit a credit on the aggregating rail's
       ``source_role``. The aggregating EOD parent then has its debit
       balanced by the cascade credits.

    2. **TransferTemplate leg cascades** — for every TransferTemplate
       whose ``leg_rails`` includes a single-leg Variable / Debit-only
       rail (e.g. ``InternalTransferSuspenseClose``), walk that rail's
       firings and emit a paired credit on the same account so the
       template-cycle "missing credit half" doesn't drive the suspense
       GL negative.

    3. **MerchantPayout cascade** — for every Rail whose name starts
       with ``MerchantPayout`` (the ``MerchantSettlementCycle`` chain's
       PayoutVehicle XOR group), emit a paired credit on the rail's
       ``source_role`` (``MerchantPayableClearing``) per firing. Models
       the per-merchant per-day ``CardSaleDailySettlement`` shape.

    4. **ZBA sub-account funding** — for every firing of a Rail whose
       ``source_role`` is a template-instance role with
       ``parent_role: ConcentrationMaster`` (i.e., ZBASubAccount via
       ZBASweep), emit a paired credit on the sub-account so the
       cumulative balance never goes negative.

    No-op for L2 instances that don't declare any of these patterns
    (e.g., spec_example, which has no aggregating rails with bundles_
    activity, no TransferTemplate variable-direction legs, no
    MerchantPayout rails, and no ConcentrationMaster-parented
    template instances). Helper returns ``[]`` cleanly.
    """
    rng = random.Random(base_seed ^ 0xCA5CAD)
    rows: list[str] = []

    # Pick a default external counterparty for the counter-leg side.
    # Mirror the strategy in _emit_opening_balance_rows: first
    # external-scope account in sorted order.
    external_account: _ResolvedAccount | None = None
    for a in sorted(instance.accounts, key=lambda a: str(a.id)):
        if str(a.scope) == "external":
            external_account = _ResolvedAccount(
                account_id=a.id,
                account_name=a.name or Name(str(a.id)),
                account_role=a.role or Identifier(str(a.id)),
                account_scope=a.scope,
                account_parent_role=a.parent_role,
            )
            break
    if external_account is None:
        return []

    rails_by_name: dict[Identifier, Rail] = {r.name: r for r in instance.rails}
    # AJ.4b — tag every cascade leg with the internal balance-maintenance
    # rail (when declared) so the synthetic credits don't masquerade as
    # firings of the money-movement rail they balance.
    bm = _balance_maintenance_rail(instance)

    # ---- Pattern 1: aggregating-rail bundled-child cascades ----
    # For each aggregating Rail (TwoLegRail) with bundles_activity, walk
    # the bundled rail's firings already in state.firings. Each firing
    # gets a paired credit on the aggregating rail's source_role.
    agg_cascade_targets: list[
        tuple[Identifier, _ResolvedAccount, Identifier]
    ] = []
    for agg_rail in sorted(instance.rails, key=lambda r: str(r.name)):
        if not agg_rail.aggregating:
            continue
        if not isinstance(agg_rail, TwoLegRail):
            continue
        if not agg_rail.bundles_activity:
            continue
        # Resolve the source-role's account (singleton in instance.accounts).
        target = _resolve_internal_singleton_for_role(
            agg_rail.source_role, instance,
        )
        if target is None:
            continue
        for child_ref in agg_rail.bundles_activity:
            child_rail_name = Identifier(str(child_ref))
            if child_rail_name not in rails_by_name:
                continue
            agg_cascade_targets.append((
                child_rail_name,
                target,
                agg_rail.name,
            ))

    for child_rail_name, target, label_rail in (
        agg_cascade_targets
    ):
        firings = sorted(
            state.firings.get(child_rail_name, []),
            key=lambda f: (f[1], f[0]),  # (day, transfer_id)
        )
        for parent_transfer_id, day, amount in firings:
            rows.extend(_emit_cascade_pair(
                state=state,
                target=target,
                external=external_account,
                amount=amount,
                day=day,
                source_transfer_id=parent_transfer_id,
                cascade_label=label_rail,
                balance_rail=bm,
                counter=counter,
                rng=rng,
                dialect=dialect,
            ))

    # ---- Pattern 2: TransferTemplate single-leg variable cascades ----
    # For every TransferTemplate, walk its leg_rails and find any
    # SingleLegRail whose leg_direction defaults to Debit (Variable /
    # Debit). Emit a paired credit on each such firing's account.
    tt_variable_rails: list[Identifier] = []
    for tt in sorted(instance.transfer_templates, key=lambda t: str(t.name)):
        for leg_rail_name in tt.leg_rails:
            r = rails_by_name.get(Identifier(str(leg_rail_name)))
            if r is None:
                continue
            if not isinstance(r, SingleLegRail):
                continue
            # Variable-direction defaults to Debit per the seed loop's
            # leg-direction handling; static-Debit also qualifies.
            if r.leg_direction not in ("Debit", "Variable"):
                continue
            tt_variable_rails.append(r.name)

    for rail_name in tt_variable_rails:
        rail = rails_by_name.get(rail_name)
        if rail is None or not isinstance(rail, SingleLegRail):
            continue
        target = _resolve_internal_singleton_for_role(
            rail.leg_role, instance,
        )
        if target is None:
            continue
        firings = sorted(
            state.firings.get(rail_name, []),
            key=lambda f: (f[1], f[0]),
        )
        for parent_transfer_id, day, amount in firings:
            rows.extend(_emit_cascade_pair(
                state=state,
                target=target,
                external=external_account,
                amount=amount,
                day=day,
                source_transfer_id=parent_transfer_id,
                cascade_label=rail.name,
                balance_rail=bm,
                counter=counter,
                rng=rng,
                dialect=dialect,
            ))

    # ---- Pattern 3: MerchantPayout cascade ----
    # For rails whose name starts with "MerchantPayout", emit a paired
    # credit on the rail's source_role (MerchantPayableClearing) per
    # firing. Models the CardSaleDailySettlement per-merchant per-day
    # credit shape called out in the V.5 design.
    for rail in sorted(instance.rails, key=lambda r: str(r.name)):
        if not str(rail.name).startswith("MerchantPayout"):
            continue
        if not isinstance(rail, TwoLegRail):
            continue
        target = _resolve_internal_singleton_for_role(
            rail.source_role, instance,
        )
        if target is None:
            continue
        firings = sorted(
            state.firings.get(rail.name, []),
            key=lambda f: (f[1], f[0]),
        )
        for parent_transfer_id, day, amount in firings:
            rows.extend(_emit_cascade_pair(
                state=state,
                target=target,
                external=external_account,
                amount=amount,
                day=day,
                source_transfer_id=parent_transfer_id,
                cascade_label=Identifier("CardSaleDailySettlement"),
                balance_rail=bm,
                counter=counter,
                rng=rng,
                dialect=dialect,
            ))

    # ---- Pattern 4: ZBA sub-account funding ----
    # For every firing of a Rail whose source_role resolves to template
    # instances under a ConcentrationMaster parent (ZBASubAccount via
    # ZBASweep), emit a paired credit on the SAME sub-account that the
    # firing debited so the cumulative balance walks back to zero.
    template_parent_roles: dict[Identifier, Identifier | None] = {
        t.role: t.parent_role for t in instance.account_templates
    }
    template_role_meta: dict[Identifier, AccountTemplate] = {
        t.role: t for t in instance.account_templates
    }
    zba_funding_rails: list[Identifier] = []
    for rail in sorted(instance.rails, key=lambda r: str(r.name)):
        if not isinstance(rail, TwoLegRail):
            continue
        # Pull every role in the source_role expression; if any resolves
        # to a template with parent_role == ConcentrationMaster, this
        # rail debits a ZBA-style sub-account.
        triggers_zba = False
        for role in rail.source_role:
            parent = template_parent_roles.get(role)
            if parent is not None and str(parent) == "ConcentrationMaster":
                triggers_zba = True
                break
        if triggers_zba:
            zba_funding_rails.append(rail.name)

    # For each ZBA-funding rail's firing, look up the actual account_id
    # the leg loop posted against (via state.account_leg_log) — the
    # firing log only carries (transfer_id, day, amount), not the
    # source account_id. Walk account_leg_log entries on the same
    # (day, transfer_id) for accounts under the ConcentrationMaster
    # parent and emit one paired credit per such (account_id, day,
    # transfer_id) tuple.
    if zba_funding_rails:
        # Build (account_id, day, transfer_id) -> amount lookup from
        # the leg log for the rails we care about.
        zba_template_role_set: set[str] = set()
        for role, parent in template_parent_roles.items():
            if parent is not None and str(parent) == "ConcentrationMaster":
                zba_template_role_set.add(str(role))
        # Find every template instance under a ZBA-style parent role.
        zba_account_ids: set[Identifier] = set()
        for ti in state.template_instances:
            if str(ti.template_role) in zba_template_role_set:
                zba_account_ids.add(ti.account_id)

        for rail_name in zba_funding_rails:
            rail = rails_by_name.get(rail_name)
            if rail is None or not isinstance(rail, TwoLegRail):
                continue
            firings = sorted(
                state.firings.get(rail_name, []),
                key=lambda f: (f[1], f[0]),
            )
            for parent_transfer_id, day, amount in firings:
                # Find which ZBA account this firing debited. The leg
                # log has every leg by account_id; match by (day,
                # signed=-amount). Since each rail firing produces one
                # debit on exactly one source account, the first match
                # wins. Sort accounts so the match is deterministic.
                target: _ResolvedAccount | None = None
                for acct_id in sorted(zba_account_ids, key=str):
                    legs = state.account_leg_log.get(acct_id, [])
                    for _posting, leg_day, signed in legs:
                        if leg_day == day and signed == -amount:
                            template_role = _template_role_for_account_id(
                                acct_id, state,
                            )
                            if template_role is None:
                                continue
                            tmpl = template_role_meta.get(template_role)
                            if tmpl is None:
                                continue
                            ti_name = _template_instance_name_for(
                                acct_id, state,
                            )
                            target = _ResolvedAccount(
                                account_id=acct_id,
                                account_name=ti_name,
                                account_role=template_role,
                                account_scope=tmpl.scope,
                                account_parent_role=tmpl.parent_role,
                            )
                            break
                    if target is not None:
                        break
                if target is None:
                    continue
                rows.extend(_emit_cascade_pair(
                    state=state,
                    target=target,
                    external=external_account,
                    amount=amount,
                    day=day,
                    source_transfer_id=parent_transfer_id,
                    cascade_label=Identifier("ZBAFundingInbound"),
                    balance_rail=bm,
                    counter=counter,
                    rng=rng,
                    dialect=dialect,
                ))

    return rows


def _emit_cascade_pair(
    *,
    state: _BaselineState,
    target: _ResolvedAccount,
    external: _ResolvedAccount,
    amount: Decimal,
    day: date,
    source_transfer_id: str,
    cascade_label: Identifier,
    counter: _Counter,
    rng: random.Random,
    dialect: Dialect,
    balance_rail: Identifier | None = None,
) -> list[str]:
    """Emit one cascade credit + counter-debit pair (helper for V.5.b).

    AJ.4b: when ``balance_rail`` is given (the L2 declares an
    ``InternalBalanceMaintenance`` rail), both legs are stamped with it
    instead of ``cascade_label`` so the synthetic balance-maintenance
    pair doesn't count as a firing of the cascaded money-movement rail.
    ``cascade_label`` is still recorded in metadata for debugging.

    The credit lands on ``target`` (the internal clearing GL / suspense
    / sub-account that's about to be debited). The counter-leg debits
    ``external`` so the pair nets to zero.

    Posting timestamp is ~30 minutes BEFORE the source firing so the
    cumulative-sum walk on ``target`` shows the GL going positive,
    then back to baseline after the source debit posts. ``source_
    transfer_id`` carries the parent firing's transfer_id; we don't
    use it for the cascade row's transfer_id (cascade gets its own
    ``tr-base-cascade-...`` namespace) but it's threaded through for
    future debugging if needed.

    Both legs share the same ``tr-base-cascade-{n}`` transfer_id so
    L1's conservation invariant sees a balanced 2-leg Transfer.
    Updates ``state.account_leg_log`` for the target so the deferred
    daily-balance walk includes the credit. No update needed for the
    external counterparty (we don't track external balances).
    """
    n = counter.next()
    transfer_id = f"tr-base-cascade-{n:06d}"
    txn_id = f"tx-base-cascade-{n:06d}"
    # Place ~30 minutes before EOD activity. Use a deterministic offset
    # via rng so multiple cascade pairs on the same day don't collide.
    # Bands of 30s within an 8:00-9:00 UTC window keep them ahead of
    # the rail-loop's 9-22 UTC postings.
    minute = rng.randrange(30)
    second = rng.randrange(60)
    posting = f"{day.isoformat()}T08:{minute:02d}:{second:02d}+00:00"

    metadata: dict[str, str] = {
        "cascade_label": str(cascade_label),
        "source_transfer_id": source_transfer_id,
    }

    rows: list[str] = [
        # Counter-leg: debit external counterparty.
        _txn_row(
            id_=f"{txn_id}-src",
            account_id=external.account_id,
            account_name=external.account_name,
            account_role=external.account_role,
            account_scope=external.account_scope,
            account_parent_role=external.account_parent_role,
            money=-amount,
            direction="Debit",
            posting=posting,
            transfer_id=transfer_id,
            rail_name=balance_rail if balance_rail is not None else cascade_label,
            origin="ExternalForcePosted",
            metadata=metadata,
            dialect=dialect,
        ),
        # Credit-leg: lands on the cascade target (internal clearing GL).
        _txn_row(
            id_=txn_id,
            account_id=target.account_id,
            account_name=target.account_name,
            account_role=target.account_role,
            account_scope=target.account_scope,
            account_parent_role=target.account_parent_role,
            money=amount,
            direction="Credit",
            posting=posting,
            transfer_id=transfer_id,
            rail_name=balance_rail if balance_rail is not None else cascade_label,
            origin="InternalInitiated",
            metadata=metadata,
            dialect=dialect,
        ),
    ]
    state.account_leg_log.setdefault(target.account_id, []).append(
        (posting, day, amount),
    )
    return rows


def _resolve_internal_singleton_for_role(
    role_expr: tuple[Identifier, ...],
    instance: L2Instance,
) -> _ResolvedAccount | None:
    """Look up the first internal-scope singleton account whose role
    matches any role in ``role_expr``. Returns None if no singleton
    matches (e.g., the role is only present as template instances).
    """
    role_set = {str(r) for r in role_expr}
    for a in sorted(instance.accounts, key=lambda a: str(a.id)):
        if str(a.scope) != "internal":
            continue
        role = str(a.role) if a.role is not None else str(a.id)
        if role in role_set:
            return _ResolvedAccount(
                account_id=a.id,
                account_name=a.name or Name(str(a.id)),
                account_role=a.role or Identifier(str(a.id)),
                account_scope=a.scope,
                account_parent_role=a.parent_role,
            )
    return None


def _template_role_for_account_id(
    account_id: Identifier, state: _BaselineState,
) -> Identifier | None:
    """Return the template role of a materialized template instance, or
    ``None`` if ``account_id`` isn't a template instance.
    """
    for ti in state.template_instances:
        if ti.account_id == account_id:
            return ti.template_role
    return None


def _template_instance_name_for(
    account_id: Identifier, state: _BaselineState,
) -> Name:
    """Return the materialized template instance's display name. Falls
    back to the account_id-as-Name if the instance isn't found (caller
    contract: only used after _template_role_for_account_id returned
    non-None, so this should always hit).
    """
    for ti in state.template_instances:
        if ti.account_id == account_id:
            return ti.name
    return Name(str(account_id))


def _emit_baseline_daily_balances(
    state: _BaselineState,
    instance: L2Instance,
    template_by_role: dict[Identifier, AccountTemplate],
    dialect: Dialect,
) -> list[str]:
    """Materialize ``daily_balances`` rows from the per-account leg log.

    Deferred-walk implementation (post-R.2.e fix): per account, sort
    the leg log by posting timestamp, walk legs in chronological order,
    accumulate from ``initial_balances``, and write
    ``eod_balances[(account, day)] = running_balance`` after each leg.
    Last leg of each day wins, which captures the correct EOD snapshot
    even when rails iterated in name-order across all days during emit.

    Carry-forward to non-business days (v8.5.4): legs only post on
    business days, so the per-leg accumulation produces balance
    snapshots only for Mon-Fri (excluding US holidays). The Daily
    Statement picker defaults to *yesterday* (real time) — when
    yesterday is a Saturday/Sunday/holiday, an unfilled view leaves
    the picker on a date with no balance row and the table renders
    empty. After the per-leg pass, fill forward each account's
    last-known balance through every calendar day in the window so
    weekend / holiday picker defaults always land on a real row
    (the Friday EOD balance carries through Sat + Sun, etc.).

    Drift invariant guarantee: by walking the FULL leg history
    chronologically, ``daily_balances.money == SUM(signed_amount)
    through end of day`` for every (account, day) — the L1 drift matview
    computes zero for every baseline row. Carry-forward weekend rows
    preserve the same invariant: their ``money`` is the prior business
    day's EOD, which equals ``SUM(signed_amount)`` through that prior
    day (no legs post Sat/Sun, so the cumulative sum is unchanged).

    Per-role business-day offsets (M.4.4.14): roles in
    ``instance.role_business_day_offsets`` get their business_day_start
    / business_day_end shifted by the configured hour offset. Roles
    without an entry default to midnight-aligned (00:00 → 00:00 next
    day).
    """
    if not state.account_leg_log:
        return []

    account_meta = _build_account_meta_map(state, instance)
    role_offsets = instance.role_business_day_offsets or {}

    # Walk every account's leg log in chronological order to compute
    # correct cumulative EOD balances. Per-leg accumulation; last leg
    # of each day wins per dict semantics.
    eod_balances: dict[tuple[Identifier, date], Decimal] = {}
    for account_id in sorted(state.account_leg_log, key=str):
        running = state.initial_balances.get(account_id, Decimal("0"))
        for posting, day, signed in sorted(
            state.account_leg_log[account_id], key=lambda l: l[0],
        ):
            running += signed
            eod_balances[(account_id, day)] = running
            _ = posting

    # Calendar days in the window — every date, not just Mon-Fri. Used
    # for the carry-forward fill below.
    calendar_days: list[date] = []
    cursor = state.anchor - timedelta(days=state.window_days)
    while cursor <= state.anchor:
        calendar_days.append(cursor)
        cursor += timedelta(days=1)

    # Per account, fill forward the last-known balance into every
    # calendar day in the window. An account's first business-day
    # balance lands on its activity day; days before it get the
    # account's initial balance; days between business-day legs
    # carry the most recent EOD; days after the final leg through
    # the anchor carry that final EOD.
    accounts_with_activity: set[Identifier] = {a for a, _ in eod_balances}
    filled_eod: dict[tuple[Identifier, date], Decimal] = {}
    for account_id in sorted(accounts_with_activity, key=str):
        running = state.initial_balances.get(account_id, Decimal("0"))
        for day in calendar_days:
            if (account_id, day) in eod_balances:
                running = eod_balances[(account_id, day)]
            filled_eod[(account_id, day)] = running

    rows: list[str] = []
    for (account_id, day), money in sorted(
        filled_eod.items(), key=lambda kv: (str(kv[0][0]), kv[0][1]),
    ):
        meta = account_meta.get(account_id)
        if meta is None:
            continue
        offset_hours = role_offsets.get(str(meta.account_role), 0)
        rows.append(_balance_row(
            account_id=meta.account_id,
            account_name=meta.account_name,
            account_role=meta.account_role,
            account_scope=meta.account_scope,
            account_parent_role=meta.account_parent_role,
            day=day,
            money=money,
            dialect=dialect,
            offset_hours=offset_hours,
        ))

    _ = template_by_role
    return rows


def _build_account_meta_map(
    state: _BaselineState, instance: L2Instance,
) -> dict[Identifier, _ResolvedAccount]:
    """Build account_id -> _ResolvedAccount lookup for daily-balance emit.

    Walks materialized template instances first, then singleton
    accounts. The map is built once per ``emit_baseline_seed`` call and
    reused for every (account_id, day) row.
    """
    template_by_role = {t.role: t for t in instance.account_templates}
    out: dict[Identifier, _ResolvedAccount] = {}
    for ti in state.template_instances:
        tmpl = template_by_role.get(ti.template_role)
        if tmpl is None:
            continue
        out[ti.account_id] = _ResolvedAccount(
            account_id=ti.account_id,
            account_name=ti.name,
            account_role=ti.template_role,
            account_scope=tmpl.scope,
            account_parent_role=tmpl.parent_role,
        )
    for a in instance.accounts:
        out[a.id] = _ResolvedAccount(
            account_id=a.id,
            account_name=a.name or Name(str(a.id)),
            account_role=a.role or Identifier(str(a.id)),
            account_scope=a.scope,
            account_parent_role=a.parent_role,
        )
    return out


# -- Internal helpers --------------------------------------------------------


class _Counter:
    """Tiny mutable counter for deterministic ID generation."""

    def __init__(self, *, start: int = 1) -> None:
        self.value = start

    def next(self) -> int:
        v = self.value
        self.value += 1
        return v


def _drift_key(p: DriftPlant) -> tuple[str, int]:
    return (str(p.account_id), p.days_ago)


def _overdraft_key(p: OverdraftPlant) -> tuple[str, int]:
    return (str(p.account_id), p.days_ago)


def _breach_key(p: LimitBreachPlant) -> tuple[str, int, str]:
    return (str(p.account_id), p.days_ago, str(p.rail_name))


def _inbound_breach_key(p: InboundCapBreachPlant) -> tuple[str, int, str]:
    return (str(p.account_id), p.days_ago, str(p.rail_name))


def _two_template_chain_key(p: TwoTemplateChainPlant) -> tuple[str, str, int]:
    return (str(p.chain_parent_rail_name), str(p.child_template_name), p.days_ago)


def _chain_parent_disagreement_key(
    p: ChainParentDisagreementPlant,
) -> tuple[str, int, str, str]:
    return (
        str(p.child_template_name), p.days_ago,
        p.parent_a_transfer_id, p.parent_b_transfer_id,
    )


def _xor_missed_firing_key(
    p: XorVariantMissedFiringPlant,
) -> tuple[str, int, int, str]:
    return (
        str(p.template_name),
        p.target_xor_group_index,
        p.days_ago,
        str(p.witness_rail_name),
    )


def _xor_overlap_key(
    p: XorVariantOverlapPlant,
) -> tuple[str, int, int, str, str]:
    return (
        str(p.template_name),
        p.target_xor_group_index,
        p.days_ago,
        str(p.variant_a_rail_name),
        str(p.variant_b_rail_name),
    )


def _stuck_pending_key(p: StuckPendingPlant) -> tuple[str, int, str]:
    return (str(p.account_id), p.days_ago, str(p.rail_name))


def _fan_in_chain_key(
    p: FanInChainPlant,
) -> tuple[str, str, int, int]:
    return (
        str(p.chain_parent_rail_name),
        str(p.child_template_name),
        p.days_ago,
        p.parent_count,
    )


def _fan_in_missing_parent_key(
    p: FanInChainMissingParentPlant,
) -> tuple[str, str, int, int]:
    return (
        str(p.chain_parent_rail_name),
        str(p.child_template_name),
        p.days_ago,
        p.parent_count,
    )


def _fan_in_extra_parent_key(
    p: FanInChainExtraParentPlant,
) -> tuple[str, str, int, int]:
    return (
        str(p.chain_parent_rail_name),
        str(p.child_template_name),
        p.days_ago,
        p.parent_count,
    )


def _multi_xor_missed_key(p: MultiXorMissedPlant) -> tuple[str, int]:
    return (str(p.chain_parent_rail_name), p.days_ago)


def _multi_xor_overlap_key(p: MultiXorOverlapPlant) -> tuple[str, int, str, str]:
    return (
        str(p.chain_parent_rail_name),
        p.days_ago,
        str(p.variant_a_child_name),
        str(p.variant_b_child_name),
    )


def _failed_transaction_key(p: FailedTransactionPlant) -> tuple[str, int, str]:
    return (str(p.account_id), p.days_ago, str(p.rail_name))


def _stuck_unbundled_key(p: StuckUnbundledPlant) -> tuple[str, int, str]:
    return (str(p.account_id), p.days_ago, str(p.rail_name))


def _supersession_key(p: SupersessionPlant) -> tuple[str, int, str]:
    return (str(p.account_id), p.days_ago, str(p.rail_name))


def _tt_key(p: TransferTemplatePlant) -> tuple[str, int, int]:
    return (str(p.template_name), p.days_ago, p.firing_seq)


def _rail_firing_key(p: RailFiringPlant) -> tuple[str, int, int]:
    return (str(p.rail_name), p.days_ago, p.firing_seq)


def _inv_fanout_key(p: InvFanoutPlant) -> tuple[str, int, str]:
    return (str(p.recipient_account_id), p.days_ago, str(p.rail_name))


def _parent_singletons(instance: L2Instance) -> dict[Identifier, Account]:
    """Build a `role -> Account` map for singleton accounts; used to
    resolve `AccountTemplate.parent_role` to a concrete parent."""
    return {
        a.role: a for a in instance.accounts if a.role is not None
    }


def _resolve_template(
    account_id: Identifier,
    scenarios: ScenarioPlant,
) -> TemplateInstance:
    """Find the materialized template instance for `account_id`."""
    for ti in scenarios.template_instances:
        if ti.account_id == account_id:
            return ti
    raise KeyError(
        f"account_id {account_id!r} not declared in scenarios.template_instances"
    )


def _resolve_account(account_id: Identifier, instance: L2Instance) -> Account:
    """Find the L2-declared Account by id; raise on miss with a clear message."""
    for a in instance.accounts:
        if a.id == account_id:
            return a
    raise KeyError(
        f"account_id {account_id!r} not declared in instance.accounts; "
        f"plant references an external counterparty that doesn't exist "
        f"in the L2 YAML"
    )


def _eod_timestamp(d: date, offset_hours: int = 0) -> str:
    """End-of-day UTC timestamp for `d` shifted by ``offset_hours``
    (i.e. start of next day at the same hour). Default 0 keeps
    midnight-aligned production behavior.
    """
    next_day = d + timedelta(days=1)
    return f"{next_day.isoformat()}T{offset_hours:02d}:00:00+00:00"


def _bod_timestamp(d: date, offset_hours: int = 0) -> str:
    """Beginning-of-day UTC timestamp for `d` shifted by ``offset_hours``.
    Default 0 keeps midnight-aligned production behavior.
    """
    return f"{d.isoformat()}T{offset_hours:02d}:00:00+00:00"


def _emit_inbound_cap_breach_rows(
    p: InboundCapBreachPlant,
    instance: L2Instance,
    scenarios: ScenarioPlant,
    template_by_role: dict[Identifier, AccountTemplate],
    parent_singleton_by_role: dict[Identifier, Account],
    counter: _Counter,
    dialect: Dialect,
) -> list[str]:
    """Plant ONE inbound credit row exceeding the cap. Mirror of
    :func:`_emit_limit_breach_rows`: customer leg is Credit (money IN),
    external counter-leg is Debit (money OUT of the funds source).

    The row alone drives ``InboundFlow > limit`` for the
    ``(account, day, rail)`` cell — surfaces on the Inbound branch of
    the ``<prefix>_limit_breach`` matview (AB.1).
    """
    ti = _resolve_template(p.account_id, scenarios)
    template = template_by_role[ti.template_role]
    parent_role = template.parent_role
    counter_account = _resolve_account(p.counter_account_id, instance)
    counter_name = counter_account.name or Name(str(counter_account.id))
    counter_role = counter_account.role or Identifier(str(counter_account.id))
    breach_day = scenarios.today - timedelta(days=p.days_ago)
    posting_ts = (
        f"{breach_day.isoformat()}T14:00:00+00:00"  # 2pm — middle of business day
    )

    n = counter.next()
    txn_id = f"tx-inbreach-{n:04d}"
    transfer_id = f"tr-inbreach-{n:04d}"
    credit_money = p.amount  # inbound = Credit; sign-direction agreement (+ = money IN)

    rows = [
        # Customer DDA credit leg (the breaching one)
        _txn_row(
            id_=txn_id,
            account_id=ti.account_id,
            account_name=ti.name,
            account_role=ti.template_role,
            account_scope=template.scope,
            account_parent_role=parent_role,
            money=credit_money,
            direction="Credit",
            posting=posting_ts,
            transfer_id=transfer_id,
            rail_name=p.rail_name,
            origin="ExternalForcePosted",  # inbound from external system
            metadata={"customer_id": str(ti.account_id)},

            dialect=dialect,
        ),
        # External counter-leg (no balance tracking, but needed for Conservation)
        _txn_row(
            id_=f"{txn_id}-ext",
            account_id=counter_account.id,
            account_name=counter_name,
            account_role=counter_role,
            account_scope=counter_account.scope,
            account_parent_role=counter_account.parent_role,
            money=-p.amount,  # -ve: external sends
            direction="Debit",
            posting=posting_ts,
            transfer_id=transfer_id,
            rail_name=p.rail_name,
            origin="ExternalForcePosted",
            metadata={"customer_id": str(ti.account_id)},

            dialect=dialect,
        ),
    ]
    # AJ.3 (Gap H residual): the breaching rail may also be a chain parent
    # (e.g. an inbound rail with downstream return-rail children) — complete
    # the firing so multi_xor_violation / chain_orphans don't read this
    # limit-breach plant as a missing-child violation too.
    rows.extend(_emit_plant_chain_completion(
        transfer_id, p.rail_name, posting_ts,
        account_id=ti.account_id, account_name=ti.name,
        account_role=ti.template_role, account_scope=template.scope,
        account_parent_role=parent_role,
        instance=instance, counter=counter, dialect=dialect,
    ))
    return rows


def _emit_two_template_chain_rows(
    p: TwoTemplateChainPlant,
    instance: L2Instance,
    scenarios: ScenarioPlant,
    template_by_role: dict[Identifier, AccountTemplate],
    counter: _Counter,
    dialect: Dialect,
) -> list[str]:
    """AB.2.6 healthy plant: one parent leg_rail firing + one child
    template firing (all leg_rails of the child template share one
    transfer_id and agree on parent_transfer_id).

    No violation: the AB.2.3 matview reads COUNT(DISTINCT
    parent_transfer_id) = 1 for the shared child transfer_id. Purpose
    is positive demo coverage on the dashboard's PostedRequirements
    panel + audit PDF — a clearly-labeled healthy two-template chain
    row, separate from the probabilistic baseline.
    """
    # AG.3 (Gap A): accept Rail OR Template parents. Template parents
    # synthesize the parent row via the template's first leg_rail and
    # stamp template_name on the row so it reads as a template firing.
    parent_resolution = _resolve_plant_chain_parent(
        p.chain_parent_rail_name, instance,
    )
    if parent_resolution is None:
        return []
    parent_rail_for_emit, parent_template_for_emit = parent_resolution
    child_template = _find_template_or_skip(p.child_template_name, instance)
    if child_template is None or not child_template.leg_rails:
        return []
    # Source / counter accounts: any TemplateInstance the parent rail
    # could fire on. Falls back to first available template_instance
    # for shape consistency with the rest of the plant emitters.
    ti = _first_template_instance_or_skip(scenarios)
    if ti is None:
        return []
    template = template_by_role.get(ti.template_role)
    if template is None:
        return []
    parent_role = template.parent_role

    plant_day = scenarios.today - timedelta(days=p.days_ago)
    parent_posting = f"{plant_day.isoformat()}T10:00:00+00:00"
    child_posting = f"{plant_day.isoformat()}T11:00:00+00:00"

    n_parent = counter.next()
    parent_transfer_id = f"tr-tmpl-chain-parent-{n_parent:04d}"
    n_child = counter.next()
    child_transfer_id = f"tr-tmpl-chain-child-{n_child:04d}"

    rows: list[str] = []
    # Parent firing: one row keyed to parent_rail_for_emit, with
    # template_name set when the chain.parent resolved to a Template
    # (AG.3 — Gap A). For SQL simplicity the parent is a single-leg
    # debit row — the matview only inspects child rows.
    rows.append(_txn_row(
        id_=f"tx-tmpl-chain-parent-{n_parent:04d}",
        account_id=ti.account_id,
        account_name=ti.name,
        account_role=ti.template_role,
        account_scope=template.scope,
        account_parent_role=parent_role,
        money=-Decimal("100.00"),
        direction="Debit",
        posting=parent_posting,
        transfer_id=parent_transfer_id,
        rail_name=parent_rail_for_emit,
        template_name=parent_template_for_emit,
        origin="InternalInitiated",
        metadata={},
        dialect=dialect,
    ))
    # Child leg_rail firings — all share child_transfer_id, all carry
    # transfer_parent_id=parent_transfer_id, all carry template_name=
    # child_template_name. The AB.2.3 matview groups these into one
    # row with distinct_parent_count=1 (no violation).
    for i, leg_rail_name in enumerate(child_template.leg_rails):
        rows.append(_txn_row(
            id_=f"tx-tmpl-chain-child-{n_child:04d}-{i}",
            account_id=ti.account_id,
            account_name=ti.name,
            account_role=ti.template_role,
            account_scope=template.scope,
            account_parent_role=parent_role,
            money=Decimal("50.00"),  # arbitrary; matview doesn't read amount
            direction="Credit",
            posting=child_posting,
            transfer_id=child_transfer_id,
            rail_name=leg_rail_name,
            origin="InternalInitiated",
            metadata={},
            template_name=child_template.name,
            transfer_parent_id=parent_transfer_id,
            dialect=dialect,
        ))
    return rows


def _emit_chain_parent_disagreement_rows(
    p: ChainParentDisagreementPlant,
    instance: L2Instance,
    scenarios: ScenarioPlant,
    template_by_role: dict[Identifier, AccountTemplate],
    counter: _Counter,
    dialect: Dialect,
) -> list[str]:
    """AB.2.6 violation plant: ≥2 leg_rail rows sharing one child
    transfer_id + template_name but assigning *different* parent
    transfer_ids — simulates an ETL bug where leg_rail firings of one
    chain invocation disagree on which parent firing they descend from.

    The AB.2.3 matview reads COUNT(DISTINCT parent_transfer_id) = 2 for
    the shared child transfer_id and surfaces a row. This is the
    canonical demo-visible L1 violation for two-template chains.
    """
    child_template = _find_template_or_skip(p.child_template_name, instance)
    if child_template is None or len(child_template.leg_rails) < 1:
        return []
    ti = _first_template_instance_or_skip(scenarios)
    if ti is None:
        return []
    template = template_by_role.get(ti.template_role)
    if template is None:
        return []
    parent_role = template.parent_role

    plant_day = scenarios.today - timedelta(days=p.days_ago)
    posting = f"{plant_day.isoformat()}T12:00:00+00:00"
    n = counter.next()
    child_transfer_id = f"tr-cpd-{n:04d}"
    # If the template has only 1 leg_rail, the disagreement still fires
    # — we emit 2 rows on the same leg_rail with different parent ids.
    # The matview's GROUP BY (transfer_id, template_name) doesn't care
    # about rail_name distinctness.
    leg_rails = list(child_template.leg_rails)
    if len(leg_rails) == 1:
        leg_rails.append(leg_rails[0])  # second row on same rail
    parent_ids = [p.parent_a_transfer_id, p.parent_b_transfer_id]

    rows: list[str] = []
    for i, (leg_rail_name, parent_tid) in enumerate(zip(leg_rails[:2], parent_ids, strict=True)):
        rows.append(_txn_row(
            id_=f"tx-cpd-{n:04d}-{i}",
            account_id=ti.account_id,
            account_name=ti.name,
            account_role=ti.template_role,
            account_scope=template.scope,
            account_parent_role=parent_role,
            money=Decimal("75.00"),  # arbitrary; matview only counts DISTINCT parent_transfer_id
            direction="Credit",
            posting=posting,
            transfer_id=child_transfer_id,
            rail_name=leg_rail_name,
            origin="ExternalForcePosted",  # ETL-bug origin convention
            metadata={},
            template_name=child_template.name,
            transfer_parent_id=parent_tid,
            dialect=dialect,
        ))
    return rows


def _emit_fan_in_chain_plant_rows(
    chain_parent_rail_name: Identifier,
    child_template_name: Identifier,
    days_ago: int,
    parent_count: int,
    *,
    plant_tag: str,
    instance: L2Instance,
    scenarios: ScenarioPlant,
    template_by_role: dict[Identifier, AccountTemplate],
    counter: _Counter,
    dialect: Dialect,
) -> list[str]:
    """AB.4.5 — shared emitter for all 3 fan-in plant kinds (healthy /
    missing / extra). Each plant kind passes a different
    ``parent_count`` value:

    - Healthy (``FanInChainPlant``): parent_count = chain's expected
      → no AB.4.7 matview row.
    - Missing (``FanInChainMissingParentPlant``): parent_count < expected
      → row with ``disagreement_kind='missing'`` (or ``'orphan'`` when
      parent_count = 1).
    - Extra (``FanInChainExtraParentPlant``): parent_count > expected
      → row with ``disagreement_kind='extra'``.

    Emits ``parent_count`` synthetic parent legs + the child template's
    full leg_rail set, all leg_rails of the child Transfer sharing one
    ``transfer_id`` but each parent's leg row carrying that parent's
    ``transfer_parent_id``. The AB.4.3 ``_transfer_parents`` matview's
    DISTINCT over (child_transfer_id, transfer_parent_id) yields
    ``parent_count`` rows for this Transfer — that's the cardinality
    AB.4.7's downstream check reads.

    ``plant_tag`` is the row-id prefix discriminator (``'fanin-h'`` /
    ``'fanin-m'`` / ``'fanin-x'``) so analysts can grep the dialect-
    specific SQL for the plant kind.
    """
    # AG.3 (Gap A): accept Rail OR Template parents.
    parent_resolution = _resolve_plant_chain_parent(
        chain_parent_rail_name, instance,
    )
    if parent_resolution is None:
        return []
    parent_rail_for_emit, parent_template_for_emit = parent_resolution
    child_template = _find_template_or_skip(child_template_name, instance)
    if child_template is None or not child_template.leg_rails:
        return []
    ti = _first_template_instance_or_skip(scenarios)
    if ti is None:
        return []
    template = template_by_role.get(ti.template_role)
    if template is None:
        return []
    parent_role = template.parent_role

    plant_day = scenarios.today - timedelta(days=days_ago)
    parent_posting = f"{plant_day.isoformat()}T10:00:00+00:00"
    child_posting = f"{plant_day.isoformat()}T11:00:00+00:00"

    n_child = counter.next()
    child_transfer_id = f"tr-{plant_tag}-child-{n_child:04d}"

    rows: list[str] = []
    parent_ids: list[str] = []
    # Emit `parent_count` synthetic parent legs.
    for k in range(parent_count):
        counter.next()  # advance counter for determinism across plant kinds
        parent_transfer_id = f"tr-{plant_tag}-parent-{n_child:04d}-{k:02d}"
        parent_ids.append(parent_transfer_id)
        rows.append(_txn_row(
            id_=f"tx-{plant_tag}-parent-{n_child:04d}-{k:02d}",
            account_id=ti.account_id,
            account_name=ti.name,
            account_role=ti.template_role,
            account_scope=template.scope,
            account_parent_role=parent_role,
            money=-Decimal("100.00"),
            direction="Debit",
            posting=parent_posting,
            transfer_id=parent_transfer_id,
            rail_name=parent_rail_for_emit,
            template_name=parent_template_for_emit,
            origin="InternalInitiated",
            metadata={},
            dialect=dialect,
        ))
    # Emit child template's leg_rails, cycling through the parent_ids
    # so each leg row carries a contributing parent's transfer_parent_id.
    # All legs share the single child_transfer_id (the fan-in shape).
    for i, leg_rail_name in enumerate(child_template.leg_rails):
        parent_tid = parent_ids[i % len(parent_ids)]
        rows.append(_txn_row(
            id_=f"tx-{plant_tag}-child-{n_child:04d}-{i}",
            account_id=ti.account_id,
            account_name=ti.name,
            account_role=ti.template_role,
            account_scope=template.scope,
            account_parent_role=parent_role,
            money=Decimal("50.00"),
            direction="Credit",
            posting=child_posting,
            transfer_id=child_transfer_id,
            rail_name=leg_rail_name,
            origin="InternalInitiated",
            metadata={},
            template_name=child_template.name,
            transfer_parent_id=parent_tid,
            dialect=dialect,
        ))
    # If parent_count > len(leg_rails), emit additional child legs
    # carrying the leftover parents' transfer_parent_ids so they show
    # up in the _transfer_parents DISTINCT. Without this loop, the
    # extra-parent plant's parent firings would exist in transactions
    # but not contribute to the child Transfer's parent set.
    n_legs = len(child_template.leg_rails)
    if parent_count > n_legs:
        for k in range(n_legs, parent_count):
            parent_tid = parent_ids[k]
            # Reuse the first leg_rail for the extra rows — the matview
            # cares about (child_transfer_id, transfer_parent_id) DISTINCT
            # so the rail_name choice doesn't matter for fan_in_disagreement.
            leg_rail_name = child_template.leg_rails[0]
            rows.append(_txn_row(
                id_=f"tx-{plant_tag}-child-{n_child:04d}-extra-{k}",
                account_id=ti.account_id,
                account_name=ti.name,
                account_role=ti.template_role,
                account_scope=template.scope,
                account_parent_role=parent_role,
                money=Decimal("50.00"),
                direction="Credit",
                posting=child_posting,
                transfer_id=child_transfer_id,
                rail_name=leg_rail_name,
                origin="InternalInitiated",
                metadata={},
                template_name=child_template.name,
                transfer_parent_id=parent_tid,
                dialect=dialect,
            ))
    return rows


def _emit_xor_variant_missed_firing_rows(
    p: XorVariantMissedFiringPlant,
    instance: L2Instance,
    scenarios: ScenarioPlant,
    template_by_role: dict[Identifier, AccountTemplate],
    counter: _Counter,
    dialect: Dialect,
) -> list[str]:
    """AB.3.5 violation plant: emit a Transfer tagged with a template
    whose targeted XOR group has zero member-rail firings.

    Emits one witness row (template_name set, rail_name = a leg_rail
    NOT in the target XOR group). The AB.3.3 matview's
    ``template_transfers`` CTE picks up the Transfer (template_name
    matches an XOR-grouped template); the LEFT JOIN against
    ``(transfer_id, template, member_rail)`` for the target group
    finds zero rows; ``firing_count = 0`` → ``HAVING <> 1`` → row
    surfaces with ``fired_rails=''``.

    Graceful skip when: the template doesn't exist, declares no XOR
    groups, the target group_index is out of range, the witness rail
    isn't a real leg_rail of the template, OR no materialized template
    instance is available (account-side defensive checks mirror AB.2).
    """
    template = _find_template_or_skip(p.template_name, instance)
    if template is None:
        return []
    if not template.leg_rail_xor_groups:
        return []
    if p.target_xor_group_index >= len(template.leg_rail_xor_groups):
        return []
    if p.witness_rail_name not in template.leg_rails:
        return []
    # The witness MUST NOT be a member of the targeted group, else it
    # would itself fire the group and break the missed-firing invariant.
    target_group = set(template.leg_rail_xor_groups[p.target_xor_group_index])
    if p.witness_rail_name in target_group:
        return []
    ti = _first_template_instance_or_skip(scenarios)
    if ti is None:
        return []
    at = template_by_role.get(ti.template_role)
    if at is None:
        return []
    parent_role = at.parent_role

    plant_day = scenarios.today - timedelta(days=p.days_ago)
    posting = f"{plant_day.isoformat()}T11:30:00+00:00"

    n = counter.next()
    transfer_id = f"tr-xor-missed-{n:04d}"

    rows = [_txn_row(
        id_=f"tx-xor-missed-{n:04d}-w",
        account_id=ti.account_id,
        account_name=ti.name,
        account_role=ti.template_role,
        account_scope=at.scope,
        account_parent_role=parent_role,
        money=Decimal("100.00"),  # arbitrary; matview only counts row presence
        direction="Credit",
        posting=posting,
        transfer_id=transfer_id,
        rail_name=p.witness_rail_name,
        origin="InternalInitiated",
        metadata={},
        template_name=template.name,
        dialect=dialect,
    )]
    # AJ.3 (Gap H residual): this template may ALSO be a multi-XOR chain
    # parent — complete the firing so multi_xor_violation / chain_orphans
    # don't read this XOR-variant plant as a missing-child violation too.
    rows.extend(_emit_plant_chain_completion(
        transfer_id, template.name, posting,
        account_id=ti.account_id, account_name=ti.name,
        account_role=ti.template_role, account_scope=at.scope,
        account_parent_role=parent_role,
        instance=instance, counter=counter, dialect=dialect,
    ))
    return rows


def _emit_xor_variant_overlap_rows(
    p: XorVariantOverlapPlant,
    instance: L2Instance,
    scenarios: ScenarioPlant,
    template_by_role: dict[Identifier, AccountTemplate],
    counter: _Counter,
    dialect: Dialect,
) -> list[str]:
    """AB.3.5b violation plant: emit a Transfer tagged with a template
    whose targeted XOR group has TWO member-rail firings.

    Emits two leg_rail rows sharing one ``transfer_id`` +
    ``template_name``, ``rail_name`` values = the plant's
    ``variant_a`` and ``variant_b`` (both must be members of the
    target group, distinct). The AB.3.3 matview's LEFT JOIN finds two
    member-rail firings for ``(transfer_id, template, target_group)``
    → ``COUNT(*) = 2`` → ``HAVING <> 1`` → row surfaces with
    ``fired_rails='<a>,<b>'`` (concat ordering dialect-specific).

    Graceful skip when the template / group / variants don't satisfy
    the AB.3.5b plant invariants (mirrors AB.3.5's defensive checks).
    """
    template = _find_template_or_skip(p.template_name, instance)
    if template is None:
        return []
    if not template.leg_rail_xor_groups:
        return []
    if p.target_xor_group_index >= len(template.leg_rail_xor_groups):
        return []
    group = set(template.leg_rail_xor_groups[p.target_xor_group_index])
    if p.variant_a_rail_name not in group or p.variant_b_rail_name not in group:
        return []
    if p.variant_a_rail_name == p.variant_b_rail_name:
        return []
    if p.variant_a_rail_name not in template.leg_rails:
        return []
    if p.variant_b_rail_name not in template.leg_rails:
        return []
    ti = _first_template_instance_or_skip(scenarios)
    if ti is None:
        return []
    at = template_by_role.get(ti.template_role)
    if at is None:
        return []
    parent_role = at.parent_role

    plant_day = scenarios.today - timedelta(days=p.days_ago)
    posting_a = f"{plant_day.isoformat()}T11:45:00+00:00"
    posting_b = f"{plant_day.isoformat()}T11:46:00+00:00"

    n = counter.next()
    transfer_id = f"tr-xor-overlap-{n:04d}"

    rows: list[str] = []
    for suffix, posting, variant in (
        ("a", posting_a, p.variant_a_rail_name),
        ("b", posting_b, p.variant_b_rail_name),
    ):
        rows.append(_txn_row(
            id_=f"tx-xor-overlap-{n:04d}-{suffix}",
            account_id=ti.account_id,
            account_name=ti.name,
            account_role=ti.template_role,
            account_scope=at.scope,
            account_parent_role=parent_role,
            money=Decimal("100.00"),  # arbitrary; matview only counts row presence
            direction="Credit",
            posting=posting,
            transfer_id=transfer_id,
            rail_name=variant,
            origin="InternalInitiated",
            metadata={},
            template_name=template.name,
            dialect=dialect,
        ))
    # AJ.3 (Gap H residual): complete the firing if this template is also
    # a multi-XOR chain parent (see _emit_plant_chain_completion).
    rows.extend(_emit_plant_chain_completion(
        transfer_id, template.name, posting_a,
        account_id=ti.account_id, account_name=ti.name,
        account_role=ti.template_role, account_scope=at.scope,
        account_parent_role=parent_role,
        instance=instance, counter=counter, dialect=dialect,
    ))
    return rows


def _emit_multi_xor_missed_rows(
    p: MultiXorMissedPlant,
    instance: L2Instance,
    scenarios: ScenarioPlant,
    template_by_role: dict[Identifier, AccountTemplate],
    counter: _Counter,
    dialect: Dialect,
) -> list[str]:
    """AB.6.6 violation plant: emit ONE parent firing with ZERO
    declared XOR-sibling children. AB.6.5 matview reads
    ``COUNT(matched_child_name) = 0`` → ``HAVING <> 1`` → row with
    ``disagreement_kind='missed'``.

    Account context comes from the first materialized TemplateInstance
    (mirrors xor_variant emit pattern — the matview doesn't care which
    account; it groups by parent_transfer_id).

    Graceful skip when no template instance is available or the parent
    rail isn't declared (defensive — the picker filters these, but
    this preserves the contract on hand-built scenarios).
    """
    # AG.3 (Gap A): accept Rail OR Template parents.
    parent_resolution = _resolve_plant_chain_parent(
        p.chain_parent_rail_name, instance,
    )
    if parent_resolution is None:
        return []
    parent_rail_for_emit, parent_template_for_emit = parent_resolution
    ti = _first_template_instance_or_skip(scenarios)
    if ti is None:
        return []
    at = template_by_role.get(ti.template_role)
    if at is None:
        return []

    plant_day = scenarios.today - timedelta(days=p.days_ago)
    posting = f"{plant_day.isoformat()}T12:15:00+00:00"

    n = counter.next()
    parent_tid = f"tr-mxor-missed-{n:04d}"

    return [_txn_row(
        id_=f"tx-mxor-missed-{n:04d}-p",
        account_id=ti.account_id,
        account_name=ti.name,
        account_role=ti.template_role,
        account_scope=at.scope,
        account_parent_role=at.parent_role,
        money=Decimal("100.00"),
        direction="Credit",
        posting=posting,
        transfer_id=parent_tid,
        rail_name=parent_rail_for_emit,
        template_name=parent_template_for_emit,
        origin="InternalInitiated",
        metadata={},
        dialect=dialect,
    )]


def _emit_multi_xor_overlap_rows(
    p: MultiXorOverlapPlant,
    instance: L2Instance,
    scenarios: ScenarioPlant,
    template_by_role: dict[Identifier, AccountTemplate],
    counter: _Counter,
    dialect: Dialect,
) -> list[str]:
    """AB.6.6 violation plant: emit ONE parent firing + TWO child firings
    that both reference it via ``transfer_parent_id``. AB.6.5 matview
    reads ``COUNT(matched_child_name) = 2`` → ``HAVING <> 1`` → row
    with ``disagreement_kind='overlap'``.

    Both variants MUST be declared children of a multi-XOR chain whose
    parent is ``chain_parent_rail_name`` (picker enforces). Each child
    can be either a Rail or a TransferTemplate; the matview matches
    on whichever fires (rail_name or template_name).

    Graceful skip when the parent rail doesn't exist or no template
    instance is available.
    """
    # AG.3 (Gap A): accept Rail OR Template parents.
    parent_resolution = _resolve_plant_chain_parent(
        p.chain_parent_rail_name, instance,
    )
    if parent_resolution is None:
        return []
    parent_rail_for_emit, parent_template_for_emit = parent_resolution
    ti = _first_template_instance_or_skip(scenarios)
    if ti is None:
        return []
    at = template_by_role.get(ti.template_role)
    if at is None:
        return []
    # Resolve each child name to whether it's a rail or template — the
    # emitter sets rail_name OR template_name accordingly.
    rail_names = {r.name for r in instance.rails}
    template_names = {t.name for t in instance.transfer_templates}

    def _child_kind(name: Identifier) -> str | None:
        if name in rail_names:
            return "rail"
        if name in template_names:
            return "template"
        return None

    kind_a = _child_kind(p.variant_a_child_name)
    kind_b = _child_kind(p.variant_b_child_name)
    if kind_a is None or kind_b is None:
        return []
    if p.variant_a_child_name == p.variant_b_child_name:
        return []

    plant_day = scenarios.today - timedelta(days=p.days_ago)
    posting_parent = f"{plant_day.isoformat()}T12:30:00+00:00"
    posting_a = f"{plant_day.isoformat()}T12:31:00+00:00"
    posting_b = f"{plant_day.isoformat()}T12:32:00+00:00"

    n = counter.next()
    parent_tid = f"tr-mxor-overlap-{n:04d}"
    child_a_tid = f"tr-mxor-overlap-{n:04d}-a"
    child_b_tid = f"tr-mxor-overlap-{n:04d}-b"

    rows: list[str] = []
    # Parent firing (chain.parent rail OR template — AG.3 Gap A).
    rows.append(_txn_row(
        id_=f"tx-mxor-overlap-{n:04d}-p",
        account_id=ti.account_id,
        account_name=ti.name,
        account_role=ti.template_role,
        account_scope=at.scope,
        account_parent_role=at.parent_role,
        money=Decimal("100.00"),
        direction="Credit",
        posting=posting_parent,
        transfer_id=parent_tid,
        rail_name=parent_rail_for_emit,
        template_name=parent_template_for_emit,
        origin="InternalInitiated",
        metadata={},
        dialect=dialect,
    ))
    # Two child firings sharing transfer_parent_id = parent_tid. Each
    # carries its name on rail_name OR template_name per its kind.
    for suffix, posting, child_tid, child_name, kind in (
        ("a", posting_a, child_a_tid, p.variant_a_child_name, kind_a),
        ("b", posting_b, child_b_tid, p.variant_b_child_name, kind_b),
    ):
        # AJ.2 (Gap G): a child row's rail_name must be a REAL declared
        # Rail. For a Rail child that's the child's own name; for a
        # Template child it's the child template's first leg_rail
        # (resolved the same way the parent row is). Pre-fix this fell
        # back to ``p.chain_parent_rail_name`` for Template children —
        # leaking the chain-PARENT name (a TransferTemplate name when the
        # parent is a template) into the rail-conformance column, which
        # then false-positived on the unmatched_rail_name exception.
        if kind == "rail":
            rail_name_for_row = child_name
            template_name_for_row = None
        else:  # template
            child_resolution = _resolve_plant_chain_parent(child_name, instance)
            rail_name_for_row = (
                child_resolution[0] if child_resolution else child_name
            )
            template_name_for_row = child_name
        rows.append(_txn_row(
            id_=f"tx-mxor-overlap-{n:04d}-{suffix}",
            account_id=ti.account_id,
            account_name=ti.name,
            account_role=ti.template_role,
            account_scope=at.scope,
            account_parent_role=at.parent_role,
            money=Decimal("100.00"),
            direction="Credit",
            posting=posting,
            transfer_id=child_tid,
            rail_name=rail_name_for_row,
            origin="InternalInitiated",
            metadata={},
            template_name=template_name_for_row,
            transfer_parent_id=parent_tid,
            dialect=dialect,
        ))
    return rows


def _find_rail_or_skip(name: Identifier, instance: L2Instance) -> Rail | None:
    """Return the L2 Rail matching ``name`` or None for graceful skip."""
    for r in instance.rails:
        if r.name == name:
            return r
    return None


def _resolve_plant_chain_parent(
    name: Identifier, instance: L2Instance,
) -> tuple[Identifier, Identifier | None] | None:
    """AG.3 (Gap A): resolve a chain.parent identifier to the
    ``(rail_name_for_emit, template_name_for_emit)`` pair to stamp on
    a plant-emitted parent row.

    Mirrors the picker-side AG.3 fix: picker tuples carry a chain
    parent that may resolve to either a Rail or a TransferTemplate.
    Plant emitters need to synthesize a valid parent row whose
    ``rail_name`` column references a real Rail (per the schema
    convention that template firings stamp the leg_rail's name in
    ``rail_name`` and the template's name in ``template_name``).

    - Rail parent ``X``: returns ``(X, None)`` — caller stamps
      ``rail_name=X`` and leaves ``template_name`` NULL.
    - Template parent ``T`` (with at least one leg_rail): returns
      ``(T.leg_rails[0], T)`` — caller stamps ``rail_name=leg_rails[0]``
      AND ``template_name=T`` so the row reads as a template firing
      via its first leg_rail (consistent with how chain-child template
      legs are emitted at ``_emit_chain_child_template_legs``).
    - Returns ``None`` if name resolves to neither a Rail nor a
      Template (defensive — pickers should guard, but plant emit stays
      tolerant of hand-built scenarios).
    """
    if _find_rail_or_skip(name, instance) is not None:
        return (name, None)
    template = _find_template_or_skip(name, instance)
    if template is not None and template.leg_rails:
        return (template.leg_rails[0], template.name)
    return None


def _find_template_or_skip(
    name: Identifier, instance: L2Instance,
) -> TransferTemplate | None:
    """Return the L2 TransferTemplate matching ``name`` or None for skip."""
    for t in instance.transfer_templates:
        if t.name == name:
            return t
    return None


def _emit_plant_chain_completion(
    parent_transfer_id: str,
    parent_name: Identifier,
    posting: str,
    *,
    account_id: Identifier,
    account_name: Name,
    account_role: Identifier,
    account_scope: str,
    account_parent_role: Identifier | None,
    instance: L2Instance,
    counter: _Counter,
    dialect: Dialect,
    base_seed: int = _BASELINE_BASE_SEED,
    multi_children_only: bool = False,
    via_template_name: Identifier | None = None,
) -> list[str]:
    """AJ.3 (Gap H residual): make a plant's chain-parent firing chain-
    complete by emitting its XOR/fan-in child.

    A plant that targets some OTHER invariant (rail-conformance,
    template-completion, XOR-variant, limit-breach, …) may fire a
    rail/template that *also* happens to be a Chain ``parent``. The
    firing then carries ``rail_name``/``template_name`` of a chain parent
    but has no child Transfer, so ``<prefix>_multi_xor_violation`` and the
    L2FT ``chain_orphans`` dataset false-positive it as a missing-child
    violation on a "healthy" seed.

    The fix is seed-side (NOT a matview/dataset filter — those run on
    real customer data where a childless chain-parent firing IS a
    violation, and there are no ``tr-*`` prefixes to filter on): emit one
    non-fan_in child per chain this firing parents, picked deterministi-
    cally by the SAME ``_baseline_xor_child_pick`` the baseline uses, so
    the planted firing is chain-complete and only trips the invariant it
    actually targets. The child reuses the plant's already-resolved
    account context (the matview/dataset key on ``transfer_parent_id`` +
    child name, not the child's account).

    No-op when ``parent_name`` parents no chain (the common case — most
    plant firings land on standalone rails). fan_in-only chains return no
    pick (their N:1 cardinality is ``_fan_in_disagreement``'s concern,
    not multi_xor / chain_orphans).

    ``multi_children_only`` (broad rail emitter only): skip single-child
    chains — ``auto_scenario._build_broad_rail_firings`` already links those
    to the parent firing, so completing them here would double the child.
    The picker skips multi-XOR chains, so those are ours to fill.

    ``via_template_name`` (broad rail emitter): a rail firing carries a
    ``template_name``; when the chain parent IS that template (not the
    rail), match it too — e.g. a broad firing of ``DisbursementCycle``'s
    leg-rail is a ``DisbursementCycle``-chain parent firing.
    """
    rail_names = {r.name for r in instance.rails}
    # A firing's chain-parent identity may be its rail_name OR its
    # template_name — the multi_xor matview attributes via either, so a
    # broad rail firing of a template-parented chain's leg-rail counts.
    candidate_parents = {str(parent_name)}
    if via_template_name is not None:
        candidate_parents.add(str(via_template_name))
    rows: list[str] = []
    for chain in instance.chains:
        if str(chain.parent) not in candidate_parents:
            continue
        if multi_children_only and len(chain.children) <= 1:
            # The broad rail picker already links single-child chains to
            # their parent firing (auto_scenario._build_broad_rail_firings);
            # only multi-XOR chains (which it skips) need completing here.
            # Without this guard the rail emitter double-emits the child.
            continue
        child_name = _baseline_xor_child_pick(
            chain, parent_transfer_id, base_seed,
        )
        if child_name is None:
            continue
        if child_name in rail_names:
            rail_for_row: Identifier = child_name
            template_for_row: Identifier | None = None
        else:
            child_res = _resolve_plant_chain_parent(child_name, instance)
            rail_for_row = child_res[0] if child_res else child_name
            template_for_row = child_name
        n = counter.next()
        rows.append(_txn_row(
            id_=f"tx-plant-chainfill-{n:04d}",
            account_id=account_id,
            account_name=account_name,
            account_role=account_role,
            account_scope=account_scope,
            account_parent_role=account_parent_role,
            money=Decimal("100.00"),
            direction="Credit",
            posting=posting,
            transfer_id=f"tr-plant-chainfill-{n:04d}",
            rail_name=rail_for_row,
            template_name=template_for_row,
            origin="InternalInitiated",
            metadata={},
            transfer_parent_id=parent_transfer_id,
            dialect=dialect,
        ))
    return rows


def _first_template_instance_or_skip(
    scenarios: ScenarioPlant,
) -> TemplateInstance | None:
    """Return the first materialized TemplateInstance, or None."""
    if scenarios.template_instances:
        return scenarios.template_instances[0]
    return None


def _emit_limit_breach_rows(
    p: LimitBreachPlant,
    instance: L2Instance,
    scenarios: ScenarioPlant,
    template_by_role: dict[Identifier, AccountTemplate],
    parent_singleton_by_role: dict[Identifier, Account],
    counter: _Counter,
    dialect: Dialect,
) -> list[str]:
    """Plant ONE outbound debit row exceeding the cap. The row alone is
    enough to drive `OutboundFlow > limit` for the (account, day, type)."""
    ti = _resolve_template(p.account_id, scenarios)
    template = template_by_role[ti.template_role]
    parent_role = template.parent_role
    counter_account = _resolve_account(p.counter_account_id, instance)
    counter_name = counter_account.name or Name(str(counter_account.id))
    counter_role = counter_account.role or Identifier(str(counter_account.id))
    breach_day = scenarios.today - timedelta(days=p.days_ago)
    posting_ts = (
        f"{breach_day.isoformat()}T14:00:00+00:00"  # 2pm — middle of business day
    )

    n = counter.next()
    txn_id = f"tx-breach-{n:04d}"
    transfer_id = f"tr-breach-{n:04d}"
    debit_money = -p.amount  # outbound = Debit; sign-direction agreement

    return [
        # Customer DDA debit leg (the breaching one)
        _txn_row(
            id_=txn_id,
            account_id=ti.account_id,
            account_name=ti.name,
            account_role=ti.template_role,
            account_scope=template.scope,
            account_parent_role=parent_role,
            money=debit_money,
            direction="Debit",
            posting=posting_ts,
            transfer_id=transfer_id,
            rail_name=p.rail_name,
            origin="InternalInitiated",
            metadata={"customer_id": str(ti.account_id)},
        
            dialect=dialect,
        ),
        # External counter-leg (no balance tracking, but needed for Conservation)
        _txn_row(
            id_=f"{txn_id}-ext",
            account_id=counter_account.id,
            account_name=counter_name,
            account_role=counter_role,
            account_scope=counter_account.scope,
            account_parent_role=counter_account.parent_role,
            money=p.amount,  # +ve: external receives
            direction="Credit",
            posting=posting_ts,
            transfer_id=transfer_id,
            rail_name=p.rail_name,
            origin="InternalInitiated",
            metadata={"customer_id": str(ti.account_id)},
        
            dialect=dialect,
        ),
    ]


def _emit_drift_background_rows(
    p: DriftPlant,
    instance: L2Instance,
    scenarios: ScenarioPlant,
    template_by_role: dict[Identifier, AccountTemplate],
    parent_singleton_by_role: dict[Identifier, Account],
    counter: _Counter,
    dialect: Dialect,
) -> list[str]:
    """For drift planting we want SOME postings on the day so the computed
    balance is meaningful. Plant two normal credits, each $100, so the
    computed balance is $200. The drift row will then state a different
    stored balance to surface the drift.

    The rail used is whatever ``p.rail_name`` declares; its
    ``transfer_type`` and per-leg origins come from the rail's L2
    declaration. The counter-leg is the L2 Account named by
    ``p.counter_account_id``.
    """
    ti = _resolve_template(p.account_id, scenarios)
    template = template_by_role[ti.template_role]
    parent_role = template.parent_role
    rail = _resolve_rail(p.rail_name, instance)
    counter_account = _resolve_account(p.counter_account_id, instance)
    counter_name = counter_account.name or Name(str(counter_account.id))
    counter_role = counter_account.role or Identifier(str(counter_account.id))
    counter_origin = _counter_origin_for_drift(rail)
    customer_origin = _customer_origin_for_drift(rail)
    drift_day = scenarios.today - timedelta(days=p.days_ago)
    rows: list[str] = []

    for hour in (9, 14):  # two credits during the business day
        n = counter.next()
        txn_id = f"tx-drift-{n:04d}"
        transfer_id = f"tr-drift-{n:04d}"
        posting_ts = f"{drift_day.isoformat()}T{hour:02d}:00:00+00:00"
        rows.extend([
            # External debit leg
            _txn_row(
                id_=f"{txn_id}-ext",
                account_id=counter_account.id,
                account_name=counter_name,
                account_role=counter_role,
                account_scope=counter_account.scope,
                account_parent_role=counter_account.parent_role,
                money=Decimal("-100.00"),
                direction="Debit",
                posting=posting_ts,
                transfer_id=transfer_id,
                rail_name=p.rail_name,
                origin=counter_origin,
                metadata={"customer_id": str(ti.account_id),
                          "external_reference": f"ER-{n:04d}"},
            
                dialect=dialect,
            ),
            # Customer DDA credit leg (the one we're tracking)
            _txn_row(
                id_=txn_id,
                account_id=ti.account_id,
                account_name=ti.name,
                account_role=ti.template_role,
                account_scope=template.scope,
                account_parent_role=parent_role,
                money=Decimal("100.00"),
                direction="Credit",
                posting=posting_ts,
                transfer_id=transfer_id,
                rail_name=p.rail_name,
                origin=customer_origin,
                metadata={"customer_id": str(ti.account_id),
                          "external_reference": f"ER-{n:04d}"},
            
                dialect=dialect,
            ),
        ])
    return rows


def _resolve_rail(rail_name: Identifier, instance: L2Instance) -> Rail:
    """Find the L2-declared Rail by name; raise on miss."""
    for r in instance.rails:
        if r.name == rail_name:
            return r
    raise KeyError(
        f"rail {rail_name!r} not declared in instance.rails; "
        f"plant references a rail that doesn't exist in the L2 YAML"
    )


def _customer_origin_for_drift(rail: Rail) -> str:
    """The customer-side leg's origin on a two-leg inbound drift rail.

    Two-leg rails carry per-leg origins (source_origin / destination_origin)
    or a shared rail-level origin. For drift background, the customer
    DDA receives the credit (destination side); fall back through the
    L2 origin-resolution table.
    """
    if isinstance(rail, TwoLegRail) and rail.destination_origin is not None:
        return str(rail.destination_origin)
    if rail.origin is not None:
        return str(rail.origin)
    return "InternalInitiated"


def _counter_origin_for_drift(rail: Rail) -> str:
    """The external-side leg's origin on a two-leg inbound drift rail."""
    if isinstance(rail, TwoLegRail) and rail.source_origin is not None:
        return str(rail.source_origin)
    if rail.origin is not None:
        return str(rail.origin)
    return "ExternalForcePosted"


def _emit_drift_balance_row(
    p: DriftPlant,
    scenarios: ScenarioPlant,
    template_by_role: dict[Identifier, AccountTemplate],
    role_offsets: dict[str, int] | None,
    dialect: Dialect,
) -> str:
    """Emit one daily_balances row whose `money` differs from the sum of
    that day's planted credits ($200) by `delta_money`.

    Surfaces drift = stored - computed = $200 + delta - $200 = delta.
    """
    ti = _resolve_template(p.account_id, scenarios)
    template = template_by_role[ti.template_role]
    parent_role = template.parent_role
    drift_day = scenarios.today - timedelta(days=p.days_ago)
    computed_from_postings = Decimal("200.00")
    stored = computed_from_postings + p.delta_money
    return _balance_row(
        account_id=ti.account_id,
        account_name=ti.name,
        account_role=ti.template_role,
        account_scope=template.scope,
        account_parent_role=parent_role,
        day=drift_day,
        money=stored,
        offset_hours=_resolve_role_offset(ti.template_role, role_offsets),
    
        dialect=dialect,
    )


def _emit_overdraft_balance_row(
    p: OverdraftPlant,
    scenarios: ScenarioPlant,
    template_by_role: dict[Identifier, AccountTemplate],
    role_offsets: dict[str, int] | None,
    dialect: Dialect,
) -> str:
    """Emit one daily_balances row with negative money — overdraft."""
    ti = _resolve_template(p.account_id, scenarios)
    template = template_by_role[ti.template_role]
    parent_role = template.parent_role
    if p.money >= 0:
        raise ValueError(
            f"OverdraftPlant.money must be negative; got {p.money!r}"
        )
    overdraft_day = scenarios.today - timedelta(days=p.days_ago)
    return _balance_row(
        account_id=ti.account_id,
        account_name=ti.name,
        account_role=ti.template_role,
        account_scope=template.scope,
        account_parent_role=parent_role,
        day=overdraft_day,
        money=p.money,
        offset_hours=_resolve_role_offset(ti.template_role, role_offsets),
    
        dialect=dialect,
    )


def _resolve_role_offset(
    role: Identifier, role_offsets: dict[str, int] | None,
) -> int:
    """Look up ``role``'s business-day offset hours (M.4.4.14).

    Returns 0 when no map is set OR the role is absent — preserves
    midnight-aligned behavior for production fixtures that don't opt
    into per-role offsets.
    """
    if not role_offsets:
        return 0
    return role_offsets.get(str(role), 0)


def _emit_stuck_pending_rows(
    p: StuckPendingPlant,
    scenarios: ScenarioPlant,
    template_by_role: dict[Identifier, AccountTemplate],
    counter: _Counter,
    dialect: Dialect,
) -> list[str]:
    """Plant ONE Pending leg (no external counter-leg — Pending legs
    haven't traversed the rail yet so the counter-leg doesn't exist).

    The rail's `max_pending_age` cap (inlined into the
    `<prefix>_stuck_pending` view at schema-emit time) determines
    when this surfaces; pick `days_ago` past whatever cap the chosen
    rail carries.
    """
    ti = _resolve_template(p.account_id, scenarios)
    template = template_by_role[ti.template_role]
    parent_role = template.parent_role
    plant_day = scenarios.today - timedelta(days=p.days_ago)
    posting_ts = f"{plant_day.isoformat()}T10:00:00+00:00"
    n = counter.next()
    return [
        _txn_row(
            id_=f"tx-pending-{n:04d}",
            account_id=ti.account_id,
            account_name=ti.name,
            account_role=ti.template_role,
            account_scope=template.scope,
            account_parent_role=parent_role,
            money=-p.amount,  # Debit
            direction="Debit",
            posting=posting_ts,
            transfer_id=f"tr-pending-{n:04d}",
            rail_name=p.rail_name,
            origin="InternalInitiated",
            metadata={"customer_id": str(ti.account_id)},
            status="Pending",

            dialect=dialect,
        ),
    ]


def _emit_failed_transaction_rows(
    p: FailedTransactionPlant,
    scenarios: ScenarioPlant,
    template_by_role: dict[Identifier, AccountTemplate],
    counter: _Counter,
    dialect: Dialect,
) -> list[str]:
    """Plant ONE Failed leg (X.1.i — drives the L2FT Status='Other'
    dropdown coverage).

    Single-leg, no counter-leg: a Failed transaction never settled, so
    the rail's other side wasn't created. The leg posts at a normal
    debit shape with ``status='Failed'`` so the L2FT postings dataset's
    ``CASE WHEN status IN ('Pending','Posted') THEN status ELSE 'Other'
    END`` collapses it to ``Other`` for the dropdown.
    """
    ti = _resolve_template(p.account_id, scenarios)
    template = template_by_role[ti.template_role]
    parent_role = template.parent_role
    plant_day = scenarios.today - timedelta(days=p.days_ago)
    posting_ts = f"{plant_day.isoformat()}T11:30:00+00:00"
    n = counter.next()
    return [
        _txn_row(
            id_=f"tx-failed-{n:04d}",
            account_id=ti.account_id,
            account_name=ti.name,
            account_role=ti.template_role,
            account_scope=template.scope,
            account_parent_role=parent_role,
            money=-p.amount,  # Debit attempt
            direction="Debit",
            posting=posting_ts,
            transfer_id=f"tr-failed-{n:04d}",
            rail_name=p.rail_name,
            origin="InternalInitiated",
            metadata={"customer_id": str(ti.account_id)},
            status="Failed",

            dialect=dialect,
        ),
    ]


def _emit_stuck_unbundled_rows(
    p: StuckUnbundledPlant,
    scenarios: ScenarioPlant,
    template_by_role: dict[Identifier, AccountTemplate],
    counter: _Counter,
    dialect: Dialect,
) -> list[str]:
    """Plant ONE Posted leg with `bundle_id IS NULL` on a rail whose
    `max_unbundled_age` cap has been exceeded.

    Per validator R8, `max_unbundled_age` is only meaningful on rails
    that appear in some AggregatingRail's `bundles_activity`. Pick a
    rail name + days_ago that satisfies both conditions.
    """
    ti = _resolve_template(p.account_id, scenarios)
    template = template_by_role[ti.template_role]
    parent_role = template.parent_role
    plant_day = scenarios.today - timedelta(days=p.days_ago)
    posting_ts = f"{plant_day.isoformat()}T11:00:00+00:00"
    n = counter.next()
    return [
        _txn_row(
            id_=f"tx-unbundled-{n:04d}",
            account_id=ti.account_id,
            account_name=ti.name,
            account_role=ti.template_role,
            account_scope=template.scope,
            account_parent_role=parent_role,
            money=-p.amount,  # Debit (fee accrual)
            direction="Debit",
            posting=posting_ts,
            transfer_id=f"tr-unbundled-{n:04d}",
            rail_name=p.rail_name,
            origin="InternalInitiated",
            metadata={"customer_id": str(ti.account_id)},
            # status defaults to Posted; bundle_id stays NULL — that's
            # the whole point.
            dialect=dialect,
        ),
    ]


def _emit_supersession_rows(
    p: SupersessionPlant,
    scenarios: ScenarioPlant,
    template_by_role: dict[Identifier, AccountTemplate],
    counter: _Counter,
    dialect: Dialect,
) -> list[str]:
    """Plant TWO transactions sharing one logical `id` — the original
    + a TechnicalCorrection rewrite.

    PostgreSQL's BIGSERIAL `entry` column auto-increments per insert,
    so the second row lands at a higher entry value; the M.2b.12
    Supersession Audit dataset's `COUNT(*) OVER (PARTITION BY id) > 1`
    catches the pair. No counter-leg — the corrected leg is the audit
    artifact, not a Conservation-bearing transfer.
    """
    ti = _resolve_template(p.account_id, scenarios)
    template = template_by_role[ti.template_role]
    parent_role = template.parent_role
    plant_day = scenarios.today - timedelta(days=p.days_ago)
    n = counter.next()
    txn_id = f"tx-supersedes-{n:04d}"
    transfer_id = f"tr-supersedes-{n:04d}"
    metadata = {"customer_id": str(ti.account_id)}
    return [
        # Original posting at 09:00.
        _txn_row(
            id_=txn_id,
            account_id=ti.account_id,
            account_name=ti.name,
            account_role=ti.template_role,
            account_scope=template.scope,
            account_parent_role=parent_role,
            money=-p.original_amount,
            direction="Debit",
            posting=f"{plant_day.isoformat()}T09:00:00+00:00",
            transfer_id=transfer_id,
            rail_name=p.rail_name,
            origin="InternalInitiated",
            metadata=metadata,
        
            dialect=dialect,
        ),
        # TechnicalCorrection at 09:30 — same logical id, different
        # amount, supersedes='TechnicalCorrection'.
        _txn_row(
            id_=txn_id,
            account_id=ti.account_id,
            account_name=ti.name,
            account_role=ti.template_role,
            account_scope=template.scope,
            account_parent_role=parent_role,
            money=-p.corrected_amount,
            direction="Debit",
            posting=f"{plant_day.isoformat()}T09:30:00+00:00",
            transfer_id=transfer_id,
            rail_name=p.rail_name,
            origin="InternalInitiated",
            metadata=metadata,
            supersedes="TechnicalCorrection",
        
            dialect=dialect,
        ),
    ]


def _resolve_transfer_template(
    template_name: Identifier,
    instance: L2Instance,
):
    """Find the L2-declared TransferTemplate by name; raise on miss."""
    for t in instance.transfer_templates:
        if t.name == template_name:
            return t
    raise KeyError(
        f"transfer_template {template_name!r} not declared in "
        f"instance.transfer_templates"
    )


@dataclass(frozen=True, slots=True)
class _ResolvedAccount:
    """Per-account fields the seed needs to emit a transactions row.

    Captures both the simple-Account case (singleton or external from
    instance.accounts) and the TemplateInstance case (materialized
    customer under an AccountTemplate) under one shape so the emit
    helper doesn't branch on which kind it got.
    """

    account_id: Identifier
    account_name: Name
    account_role: Identifier
    account_scope: str
    account_parent_role: Identifier | None


def _resolve_any_account(
    account_id: Identifier,
    instance: L2Instance,
    scenarios: ScenarioPlant,
    template_by_role: dict[Identifier, AccountTemplate],
) -> _ResolvedAccount:
    """Resolve account_id to (id, name, role, scope, parent_role).

    Tries scenarios.template_instances first (materialized customers),
    falls back to instance.accounts (singletons + externals). Raises
    KeyError if neither match.
    """
    for ti in scenarios.template_instances:
        if ti.account_id == account_id:
            tmpl = template_by_role[ti.template_role]
            return _ResolvedAccount(
                account_id=ti.account_id,
                account_name=ti.name,
                account_role=ti.template_role,
                account_scope=tmpl.scope,
                account_parent_role=tmpl.parent_role,
            )
    for a in instance.accounts:
        if a.id == account_id:
            return _ResolvedAccount(
                account_id=a.id,
                account_name=a.name or Name(str(a.id)),
                account_role=a.role or Identifier(str(a.id)),
                account_scope=a.scope,
                account_parent_role=a.parent_role,
            )
    raise KeyError(
        f"account_id {account_id!r} not found in template_instances "
        f"or instance.accounts"
    )


def _emit_transfer_template_rows(
    p: TransferTemplatePlant,
    instance: L2Instance,
    scenarios: ScenarioPlant,
    template_by_role: dict[Identifier, AccountTemplate],
    counter: _Counter,
    dialect: Dialect,
) -> list[str]:
    """Plant ONE shared Transfer firing of the L2-declared TransferTemplate.

    Branches on the first ``leg_rails`` entry's rail kind:

    - ``TwoLegRail`` — emits 2 legs (debit on source account + credit
      on destination account). Net = -amount + amount = 0 (matches
      ``expected_net = 0``).
    - ``SingleLegRail`` — emits 1 leg with direction per
      ``rail.leg_direction`` (``Variable`` treated as ``Debit`` for
      seed purposes; closing-leg semantics aren't material to the
      L2 hygiene checks the plant targets). Net = ±amount (the SQL's
      completion_status surfaces this as 'Imbalanced' against
      ``expected_net = 0`` — accurate L1 representation of a bare
      single-leg cycle without its sibling legs).

    Both shapes set:

    - ``transfer_id`` shared (= ``tr-tt-<n>``)
    - ``template_name`` = ``p.template_name``
    - ``transfer_type`` = the template's declared ``transfer_type``
    - ``rail_name`` = first ``leg_rails`` entry
    - ``transfer_key`` metadata values populated with synthetic
      per-firing values so the SPEC's "same transfer_key joins one
      shared Transfer" rule remains true.

    Templates with non-zero ``expected_net`` are not yet supported;
    the picker excludes them.
    """
    template = _resolve_transfer_template(p.template_name, instance)
    rail = _resolve_rail(template.leg_rails[0], instance)

    # transfer_key metadata: populate every declared transfer_key field
    # with a synthetic per-firing value so two firings of the same
    # template don't collapse to one shared Transfer (per SPEC).
    metadata = {
        str(k): f"{p.template_name}-firing-{p.firing_seq:04d}"
        for k in template.transfer_key
    }

    plant_day = scenarios.today - timedelta(days=p.days_ago)
    n = counter.next()
    txn_id = f"tx-tt-{n:04d}"
    transfer_id = f"tr-tt-{n:04d}"
    posting_ts = f"{plant_day.isoformat()}T11:00:00+00:00"

    if isinstance(rail, TwoLegRail):
        src = _resolve_any_account(
            p.source_account_id, instance, scenarios, template_by_role,
        )
        dst = _resolve_any_account(
            p.destination_account_id, instance, scenarios, template_by_role,
        )

        # Origin resolution per L2 rule O1: per-leg overrides take precedence
        # over the rail-level shared origin.
        src_origin = (
            str(rail.source_origin) if rail.source_origin is not None
            else (str(rail.origin) if rail.origin is not None else "InternalInitiated")
        )
        dst_origin = (
            str(rail.destination_origin) if rail.destination_origin is not None
            else (str(rail.origin) if rail.origin is not None else "InternalInitiated")
        )

        rows = [
            # Source-side leg (debit, money out).
            _txn_row(
                id_=f"{txn_id}-src",
                account_id=src.account_id,
                account_name=src.account_name,
                account_role=src.account_role,
                account_scope=src.account_scope,
                account_parent_role=src.account_parent_role,
                money=-p.amount,
                direction="Debit",
                posting=posting_ts,
                transfer_id=transfer_id,
                rail_name=rail.name,
                origin=src_origin,
                metadata=metadata,
                template_name=p.template_name,

                dialect=dialect,
            ),
            # Destination-side leg (credit, money in).
            _txn_row(
                id_=txn_id,
                account_id=dst.account_id,
                account_name=dst.account_name,
                account_role=dst.account_role,
                account_scope=dst.account_scope,
                account_parent_role=dst.account_parent_role,
                money=p.amount,
                direction="Credit",
                posting=posting_ts,
                transfer_id=transfer_id,
                rail_name=rail.name,
                origin=dst_origin,
                metadata=metadata,
                template_name=p.template_name,

                dialect=dialect,
            ),
        ]
    else:
        # SingleLegRail — emit one leg using source_account_id as the
        # leg account (destination_account_id ignored; picker sets it
        # to the same value for shape consistency).
        assert isinstance(rail, SingleLegRail)
        leg = _resolve_any_account(
            p.source_account_id, instance, scenarios, template_by_role,
        )
        if rail.leg_direction == "Credit":
            direction, money = "Credit", p.amount
        else:
            # Debit OR Variable — treat as Debit; the closing-leg
            # semantics aren't material for the plant's purpose
            # (surfacing data on the TT explorer).
            direction, money = "Debit", -p.amount
        leg_origin = (
            str(rail.origin) if rail.origin is not None
            else "InternalInitiated"
        )
        rows = [
            _txn_row(
                id_=txn_id,
                account_id=leg.account_id,
                account_name=leg.account_name,
                account_role=leg.account_role,
                account_scope=leg.account_scope,
                account_parent_role=leg.account_parent_role,
                money=money,
                direction=direction,
                posting=posting_ts,
                transfer_id=transfer_id,
                rail_name=rail.name,
                origin=leg_origin,
                metadata=metadata,
                template_name=p.template_name,

                dialect=dialect,
            ),
        ]

    # Chain children (M.3.10h). For each pre-resolved (child_rail,
    # account) pair, plant ONE child leg whose transfer_parent_id
    # points at this firing's transfer_id — that's what the L2 chain
    # detection SQL matches against. Single-leg child plants don't
    # satisfy L1 conservation in isolation, but the chain dataset's
    # detection only needs EXISTS of a leg with the right rail_name +
    # transfer_parent_id, which this satisfies. Posting timestamps
    # one hour after the parent for visual sequencing in the explorer.
    if p.chain_children:
        child_posting_ts = f"{plant_day.isoformat()}T12:00:00+00:00"
        for child_rail_name, child_account_id in p.chain_children:
            child_rail = _resolve_rail(child_rail_name, instance)
            child_acct = _resolve_any_account(
                child_account_id, instance, scenarios, template_by_role,
            )
            child_origin = (
                str(child_rail.origin)
                if child_rail.origin is not None
                else "InternalInitiated"
            )
            cn = counter.next()
            child_txn_id = f"tx-tt-cc-{cn:04d}"
            child_transfer_id = f"tr-tt-cc-{cn:04d}"
            rows.append(_txn_row(
                id_=child_txn_id,
                account_id=child_acct.account_id,
                account_name=child_acct.account_name,
                account_role=child_acct.account_role,
                account_scope=child_acct.account_scope,
                account_parent_role=child_acct.account_parent_role,
                money=p.amount,
                direction="Credit",
                posting=child_posting_ts,
                transfer_id=child_transfer_id,
                rail_name=child_rail.name,
                origin=child_origin,
                metadata=metadata,
                transfer_parent_id=transfer_id,

                dialect=dialect,
            ))
    return rows


def _emit_rail_firing_rows(
    p: RailFiringPlant,
    instance: L2Instance,
    scenarios: ScenarioPlant,
    template_by_role: dict[Identifier, AccountTemplate],
    counter: _Counter,
    dialect: Dialect,
) -> list[str]:
    """Plant ONE Posted firing of an L2-declared Rail (M.4.2 broad mode).

    Two-leg rails plant 2 legs:
      - debit on ``account_id_a`` for -amount
      - credit on ``account_id_b`` for +amount

    Single-leg rails plant 1 leg:
      - on ``account_id_a``, direction per ``Rail.leg_direction``
      - amount sign per the direction (Variable treated as Debit for
        the seed; the closing-leg semantics aren't material to the
        L2 hygiene checks the broad mode targets)

    Per-leg Origin resolves through the SPEC's Origin resolution rules
    (validator O1) — rail-level ``origin`` falls back if a per-leg
    override isn't set; for 1-leg rails ``origin`` is required (validator).

    Metadata:
      - Auto-derived ``transfer_key`` field values come from any
        containing TransferTemplate's transfer_key.
      - ``extra_metadata`` (per-key tuples on the plant) supplies
        values for the rail's other declared metadata_keys.
      - The two sources are merged at emit time; ``extra_metadata``
        wins on overlap (gives the broad-mode picker explicit control).

    ``transfer_parent_id`` is set when the plant carries one — used
    for chain-child firings the broad picker links into Required
    chain entries.
    """
    rail = _resolve_rail(p.rail_name, instance)

    n = counter.next()
    plant_day = scenarios.today - timedelta(days=p.days_ago)
    posting_ts = f"{plant_day.isoformat()}T11:00:00+00:00"
    transfer_id = f"tr-rail-{n:04d}"
    txn_id = f"tx-rail-{n:04d}"

    src = _resolve_any_account(
        p.account_id_a, instance, scenarios, template_by_role,
    )
    dst = (
        _resolve_any_account(
            p.account_id_b, instance, scenarios, template_by_role,
        ) if p.account_id_b is not None else None
    )

    # Metadata: TransferKey fields auto-derived from any containing
    # TransferTemplate, plus the plant's extra_metadata. Per-firing
    # values keyed off rail name + firing seq so two firings of the
    # same rail produce distinct values (the L2 Flow Tracing metadata
    # cascade reads distinct values from this column).
    metadata: dict[str, str] = {}
    for tt in instance.transfer_templates:
        if rail.name not in tt.leg_rails:
            continue
        for k in tt.transfer_key:
            metadata[str(k)] = (
                f"{rail.name}-firing-{p.firing_seq:04d}"
            )
    for key, value in p.extra_metadata:
        metadata[key] = value

    if isinstance(rail, TwoLegRail):
        if dst is None:
            raise ValueError(
                f"_emit_rail_firing_rows: TwoLegRail {rail.name!r} requires "
                f"account_id_b for the destination leg"
            )
        src_origin = (
            str(rail.source_origin) if rail.source_origin is not None
            else (str(rail.origin) if rail.origin is not None else "InternalInitiated")
        )
        dst_origin = (
            str(rail.destination_origin) if rail.destination_origin is not None
            else (str(rail.origin) if rail.origin is not None else "InternalInitiated")
        )
        rows = [
            _txn_row(
                id_=f"{txn_id}-src",
                account_id=src.account_id,
                account_name=src.account_name,
                account_role=src.account_role,
                account_scope=src.account_scope,
                account_parent_role=src.account_parent_role,
                money=-p.amount,
                direction="Debit",
                posting=posting_ts,
                transfer_id=transfer_id,
                rail_name=rail.name,
                origin=src_origin,
                metadata=metadata,
                transfer_parent_id=p.transfer_parent_id,
                template_name=p.template_name,
            
                dialect=dialect,
            ),
            _txn_row(
                id_=txn_id,
                account_id=dst.account_id,
                account_name=dst.account_name,
                account_role=dst.account_role,
                account_scope=dst.account_scope,
                account_parent_role=dst.account_parent_role,
                money=p.amount,
                direction="Credit",
                posting=posting_ts,
                transfer_id=transfer_id,
                rail_name=rail.name,
                origin=dst_origin,
                metadata=metadata,
                transfer_parent_id=p.transfer_parent_id,
                template_name=p.template_name,
            
                dialect=dialect,
            ),
        ]
        # AJ.6 (Gap H residual, broad mode): if this rail is also a
        # multi-XOR chain parent, emit its XOR-pick child so the firing is
        # chain-complete — otherwise it false-positives as a childless
        # multi_xor_violation / chain_orphan. Same pick the baseline +
        # sibling plant emitters use (no-op when the rail parents no chain).
        rows.extend(_emit_plant_chain_completion(
            transfer_id, rail.name, posting_ts,
            account_id=dst.account_id, account_name=dst.account_name,
            account_role=dst.account_role, account_scope=dst.account_scope,
            account_parent_role=dst.account_parent_role,
            instance=instance, counter=counter, dialect=dialect,
            multi_children_only=True, via_template_name=p.template_name,
        ))
        return rows

    # SingleLegRail (the only other arm of the discriminated union;
    # exhaustion guarded by the TwoLegRail isinstance branch above).
    assert isinstance(rail, SingleLegRail)
    if rail.leg_direction == "Credit":
        direction, money = "Credit", p.amount
    else:
        # Debit OR Variable — Variable closing-leg semantics aren't
        # material to broad-mode L2 hygiene checks; treat as Debit
        # so the firing has a consistent sign.
        direction, money = "Debit", -p.amount
    leg_origin = (
        str(rail.origin) if rail.origin is not None
        else "InternalInitiated"
    )
    rows = [
        _txn_row(
            id_=txn_id,
            account_id=src.account_id,
            account_name=src.account_name,
            account_role=src.account_role,
            account_scope=src.account_scope,
            account_parent_role=src.account_parent_role,
            money=money,
            direction=direction,
            posting=posting_ts,
            transfer_id=transfer_id,
            rail_name=rail.name,
            origin=leg_origin,
            metadata=metadata,
            transfer_parent_id=p.transfer_parent_id,
            template_name=p.template_name,
        
            dialect=dialect,
        ),
    ]
    # AJ.6 (Gap H residual, broad mode): same completion for a single-leg
    # rail firing that is also a chain parent.
    rows.extend(_emit_plant_chain_completion(
        transfer_id, rail.name, posting_ts,
        account_id=src.account_id, account_name=src.account_name,
        account_role=src.account_role, account_scope=src.account_scope,
        account_parent_role=src.account_parent_role,
        instance=instance, counter=counter, dialect=dialect,
        multi_children_only=True, via_template_name=p.template_name,
    ))
    return rows


def _emit_inv_fanout_rows(
    p: InvFanoutPlant,
    instance: L2Instance,
    scenarios: ScenarioPlant,
    template_by_role: dict[Identifier, AccountTemplate],
    counter: _Counter,
    dialect: Dialect,
) -> list[str]:
    """Plant N two-leg transfers: every sender debits, the same recipient
    credits, all on ``days_ago`` (N.4.h Investigation coverage).

    Each transfer is one ``transfer_id`` with two legs (debit on sender +
    credit on recipient summing to zero), so the
    ``<prefix>_inv_money_trail_edges`` recursive CTE matches both legs
    via ``transfer_id`` and emits ONE depth-0 edge per transfer. The
    recipient's leaf-internal status (template_role w/ parent_role set)
    satisfies the ``<prefix>_inv_pair_rolling_anomalies`` filter
    (``account_scope='internal' AND account_parent_role IS NOT NULL``)
    so the rolling-window aggregation has data to operate on.

    Posting times are stratified across the day (10am, 11am, …) per
    sender to keep transfer_ids visually distinct in the dashboard
    detail tables. Rail / transfer_type are not validated against L2
    declarations beyond what ``_resolve_rail`` enforces (we let the
    plant declare any rail name; the Inv matviews don't read rail).
    """
    recipient = _resolve_any_account(
        p.recipient_account_id, instance, scenarios, template_by_role,
    )
    plant_day = scenarios.today - timedelta(days=p.days_ago)
    rows: list[str] = []
    # Sort senders for deterministic ordering even if plant carries them
    # in a different order across calls.
    for idx, sender_id in enumerate(sorted(p.sender_account_ids, key=str)):
        sender = _resolve_any_account(
            sender_id, instance, scenarios, template_by_role,
        )
        n = counter.next()
        # Posting time stratified by sender index, wrapping at 24h so a
        # >24-sender plant doesn't blow past midnight (would shift the
        # plant into the next business day).
        hour = 10 + (idx % 12)
        posting_ts = f"{plant_day.isoformat()}T{hour:02d}:00:00+00:00"
        transfer_id = f"tr-inv-fanout-{n:04d}"
        txn_id = f"tx-inv-fanout-{n:04d}"
        rows.extend([
            # Sender debit leg
            _txn_row(
                id_=f"{txn_id}-src",
                account_id=sender.account_id,
                account_name=sender.account_name,
                account_role=sender.account_role,
                account_scope=sender.account_scope,
                account_parent_role=sender.account_parent_role,
                money=-p.amount_per_transfer,
                direction="Debit",
                posting=posting_ts,
                transfer_id=transfer_id,
                rail_name=p.rail_name,
                origin="ExternalInitiated",
                metadata={
                    "sender_id": str(sender.account_id),
                    "recipient_id": str(recipient.account_id),
                },
            
                dialect=dialect,
            ),
            # Recipient credit leg
            _txn_row(
                id_=txn_id,
                account_id=recipient.account_id,
                account_name=recipient.account_name,
                account_role=recipient.account_role,
                account_scope=recipient.account_scope,
                account_parent_role=recipient.account_parent_role,
                money=p.amount_per_transfer,
                direction="Credit",
                posting=posting_ts,
                transfer_id=transfer_id,
                rail_name=p.rail_name,
                origin="ExternalInitiated",
                metadata={
                    "sender_id": str(sender.account_id),
                    "recipient_id": str(recipient.account_id),
                },
            
                dialect=dialect,
            ),
        ])
    return rows


def _txn_row(
    *,
    id_: str,
    account_id: Identifier,
    account_name: Name,
    account_role: Identifier,
    account_scope: str,
    account_parent_role: Identifier | None,
    money: Decimal,
    direction: str,
    posting: str,
    transfer_id: str,
    rail_name: Identifier,
    origin: str,
    metadata: dict[str, str],
    dialect: Dialect,
    status: str = "Posted",
    bundle_id: str | None = None,
    supersedes: str | None = None,
    template_name: Identifier | None = None,
    transfer_parent_id: str | None = None,
) -> str:
    """Build one VALUES row for the transactions INSERT.

    `status` defaults to 'Posted' — the M.2.2 baseline scenarios all
    plant Posted legs. `bundle_id` and `supersedes` default to NULL —
    M.2b.14 plants exercise them for stuck-Unbundled / supersession
    scenarios. `template_name` defaults to NULL; the M.3.10g
    TransferTemplate plant is the first scenario kind to populate it.
    `transfer_parent_id` defaults to NULL; the M.3.10h chain-child
    legs are the first scenario kind to populate it (linking child
    Transfers back to their parent Transfer's transfer_id so the L2
    chain detection SQL sees a matched child). `transfer_completion`
    isn't currently exercised, emits as NULL.

    ``dialect`` flows into ``_sql_timestamp_literal`` for the
    ``posting`` column — PG keeps the bare ISO-8601 string; Oracle
    wraps in ``TIMESTAMP 'YYYY-MM-DD HH:MI:SS+TZ'``.
    """
    parent_role_lit = (
        _sql_str(account_parent_role) if account_parent_role else "NULL"
    )
    metadata_json = (
        "{" + ", ".join(
            f'"{k}": "{v}"' for k, v in sorted(metadata.items())
        ) + "}"
    )
    bundle_lit = _sql_str(bundle_id) if bundle_id is not None else "NULL"
    supersedes_lit = _sql_str(supersedes) if supersedes is not None else "NULL"
    template_lit = (
        _sql_str(template_name) if template_name is not None else "NULL"
    )
    transfer_parent_lit = (
        _sql_str(transfer_parent_id) if transfer_parent_id is not None
        else "NULL"
    )
    return (
        f"({_sql_str(id_)}, {_sql_str(account_id)}, "
        f"{_sql_str(account_name)}, {_sql_str(account_role)}, "
        f"{_sql_str(account_scope)}, {parent_role_lit}, "
        f"{money}, {_sql_str(direction)}, {_sql_str(status)}, "
        f"{_sql_timestamp_literal(posting, dialect)}, "
        f"{_sql_str(transfer_id)}, "
        f"NULL, {transfer_parent_lit}, "
        f"{_sql_str(rail_name)}, {template_lit}, "
        f"{bundle_lit}, {supersedes_lit}, "
        f"{_sql_str(origin)}, {_sql_str(metadata_json)})"
    )


def _balance_row(
    *,
    account_id: Identifier,
    account_name: Name,
    account_role: Identifier,
    account_scope: str,
    account_parent_role: Identifier | None,
    day: date,
    money: Decimal,
    dialect: Dialect,
    offset_hours: int = 0,
) -> str:
    """Build one VALUES row for the daily_balances INSERT.

    ``offset_hours`` shifts ``business_day_start`` and
    ``business_day_end`` by the same amount (M.4.4.14) — a
    role with offset=17 emits 17:00→17:00 next day. Default 0 keeps
    production midnight-aligned (no hash drift).

    ``dialect`` flows into ``_sql_timestamp_literal`` for the
    business_day_start / business_day_end columns. Note these columns
    are demoted to plain TIMESTAMP on Oracle (PK-eligibility, see
    P.5.b), so the typed literal's TZ portion is dropped at insert
    time — accepted by Oracle, lossless for our midnight-aligned
    snapshot timestamps.
    """
    parent_role_lit = (
        _sql_str(account_parent_role) if account_parent_role else "NULL"
    )
    return (
        f"({_sql_str(account_id)}, {_sql_str(account_name)}, "
        f"{_sql_str(account_role)}, {_sql_str(account_scope)}, "
        f"{parent_role_lit}, NULL, "
        f"{_sql_timestamp_literal(_bod_timestamp(day, offset_hours), dialect)}, "
        f"{_sql_timestamp_literal(_eod_timestamp(day, offset_hours), dialect)}, "
        f"{money}, NULL, NULL)"
    )


def _sql_str(s: object) -> str:
    """Render an arbitrary value as a SQL string literal (single-quoted,
    embedded quotes doubled). Used for every text column in the seed."""
    return "'" + str(s).replace("'", "''") + "'"
