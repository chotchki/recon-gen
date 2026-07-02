"""Canonical residuals — the single home of each math invariant's law (DS.1).

Every law here was authored from the WRITTEN statements (the twelve
SHOULD-constraints in ``docs/L1_Invariants.md`` + the signed DS.0 kit in
``docs/audits/ds_0_invariant_parity.md`` §4), never transliterated from
the matview SQL — an SQL-derived residual would verify the SQL against
itself. The matview stays the DETECTOR; each residual is the SPEC the
detector is checked against (DS.3's enumeration gate asserts
``engine violation set == {cells : residual != 0}`` on the real engine).

Residuals implement the DECIDED laws, which means some are deliberately
BORN-DIVERGENT from today's SQL until the red-first fixes land:

- drift compares CARRIED days (DS.3.2 — today's matview provably cannot
  emit carried-day rows);
- the status law is money = 'Posted' only, firing counts =
  IN ('Posted', 'Pending') with the unknown tail on the failed side
  (DS.3.3 — today's cardinality SQL counts ``<> 'Failed'``);
- multi_xor counts distinct fired sibling NAMES, once each (DS.3.3a —
  today's SQL multiplies by distinct posting days).

Money-family residuals are branch-free: the ONLY conditional is the
``when()`` combinator, whose arguments evaluate eagerly (both sides are
always computed; selection is pure data-flow), so DST.1's symbolic
executor can run the SAME function bodies over z3 terms. The op
whitelist for these functions (no ``/ // % abs min max``, no ``if``)
is lint-enforced in ``tests/unit/test_ds1_residual_lint.py``.
Threshold / cardinality / derivation residuals are plain Python — their
∀-ℤ theorems are near-vacuous, so they don't pay the style tax.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum, auto
from typing import TypeVar

from recon_gen.common.money import Cents

T = TypeVar("T")

ZERO = Cents(0)

# -- The status law (operator-decided 2026-07-02, DS.0 sign-off) ------------
#
# ``status`` is a purposefully OPEN column (no CHECK). The law closes the
# open tail conservatively: money moves on Posted ONLY; a rail firing
# counts on Posted or Pending (in-flight legs still represent a rail
# choice); anything else — Failed or a value nobody has seen before —
# lands on the failed side in BOTH families, so corrupt status data can
# narrow a count or a sum but never silently widen one.
MONEY_STATUSES: frozenset[str] = frozenset({"Posted"})
FIRING_STATUSES: frozenset[str] = frozenset({"Posted", "Pending"})


class MathKind(Enum):
    """Residual kind — fixes the residual signature per invariant family."""

    MONEY = auto()          # residual -> Cents; zero = law holds
    CARDINALITY = auto()    # residual -> int count-delta; zero = law holds
    THRESHOLD = auto()      # residual -> int seconds-over-cap; > 0 = violation
    DERIVATION = auto()     # residual -> edge-set symmetric difference; empty = law holds
    PROBABILISTIC = auto()  # NO residual — tolerance contract owned by DS.4


# -- State vocabulary (Cents-native, persona-blind) --------------------------


@dataclass(frozen=True, slots=True)
class LegRow:
    """One money-movement leg — the residual-domain projection of a
    ``<prefix>_transactions`` row. ``entry`` is the supersession key
    (highest entry per ``id`` wins)."""

    id: str
    entry: int
    account_id: str
    amount: Cents
    status: str
    posting: datetime
    transfer_id: str
    transfer_parent_id: str | None = None
    rail_name: str | None = None
    template_name: str | None = None
    bundle_id: str | None = None
    account_scope: str = "internal"
    account_role: str | None = None
    account_parent_role: str | None = None


@dataclass(frozen=True, slots=True)
class BalanceRow:
    """One stored end-of-day balance claim — the residual-domain
    projection of a ``<prefix>_daily_balances`` row. ``entry`` is the
    supersession key (highest entry per ``(account_id, day)`` wins).
    ``account_parent_role is None`` marks a PARENT account (leaf
    accounts link up via their parent's role)."""

    account_id: str
    entry: int
    day: date
    money: Cents
    #: The fed business_day_end — an institution's day can end intraday
    #: (a 17:00 cutover). None ⇒ the law defaults to end-of-calendar-day.
    day_end: datetime | None = None
    expected_eod: Cents | None = None
    account_scope: str = "internal"
    account_role: str | None = None
    account_parent_role: str | None = None


@dataclass(frozen=True, slots=True)
class ResidualState:
    """The full feed state a residual is evaluated over."""

    legs: tuple[LegRow, ...] = field(default_factory=tuple)
    balances: tuple[BalanceRow, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class TrailEdge:
    """One money-trail edge: root-labeled src->tgt account pair at a depth."""

    root_transfer_id: str
    src_account_id: str
    tgt_account_id: str
    depth: int


def when(cond: bool, then: T, otherwise: T) -> T:
    """Branch-free select — the only conditional allowed in money-family
    residual bodies.

    Both branches arrive ALREADY EVALUATED (Python argument semantics),
    so the data-flow shape is identical for concrete ints and z3 terms;
    DST.1's symbolic adapter swaps the selection for ``z3.If`` at this
    one seam without touching any law body.
    """
    return then if cond else otherwise


# -- Shared lemmas: supersession + carry-forward ------------------------------


def current_legs(state: ResidualState) -> tuple[LegRow, ...]:
    """Supersession over legs: the highest ``entry`` per logical ``id``
    wins. (The composite PK ``(id, entry)`` makes ties unrepresentable
    at the DDL level; the max here is total.)"""
    newest: dict[str, LegRow] = {}
    for leg in state.legs:
        held = newest.get(leg.id)
        if held is None or leg.entry > held.entry:
            newest[leg.id] = leg
    return tuple(newest.values())


def current_balances(state: ResidualState) -> tuple[BalanceRow, ...]:
    """Supersession over balance claims: the highest ``entry`` per
    ``(account_id, day)`` wins."""
    newest: dict[tuple[str, date], BalanceRow] = {}
    for row in state.balances:
        key = (row.account_id, row.day)
        held = newest.get(key)
        if held is None or row.entry > held.entry:
            newest[key] = row
    return tuple(newest.values())


def effective_balance(state: ResidualState, account_id: str, day: date) -> Cents | None:
    """Carry-forward (LOCF): a balance entry is the source of an
    account's balance UNTIL a newer entry supersedes it (operator-decided
    law, DS.0 finding 1). Returns the most recent current claim at or
    before ``day``, or None before the account's first emit."""
    best: BalanceRow | None = None
    for row in current_balances(state):
        if row.account_id == account_id and row.day <= day:
            if best is None or row.day > best.day:
                best = row
    return None if best is None else best.money


def effective_cutoff(state: ResidualState, account_id: str, day: date) -> datetime:
    """The day's posting cutoff under the carried-cutover rule
    (operator-decided, DS.3.2): the last loaded balance day's END
    time-of-day carries forward — a 17:00 cutover stays 17:00 on every
    quiet day until the next loaded balance row. Defaults to
    end-of-calendar-day when the anchoring emit carries no day_end (or
    no emit exists yet)."""
    anchor: BalanceRow | None = None
    for row in current_balances(state):
        if row.account_id == account_id and row.day <= day:
            if anchor is None or row.day > anchor.day:
                anchor = row
    if anchor is None or anchor.day_end is None:
        return datetime.combine(day + timedelta(days=1), datetime.min.time())
    return datetime.combine(day, anchor.day_end.time())


# -- MONEY family (branch-free; op whitelist lint-enforced) -------------------


def drift_residual(state: ResidualState, account_id: str, day: date) -> Cents | None:
    """Law 1 (drift), as signed at DS.0: on EVERY day — emitted or
    carried — an internal LEAF account's effective stored balance equals
    the cumulative sum of Posted leg amounts posted through that day's
    end. The cumulative sum starts at ZERO: the feed contract requires
    transaction history complete from account origin (cutover
    workaround: one synthetic opening transaction + balance row).

    Returns ``reported − calculated`` in Cents; None = no cell (account
    not internal-leaf, or before its first emit).

    BORN-DIVERGENT on carried days until DS.3.2 lands — today's matview
    provably cannot emit a carried-day row (DS.0 doc §6.1, E1b).
    """
    stored = effective_balance(state, account_id, day)
    scope_rows = [
        b for b in current_balances(state)
        if b.account_id == account_id
        and b.account_scope == "internal"
        and b.account_parent_role is not None
    ]
    if stored is None or not scope_rows:
        return None
    cutoff = effective_cutoff(state, account_id, day)
    calculated = Cents(sum(
        when(leg.status in MONEY_STATUSES and leg.posting <= cutoff, leg.amount.value, 0)
        for leg in current_legs(state)
        if leg.account_id == account_id
    ))
    return stored - calculated


def ledger_drift_residual(state: ResidualState, parent_account_id: str, day: date) -> Cents | None:
    """Law 2 (ledger_drift), as signed at DS.0: a parent account's
    effective stored balance equals the sum of its child accounts'
    effective stored balances PLUS the parent's own direct Posted
    postings through the day's end. (The handbook prose omits the
    direct-postings term — the signed DS.0 signature carries it; the
    prose fix rides this task.)

    Children link to the parent via ``account_parent_role == the
    parent's account_role``. Child balances carry forward (a quiet child
    still holds its position). Returns ``reported − calculated`` in
    Cents; None = no cell (not an internal parent, or pre-first-emit).
    """
    parent_rows = [
        b for b in current_balances(state)
        if b.account_id == parent_account_id
        and b.account_scope == "internal"
        and b.account_parent_role is None
    ]
    stored = effective_balance(state, parent_account_id, day)
    if stored is None or not parent_rows:
        return None
    parent_role = parent_rows[0].account_role
    child_ids = {
        b.account_id for b in current_balances(state)
        if b.account_parent_role is not None and b.account_parent_role == parent_role
    }
    child_sum = Cents(sum(
        stored_child.value
        for child_id in child_ids
        if (stored_child := effective_balance(state, child_id, day)) is not None
    ))
    cutoff = effective_cutoff(state, parent_account_id, day)
    direct = Cents(sum(
        when(leg.status in MONEY_STATUSES and leg.posting <= cutoff, leg.amount.value, 0)
        for leg in current_legs(state)
        if leg.account_id == parent_account_id
    ))
    return stored - (child_sum + direct)


def overdraft_residual(state: ResidualState, account_id: str, day: date) -> Cents | None:
    """Law 3 (overdraft): an internal account's effective balance is
    ≥ 0 on every day, emitted or carried (CL.5 rewired the detector onto
    the carried balance; the handbook prose still quantifies over
    emitted rows — prose fix rides this task). External counterparties
    are excluded by construction.

    Residual = ``min(effective, 0)`` expressed branch-free: zero = law
    holds, negative = the overdraft magnitude. None = no cell.
    """
    scope_rows = [
        b for b in current_balances(state)
        if b.account_id == account_id and b.account_scope == "internal"
    ]
    stored = effective_balance(state, account_id, day)
    if stored is None or not scope_rows:
        return None
    return when(stored < ZERO, stored, ZERO)


def expected_eod_residual(state: ResidualState, account_id: str, day: date) -> Cents | None:
    """Law 4 (expected EOD): where an EMITTED balance claim carries an
    ``expected_eod_balance``, the stored money equals it. No carry — the
    expectation binds the day it was emitted for (the DS.0 inventory
    reads ``current_daily_balances``, not the effective view).

    Residual = ``money − expected`` in Cents; None = no cell (no emit
    that day, or no expectation set).
    """
    rows = [
        b for b in current_balances(state)
        if b.account_id == account_id and b.day == day and b.expected_eod is not None
    ]
    if not rows:
        return None
    row = rows[0]
    expected = row.expected_eod
    if expected is None:  # unreachable (filtered above); narrows the type
        return None
    return row.money - expected


def limit_breach_residual(
    state: ResidualState,
    account_id: str,
    day: date,
    rail_name: str,
    direction: str,
    cap: Cents | None,
) -> Cents | None:
    """Law 5 (limit breach): per ``(child account, day, rail,
    direction)``, cumulative flow magnitude stays ≤ the L2-declared cap.
    ``cap`` arrives L2-RESOLVED in Cents (the ×100 dollars→cents shift
    is the caller's resolution concern — config data, never SQL text).

    Direction matches the leg's sign (Outbound = money out, amount < 0;
    Inbound = amount > 0) — the sign↔direction CHECK constraint makes
    this equivalent to matching ``amount_direction``. Flow counts Posted
    legs only (money moves on Posted). Residual = ``max(0, flow − cap)``
    branch-free; None = no cell (no cap declared for the tuple).
    """
    if cap is None:
        return None
    outbound = direction == "Outbound"
    flow = Cents(sum(
        when(
            leg.status in MONEY_STATUSES
            and leg.posting.date() == day
            and leg.rail_name == rail_name
            and when(outbound, leg.amount < ZERO, leg.amount > ZERO),
            when(leg.amount < ZERO, -leg.amount, leg.amount).value,
            0,
        )
        for leg in current_legs(state)
        if leg.account_id == account_id
    ))
    over = flow - cap
    return when(over > ZERO, over, ZERO)


#: The lint boundary: these functions carry the branch-free + op-whitelist
#: discipline (tests/unit/test_ds1_residual_lint.py walks their ASTs), and
#: DST.1's symbolic executor runs exactly this set over z3 terms.
MONEY_FAMILY_RESIDUALS: tuple[object, ...] = (
    drift_residual,
    ledger_drift_residual,
    overdraft_residual,
    expected_eod_residual,
    limit_breach_residual,
)


# -- THRESHOLD family (plain Python; as_of is an EXPLICIT parameter) ----------


def stuck_pending_residual(leg: LegRow, cap_seconds: int | None, as_of: datetime) -> int | None:
    """Law 6 (stuck pending): a Pending leg on a rail with a declared
    ``max_pending_age`` transitions before ``posting + cap``. ``as_of``
    is the owned temporal frame passed explicitly — never wall-clock.

    Residual = ``age − cap`` in whole seconds; violation iff > 0
    (exactly-at-cap is NOT stuck — strict ``>``). None = no cell (no
    cap on the rail, or the leg is not Pending).
    """
    if cap_seconds is None or leg.status != "Pending":
        return None
    age_seconds = (as_of - leg.posting) // timedelta(seconds=1)
    return age_seconds - cap_seconds


def stuck_unbundled_residual(leg: LegRow, cap_seconds: int | None, as_of: datetime) -> int | None:
    """Law 7 (stuck unbundled): a Posted leg on a rail with a declared
    ``max_unbundled_age`` gets picked up by an AggregatingRail
    (``bundle_id`` set) before ``posting + cap``. Unbundled = bundle_id
    is None ('' is normalized to None at the feed boundary per DS.0
    finding 5 — the Oracle ''≡NULL fork is closed upstream of the law).

    Residual = ``age − cap`` in whole seconds; violation iff > 0.
    None = no cell.
    """
    if cap_seconds is None or leg.status != "Posted" or leg.bundle_id is not None:
        return None
    age_seconds = (as_of - leg.posting) // timedelta(seconds=1)
    return age_seconds - cap_seconds


# -- CARDINALITY family (plain Python) ----------------------------------------


def _firing_legs(state: ResidualState) -> tuple[LegRow, ...]:
    """Legs that count as rail firings under the status law."""
    return tuple(leg for leg in current_legs(state) if leg.status in FIRING_STATUSES)


def chain_parent_residual(
    state: ResidualState, transfer_id: str, template_name: str,
) -> int | None:
    """Law 8 (chain parent disagreement): every firing leg of one child
    Transfer agrees on which parent firing it descends from. Residual =
    ``|distinct claimed parent ids| − 1``; zero = law holds. None = no
    cell (no firing legs of the (transfer, template) claim any parent).
    """
    parents = {
        leg.transfer_parent_id
        for leg in _firing_legs(state)
        if leg.transfer_id == transfer_id
        and leg.template_name == template_name
        and leg.transfer_parent_id is not None
    }
    if not parents:
        return None
    return len(parents) - 1


def xor_group_residual(
    state: ResidualState,
    transfer_id: str,
    template_name: str,
    member_rails: frozenset[str],
) -> int | None:
    """Law 9 (XOR group): for a declaring template, exactly ONE member
    of the group fires per Transfer. Residual = ``firing_count − 1``
    counting FIRING-status legs on member rails (per leg, not per rail —
    a double-post on one rail is an overlap).

    EXISTENCE predicate (default-and-flag, pinned by KAT C4 for the
    DS.3.3 decision): the cell exists when the transfer has ANY
    firing-status leg of the template — the consistent composition of
    the decided status law. Consequence accepted at DS.0 sign-off
    review: an all-Failed or all-unknown transfer is fully voided and
    gets NO cell (invisible, not alarmed).
    """
    template_legs = [
        leg for leg in _firing_legs(state)
        if leg.transfer_id == transfer_id and leg.template_name == template_name
    ]
    if not template_legs:
        return None
    firing_count = sum(1 for leg in template_legs if leg.rail_name in member_rails)
    return firing_count - 1


def fan_in_residual(
    state: ResidualState,
    child_transfer_id: str,
    expected_parent_count: int | None,
) -> int | None:
    """Law 10 (fan-in): a fan_in chain child's contributing parent set
    matches ``expected_parent_count`` when set, else has cardinality ≥ 2.
    Contributing parents = distinct ``transfer_parent_id`` over the
    child transfer's firing legs.

    Residual: expected set → ``count − expected`` (negative = missing,
    positive = extra); unset → ``count − 2`` floored at zero from above
    (only the degenerate <2 case is flaggable — 'orphan'). None = no
    cell (the child transfer has no firing legs at all).
    """
    legs = [leg for leg in _firing_legs(state) if leg.transfer_id == child_transfer_id]
    if not legs:
        return None
    count = len({
        leg.transfer_parent_id for leg in legs if leg.transfer_parent_id is not None
    })
    if expected_parent_count is not None:
        return count - expected_parent_count
    return count - 2 if count < 2 else 0


def multi_xor_residual(
    state: ResidualState,
    parent_transfer_id: str,
    parent_name: str,
    child_names: frozenset[str],
) -> int | None:
    """Law 11 (multi-XOR alternation): every parent firing of a
    multi-children chain (fan_in children stripped) has exactly ONE
    declared child fire under it. Residual = ``|distinct fired sibling
    NAMES| − 1`` — names, counted ONCE each, however many legs or
    posting days carry them.

    BORN-DIVERGENT until DS.3.3a lands: today's SQL multiplies the
    count by distinct posting days (a parent whose legs straddle
    midnight false-positives as 'overlap'); KAT MX3 pins the law side.

    Cell existence: the parent firing exists when any firing-status leg
    carries the parent name as its rail or template. A child fires when
    a firing-status leg has ``transfer_parent_id == parent_transfer_id``
    and its rail or template name is in the declared sibling set.
    None = no cell.
    """
    firing = _firing_legs(state)
    parent_exists = any(
        leg.transfer_id == parent_transfer_id
        and (leg.rail_name == parent_name or leg.template_name == parent_name)
        for leg in firing
    )
    if not parent_exists:
        return None
    fired_names = {
        name
        for leg in firing
        if leg.transfer_parent_id == parent_transfer_id
        for name in (leg.rail_name, leg.template_name)
        if name is not None and name in child_names
    }
    return len(fired_names) - 1


# -- DERIVATION family ---------------------------------------------------------


def expected_trail_edges(state: ResidualState) -> frozenset[TrailEdge]:
    """Law 13 (money trail): for every transfer reachable from a root
    (``transfer_parent_id is None``), one edge per (src leg: amount < 0,
    Posted) × (tgt leg: amount > 0, Posted) pair, labeled with the
    root's transfer_id and the member's depth.

    Cycle semantics (DS.3.1, landed): a cycle made ROOT-REACHABLE by a
    multi-parent row walks to MONEY_TRAIL_DEPTH_CAP and the refresh
    script's tripwire fails LOUDLY. A pure cycle unreachable from any
    root never enters the walk — silently omitted here and in the
    detector alike (KAT D3 pins it); that residual class is a candidate
    data-quality invariant in the DS backlog.
    """
    legs = current_legs(state)
    parent_of: dict[str, str | None] = {}
    for leg in legs:
        parent_of.setdefault(leg.transfer_id, leg.transfer_parent_id)
    children: dict[str, list[str]] = {}
    roots: list[str] = []
    for tid, parent in parent_of.items():
        if parent is None or parent not in parent_of:
            # A dangling parent reference (parent id absent from the
            # feed) never anchors to a root; the transfer is dropped —
            # matching current drop-semantics, revisited with DS.3.1.
            if parent is None:
                roots.append(tid)
            continue
        children.setdefault(parent, []).append(tid)
    edges: set[TrailEdge] = set()
    for root in roots:
        frontier: list[tuple[str, int]] = [(root, 0)]
        seen: set[str] = set()
        while frontier:
            tid, depth = frontier.pop()
            if tid in seen:
                continue
            seen.add(tid)
            member_legs = [leg for leg in legs if leg.transfer_id == tid]
            srcs = [leg for leg in member_legs if leg.status in MONEY_STATUSES and leg.amount < ZERO]
            tgts = [leg for leg in member_legs if leg.status in MONEY_STATUSES and leg.amount > ZERO]
            for s in srcs:
                for t in tgts:
                    edges.add(TrailEdge(root, s.account_id, t.account_id, depth))
            for child in children.get(tid, ()):
                frontier.append((child, depth + 1))
    return frozenset(edges)


def money_trail_residual(
    state: ResidualState, emitted: frozenset[TrailEdge],
) -> frozenset[TrailEdge]:
    """Derivation residual: the symmetric difference between the edges
    the law derives and the edges the detector emitted. Empty = law
    holds."""
    return expected_trail_edges(state) ^ emitted
