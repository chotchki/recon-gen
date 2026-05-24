"""AP.3 spike — can an invariant validate a violation?

The make-or-break probe for the invariant-spine destination
(`docs/audits/date_range_model_audit.md` §5–§6). The spine's whole
value rests on one claim:

    detect(ViolationGenerator[T].emit()) ⊇ {intended Violation[T]}
    detect(clean baseline)             ⊉ {intended Violation[T]}

i.e. an Invariant can confirm a candidate *is* a Violation (and reject
clean data) by re-using its OWN detector — no DB server, in-process,
across complexity classes. If that holds, two contingent payoffs land:
byte-locked seed SQL can retire (semantic correctness becomes a direct
check, stronger than byte-identity) and training/docs scenarios become
self-validated (they can't silently fail to demonstrate / can't lie).

WHAT THIS SPIKE PROVES (and what it deliberately does NOT):

- The detector under test is the REAL emitted matview SQL — produced by
  ``emit_schema`` + ``refresh_matviews_sql`` for the SQLite dialect, the
  exact same definition production reads through QS / App2 / PDF. There
  is no re-encoded copy of the detection logic here. ``detect()`` is a
  thin read of the matview's output. ONE detector definition, used for
  both detection and self-validation — that is the spine's core bet.

- The generator is FOCUSED: it emits only the base-table rows needed to
  manifest one violation (plus, for the statistical invariant, a
  baseline population). This is the ``ViolationGenerator[T]`` the spine
  proposes — contrast today's monolithic ``build_full_seed_sql``.

- Three complexity classes, because the spine has to generalize past
  trivial arithmetic to be worth building:
    * arithmetic  — ``drift`` (stored balance vs Σ posted legs)
    * windowed    — ``inv_pair_rolling_anomalies`` (rolling-2-day z-score)
    * recursive   — ``inv_money_trail_edges`` (WITH RECURSIVE chain walk)

- The spine vocab (``Violation`` / ``Invariant`` / ``ViolationGenerator``)
  lives HERE, local to the spike — NOT promoted to ``src/``. A spike
  must not pre-commit the production API; the rollout (post-spike,
  per the audit) decides the real home + shape. This module only has to
  answer "does the round-trip hold, and does Python express it cleanly?"

FINDINGS surfaced by writing it are recorded inline at each generator.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Protocol

import pytest

from recon_gen.common.db import _register_sqlite_aggregates, execute_script
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.sql import Dialect

# spec_example is the canonical structural fixture; for these three
# invariants the emitted detector SQL is persona-blind (the L1 invariant
# from CLAUDE.md) — it never joins to L2 config, so the generator can use
# arbitrary account_id / rail_name strings. limit_breach (instance-coupled
# inline cap CASE) is out of scope precisely because it isn't persona-blind.
_SPEC_EXAMPLE = Path(__file__).resolve().parents[1] / "l2" / "spec_example.yaml"
_PREFIX = "spec_example"
_DIALECT = Dialect.SQLITE


# ---------------------------------------------------------------------------
# The spine vocab — LOCAL to this spike (see module docstring).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Violation:
    """A detected invariant breach, keyed by STABLE identity — the
    analyst-facing columns that name the violation (account + magnitude,
    a pair + sigma band, a chain edge at depth), never auto-derived row
    internals. Identity is a frozenset of (column, value) so two
    Violations compare equal iff they name the same breach.
    """

    invariant: str
    identity: frozenset[tuple[str, object]]

    @classmethod
    def of(cls, invariant: str, **identity: object) -> "Violation":
        return cls(invariant=invariant, identity=frozenset(identity.items()))


class Invariant(Protocol):
    """A detector. ``name`` is the emitted matview suffix; ``detect``
    reads that matview's OUTPUT and projects each row to a Violation.
    The detection logic is the matview body (emitted SQL) — detect() does
    not re-implement it.
    """

    name: str

    def detect(self, conn: sqlite3.Connection) -> set[Violation]: ...


class ViolationGenerator(Protocol):
    """A producer of base-table rows intended to manifest ``intended``.
    ``emit`` inserts only the rows needed (focused, not the full seed).
    Producer ≠ thing-produced: the generator is not the Violation, it
    claims to cause one.
    """

    @property
    def intended(self) -> Violation: ...

    def emit(self, conn: sqlite3.Connection) -> None: ...


# ---------------------------------------------------------------------------
# In-process harness — real emitted schema + refresh, no DB server.
# ---------------------------------------------------------------------------


def _fresh_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")
    _register_sqlite_aggregates(conn)  # STDDEV_SAMP for the windowed matview
    instance = load_instance(_SPEC_EXAMPLE)
    cur = conn.cursor()
    execute_script(
        cur, emit_schema(instance, prefix=_PREFIX, dialect=_DIALECT),
        dialect=_DIALECT,
    )
    conn.commit()
    # AW.2 + AW.3 + AW.4 bridge: matviews read from `<prefix>_config`
    # via subquery / LEFT JOIN. Seed the config row with as_of + L2
    # JSON carrying rails and limit_schedules. AP.3's stuck_pending,
    # stuck_unbundled, and limit_breach scenarios all need these
    # populated. AW.5 will eventually replace with a real
    # L2-instance-to-JSON serializer.
    import json
    from datetime import datetime
    from recon_gen.common.l2.config_table import replace_config
    l2_for_config = json.dumps({
        "rails": [
            {"name": "ExternalRailInbound", "max_pending_age_seconds": 86400},
            {"name": "SubledgerCharge", "max_unbundled_age_seconds": 14400},
        ],
        "limit_schedules": [
            {
                "parent_role": "CustomerLedger",
                "rail": "ExternalRailOutbound",
                "direction": "Outbound",
                "cap": 5000,
            },
            {
                "parent_role": "CustomerLedger",
                "rail": "ExternalRailInbound",
                "direction": "Inbound",
                "cap": 3000,
            },
        ],
    })
    replace_config(
        conn, prefix=_PREFIX,
        cfg_json="{}", l2_json=l2_for_config,
        as_of=datetime.now(),  # typing-smell: ignore[no-datetime-now]: bridge test harness — AW.5 retrofits to pinned LOCKED_ANCHOR
    )
    return conn


def _refresh(conn: sqlite3.Connection) -> None:
    instance = load_instance(_SPEC_EXAMPLE)
    cur = conn.cursor()
    execute_script(
        cur, refresh_matviews_sql(instance, prefix=_PREFIX, dialect=_DIALECT),
        dialect=_DIALECT,
    )
    conn.commit()


_TX_COLS = (
    "id", "account_id", "account_name", "account_role", "account_scope",
    "account_parent_role", "amount_money", "amount_direction", "status",
    "posting", "transfer_id", "transfer_parent_id", "rail_name", "origin",
)
_DB_COLS = (
    "account_id", "account_name", "account_role", "account_scope",
    "account_parent_role", "expected_eod_balance", "business_day_start",
    "business_day_end", "money",
)


def _insert_tx(conn: sqlite3.Connection, **vals: object) -> None:
    # AO.1 — amount_money is BIGINT cents; coerce author-side floats
    # at the write boundary so this local helper mirrors `insert_tx`.
    from recon_gen.common.money import Cents
    raw = vals.get("amount_money")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        vals["amount_money"] = int(Cents.from_dollars(raw))
    row = {c: vals.get(c) for c in _TX_COLS}
    placeholders = ", ".join("?" for _ in _TX_COLS)
    conn.execute(
        f"INSERT INTO {_PREFIX}_transactions ({', '.join(_TX_COLS)}) "
        f"VALUES ({placeholders})",
        [row[c] for c in _TX_COLS],
    )


def _insert_balance(conn: sqlite3.Connection, **vals: object) -> None:
    # AO.1 — money / expected_eod_balance are BIGINT cents; coerce.
    from recon_gen.common.money import Cents
    for col in ("money", "expected_eod_balance"):
        raw = vals.get(col)
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            vals[col] = int(Cents.from_dollars(raw))
    row = {c: vals.get(c) for c in _DB_COLS}
    placeholders = ", ".join("?" for _ in _DB_COLS)
    conn.execute(
        f"INSERT INTO {_PREFIX}_daily_balances ({', '.join(_DB_COLS)}) "
        f"VALUES ({placeholders})",
        [row[c] for c in _DB_COLS],
    )


def _credit_leg(**kw: object) -> dict[str, object]:
    return {"amount_direction": "Credit", "status": "Posted", **kw}


def _debit_leg(**kw: object) -> dict[str, object]:
    return {"amount_direction": "Debit", "status": "Posted", **kw}


_DAY = date(2030, 3, 15)


def _ts(day: date, hour: int = 12) -> str:
    return datetime(day.year, day.month, day.day, hour).strftime(
        "%Y-%m-%d %H:%M:%S",
    )


def _day_bounds(day: date) -> tuple[str, str]:
    start = datetime(day.year, day.month, day.day, 0, 0, 0)
    return (
        start.strftime("%Y-%m-%d %H:%M:%S"),
        (start + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
    )


# ---------------------------------------------------------------------------
# 1. ARITHMETIC — drift. Stored balance vs Σ posted legs (≤ business_day_end).
# ---------------------------------------------------------------------------


class DriftInvariant:
    name = "drift"

    def detect(self, conn: sqlite3.Connection) -> set[Violation]:
        rows = conn.execute(
            f"SELECT account_id, drift FROM {_PREFIX}_drift",
        ).fetchall()
        # AO.1 — drift is BIGINT cents; convert at read.
        from recon_gen.common.money import Cents
        return {
            Violation.of(
                "drift", account_id=aid,
                drift=round(float(Cents.from_db(int(d)).to_dollars()), 2),
            )
            for aid, d in rows
        }

    @staticmethod
    def scenario_for(
        account_role: str, *, drift_amount: float = 5.0,
    ) -> "DriftGenerator":
        """\"Take an L2 and say: I need a drift scenario for this
        account_role.\" The request is in SHAPE VOCABULARY (a role name);
        the invariant resolves it to a concrete leaf internal account that
        can actually exhibit drift, against the L2.

        This is the generator side of the spine living ON the invariant:
        the invariant knows both how to DETECT itself (``detect``) and how
        to MANUFACTURE a violation of itself (``scenario_for``). Resolution
        fails loud if the role isn't in the shape, or isn't drift-eligible
        (drift needs a leaf — account_parent_role IS NOT NULL).
        """
        instance = load_instance(_SPEC_EXAMPLE)
        candidates = [
            a for a in instance.accounts
            if getattr(a, "role", None) == account_role
            and getattr(a, "scope", None) == "internal"
            and getattr(a, "parent_role", None) is not None
        ]
        if not candidates:
            raise ValueError(
                f"shape has no drift-eligible (leaf internal) account with "
                f"role {account_role!r} — cannot manufacture a drift scenario",
            )
        acct = candidates[0]
        return DriftGenerator(
            account_id=f"acct-drift-{account_role}",
            account_role=account_role,
            parent_role=str(getattr(acct, "parent_role")),
            stored=100.0 + drift_amount, posted=100.0,
        )


@dataclass
class DriftGenerator:
    """Emit a leaf internal account with a stored balance that does NOT
    equal Σ of its posted legs. drift = stored − computed.

    Built via ``DriftInvariant.scenario_for(role)`` — account_role +
    parent_role come FROM THE SHAPE (the role is what the caller asked for;
    the parent_role is resolved from the L2's account of that role).

    FINDING: the focused row-set is small (1 balance + 1 leg) but it must
    satisfy the detector's structural preconditions, which are NOT
    obvious from the violation alone — account_scope='internal' AND
    account_parent_role IS NOT NULL (leaf), and the leg's posting must
    fall within the balance's [start, end). A ViolationGenerator has to
    carry those preconditions; they're exactly what a developer forgets
    today (silent-empty matview).
    """

    account_id: str
    account_role: str
    parent_role: str
    stored: float
    posted: float  # clean ⇒ stored == posted

    @property
    def intended(self) -> Violation:
        return Violation.of(
            "drift", account_id=self.account_id,
            drift=round(self.stored - self.posted, 2),
        )

    def emit(self, conn: sqlite3.Connection) -> None:
        start, end = _day_bounds(_DAY)
        _insert_balance(
            conn, account_id=self.account_id, account_name="Drift Acct",
            account_role=self.account_role, account_scope="internal",
            account_parent_role=self.parent_role, business_day_start=start,
            business_day_end=end, money=self.stored,
        )
        _insert_tx(conn, **_credit_leg(
            id=f"tx-drift-{self.account_role}", account_id=self.account_id,
            account_name="Drift Acct", account_role=self.account_role,
            account_scope="internal", account_parent_role=self.parent_role,
            amount_money=self.posted, posting=_ts(_DAY),
            transfer_id=f"xfer-drift-{self.account_role}", rail_name="ach",
            origin="etl",
        ))


# ---------------------------------------------------------------------------
# 2. WINDOWED — inv_pair_rolling_anomalies. Rolling-2-day z-score per pair.
# ---------------------------------------------------------------------------


class AnomalyInvariant:
    name = "inv_pair_rolling_anomalies"

    def detect(self, conn: sqlite3.Connection) -> set[Violation]:
        # A "violation" here is a pair-window that crosses 3σ. The matview
        # computes the z-score; detect() applies the analyst threshold and
        # names the pair. (The threshold is a VIEW concern in the spine —
        # the matview emits z; the view decides the band. The spike folds
        # them so the round-trip is one assertion.)
        rows = conn.execute(
            f"SELECT sender_account_id, recipient_account_id, z_score "
            f"FROM {_PREFIX}_inv_pair_rolling_anomalies "
            f"WHERE ABS(z_score) >= 3",
        ).fetchall()
        return {
            Violation.of(
                "inv_pair_rolling_anomalies",
                sender_account_id=s, recipient_account_id=r,
            )
            for s, r, _z in rows
        }


@dataclass
class AnomalyGenerator:
    """Emit a baseline population of quiet pair-days plus one spike pair.

    FINDING (the load-bearing one for windowed invariants): a focused
    generator for a STATISTICAL invariant cannot be a single anomalous
    row. The z-score is computed across the whole population, and a single
    outlier among n points has a hard z ceiling of ≈√n. To land a stable
    ≥3σ flag the generator MUST also emit a baseline population (here 20
    quiet single-day pairs). So ViolationGenerator[windowed] is
    intrinsically (baseline + spike), not (spike). The clean counterpart
    is the SAME topology with the spike's magnitude normalized — the
    violation is purely the magnitude, which mirrors the real semantic.
    """

    spike_sender: str = "acct-spike-src"
    spike_recipient: str = "acct-spike-dst"
    n_baseline: int = 20
    quiet_amount: float = 10.0
    spike_amount: float = 5000.0  # clean ⇒ quiet_amount

    @property
    def intended(self) -> Violation:
        return Violation.of(
            "inv_pair_rolling_anomalies",
            sender_account_id=self.spike_sender,
            recipient_account_id=self.spike_recipient,
        )

    def _emit_pair(
        self, conn: sqlite3.Connection, *, idx: int,
        sender: str, recipient: str, amount: float, day: date,
    ) -> None:
        xfer = f"xfer-anom-{idx}"
        # Recipient leg must be internal + leaf (detector's WHERE).
        _insert_tx(conn, **_credit_leg(
            id=f"tx-anom-r-{idx}", account_id=recipient,
            account_name=recipient, account_role="customer_dda",
            account_scope="internal", account_parent_role="dda_pool",
            amount_money=amount, posting=_ts(day),
            transfer_id=xfer, rail_name="ach", origin="etl",
        ))
        # Sender leg (amount < 0), same transfer_id.
        _insert_tx(conn, **_debit_leg(
            id=f"tx-anom-s-{idx}", account_id=sender,
            account_name=sender, account_role="counterparty",
            account_scope="external", amount_money=-amount,
            posting=_ts(day), transfer_id=xfer, rail_name="ach", origin="etl",
        ))

    def emit(self, conn: sqlite3.Connection) -> None:
        for i in range(self.n_baseline):
            self._emit_pair(
                conn, idx=i, sender=f"acct-quiet-src-{i}",
                recipient=f"acct-quiet-dst-{i}", amount=self.quiet_amount,
                day=_DAY,
            )
        self._emit_pair(
            conn, idx=999, sender=self.spike_sender,
            recipient=self.spike_recipient, amount=self.spike_amount, day=_DAY,
        )


# ---------------------------------------------------------------------------
# 3. RECURSIVE — inv_money_trail_edges. WITH RECURSIVE parent-chain walk.
# ---------------------------------------------------------------------------


class MoneyTrailInvariant:
    name = "inv_money_trail_edges"

    def detect(self, conn: sqlite3.Connection) -> set[Violation]:
        # Violation = an edge at depth ≥ 2 (a multi-hop money trail). The
        # recursive walk assigns depth; detect() names deep edges by their
        # (root, transfer, depth) identity.
        rows = conn.execute(
            f"SELECT root_transfer_id, transfer_id, depth "
            f"FROM {_PREFIX}_inv_money_trail_edges WHERE depth >= 2",
        ).fetchall()
        return {
            Violation.of(
                "inv_money_trail_edges",
                root_transfer_id=root, transfer_id=tid, depth=depth,
            )
            for root, tid, depth in rows
        }


@dataclass
class MoneyTrailGenerator:
    """Emit a parent-linked transfer chain root → child → grandchild,
    each a 2-leg (debit+credit) Posted transfer. The grandchild edge is
    at depth 2 → the intended deep-trail violation. The clean counterpart
    is a single root transfer (depth 0 only).

    FINDING: the recursive detector needs each chain member to be a
    *complete* 2-leg transfer (src amount<0 AND tgt amount>0, both Posted)
    or the edge silently drops from the trail even though the chain
    ancestry exists. A ViolationGenerator[recursive] therefore carries
    leg-completeness as a precondition of the depth it claims.
    """

    depth: int = 2  # clean ⇒ 0

    @property
    def intended(self) -> Violation:
        return Violation.of(
            "inv_money_trail_edges",
            root_transfer_id="xfer-trail-0",
            transfer_id=f"xfer-trail-{self.depth}", depth=self.depth,
        )

    def emit(self, conn: sqlite3.Connection) -> None:
        for hop in range(self.depth + 1):
            xfer = f"xfer-trail-{hop}"
            parent = f"xfer-trail-{hop - 1}" if hop > 0 else None
            _insert_tx(conn, **_debit_leg(
                id=f"tx-trail-s-{hop}", account_id=f"acct-trail-src-{hop}",
                account_name="src", account_role="counterparty",
                account_scope="external", amount_money=-100.0,
                posting=_ts(_DAY), transfer_id=xfer,
                transfer_parent_id=parent, rail_name="ach", origin="etl",
            ))
            _insert_tx(conn, **_credit_leg(
                id=f"tx-trail-t-{hop}", account_id=f"acct-trail-dst-{hop}",
                account_name="dst", account_role="customer_dda",
                account_scope="internal", account_parent_role="dda_pool",
                amount_money=100.0, posting=_ts(_DAY), transfer_id=xfer,
                transfer_parent_id=parent, rail_name="ach", origin="etl",
            ))


# ---------------------------------------------------------------------------
# 4. INSTANCE-COUPLED — limit_breach. The disproof of the "blind generator".
#
# This is the case that forced the finding (the other three got away with
# made-up role/rail strings because their detectors are persona-blind —
# structural/arithmetic only). limit_breach's CAP comes FROM THE SHAPE: the
# matview inlines a CASE keyed on the L2's LimitSchedules
# (parent_role, rail, direction) -> cap. A generator that emits a made-up
# (parent_role, rail) gets a NULL cap and trips NOTHING. So this generator
# CANNOT be authored blind — it must read the L2 instance to (a) SELECT a
# (parent_role, rail) that actually has a declared cap, and (b) pick a
# magnitude RELATIVE to that cap. Hence ``from_instance`` (a smart
# constructor): there is no ``LimitBreachGenerator()`` — only one resolved
# against a shape, which fails loud if the shape declares no caps.
# ---------------------------------------------------------------------------


class LimitBreachInvariant:
    name = "limit_breach"

    def detect(self, conn: sqlite3.Connection) -> set[Violation]:
        rows = conn.execute(
            f"SELECT account_id, rail_name, direction "
            f"FROM {_PREFIX}_limit_breach",
        ).fetchall()
        return {
            Violation.of(
                "limit_breach", account_id=aid, rail_name=rail,
                direction=direction,
            )
            for aid, rail, direction in rows
        }


@dataclass
class LimitBreachGenerator:
    """Plant a per-rail outbound flow that exceeds the shape's declared cap.

    The magnitude is expressed RELATIVE to the shape-derived cap
    (``cap + delta``), not as an absolute number — so the same generator
    plants a valid breach against ANY L2 that declares an Outbound cap,
    and survives a re-skin / a fuzzed topology unchanged. That portability
    is the contingent fuzzer payoff: shape-parameterized generators × a
    fuzzed shape stream = valid planted violations in arbitrary topologies.
    """

    account_id: str
    parent_role: str
    rail_name: str
    direction: str
    cap: float
    delta: float = 1.0  # clean ⇒ negative delta (under the cap)

    @classmethod
    def from_instance(cls, *, delta: float) -> "LimitBreachGenerator":
        """SELECT the first Outbound LimitSchedule from the shape. No shape
        ⇒ no generator — the precondition the 'blind' framing missed.
        """
        gens = cls.from_instance_all(delta=delta)
        outbound = [g for g in gens if g.direction == "Outbound"]
        if not outbound:
            raise ValueError(
                "shape declares no Outbound LimitSchedule — a limit_breach "
                "generator is not constructible against it",
            )
        return outbound[0]

    @classmethod
    def from_instance_all(cls, *, delta: float) -> list["LimitBreachGenerator"]:
        """Fan out: ONE generator per declared LimitSchedule in the shape.
        This is the 'across many roles/rails' shape — the selection IS a
        query over the shape's structure, not a hand-listed set.
        """
        instance = load_instance(_SPEC_EXAMPLE)
        return [
            cls(
                account_id=f"acct-limit-{ls.direction}-{ls.rail}",
                parent_role=ls.parent_role, rail_name=ls.rail,
                direction=ls.direction, cap=float(ls.cap), delta=delta,
            )
            for ls in instance.limit_schedules
        ]

    @property
    def amount(self) -> float:
        return self.cap + self.delta

    @property
    def intended(self) -> Violation:
        return Violation.of(
            "limit_breach", account_id=self.account_id,
            rail_name=self.rail_name, direction=self.direction,
        )

    def emit(self, conn: sqlite3.Connection) -> None:
        # Outbound breach = Debit legs (matview's Debit branch); Inbound =
        # Credit legs. |amount| crosses (or, delta<0, stays under) the cap.
        # account_parent_role + rail_name MUST be the shape's declared pair
        # or the matview's cap CASE yields NULL.
        leg = _debit_leg if self.direction == "Outbound" else _credit_leg
        signed = -self.amount if self.direction == "Outbound" else self.amount
        _insert_tx(conn, **leg(
            id=f"tx-limit-{self.direction}-{self.rail_name}",
            account_id=self.account_id, account_name="Limit Acct",
            account_role="CustomerSubledger", account_scope="internal",
            account_parent_role=self.parent_role, amount_money=signed,
            posting=_ts(_DAY),
            transfer_id=f"xfer-limit-{self.direction}-{self.rail_name}",
            rail_name=self.rail_name, origin="etl",
        ))


# ---------------------------------------------------------------------------
# The self-validation link.
# ---------------------------------------------------------------------------


def _assert_self_validates(
    invariant: Invariant,
    dirty: ViolationGenerator,
    clean: ViolationGenerator,
) -> None:
    """The make-or-break assertion, both directions:

        detect(dirty.emit())  ⊇ {dirty.intended}     (confirms)
        detect(clean.emit())  ⊉ {dirty.intended}     (rejects clean)

    Each direction runs against a FRESH in-memory DB with the REAL
    emitted schema + refresh — the detector is never re-implemented here.
    """
    dirty_conn = _fresh_db()
    try:
        dirty.emit(dirty_conn)
        dirty_conn.commit()
        _refresh(dirty_conn)
        detected = invariant.detect(dirty_conn)
        assert dirty.intended in detected, (
            f"{invariant.name}: detector did NOT confirm the intended "
            f"violation.\n  intended: {dirty.intended}\n  detected: {detected}"
        )
    finally:
        dirty_conn.close()

    clean_conn = _fresh_db()
    try:
        clean.emit(clean_conn)
        clean_conn.commit()
        _refresh(clean_conn)
        detected = invariant.detect(clean_conn)
        assert dirty.intended not in detected, (
            f"{invariant.name}: detector flagged a violation on CLEAN data.\n"
            f"  not-expected: {dirty.intended}\n  detected: {detected}"
        )
    finally:
        clean_conn.close()


def test_arithmetic_invariant_self_validates() -> None:
    # dirty/clean both built via scenario_for(role) — the role is resolved
    # against the shape; clean = the same account with zero drift.
    _assert_self_validates(
        DriftInvariant(),
        dirty=DriftInvariant.scenario_for("CustomerSubledger", drift_amount=5.0),
        clean=DriftInvariant.scenario_for("CustomerSubledger", drift_amount=0.0),
    )


def test_drift_scenario_for_resolves_the_role_against_the_shape() -> None:
    # "I need a drift scenario for this account_role" — the parent_role is
    # NOT supplied by the caller; the invariant resolves it from the L2's
    # account of that role. Shape vocabulary in, concrete coordinates out.
    gen = DriftInvariant.scenario_for("CustomerSubledger")
    assert gen.parent_role == "CustomerLedger"


def test_drift_scenario_for_unknown_role_fails_loud() -> None:
    # A role not in the shape (or not drift-eligible) can't manufacture a
    # scenario — fail at the request, not silently emit inert rows.
    with pytest.raises(ValueError, match="no drift-eligible"):
        DriftInvariant.scenario_for("NoSuchRole")


def test_windowed_invariant_self_validates() -> None:
    _assert_self_validates(
        AnomalyInvariant(),
        dirty=AnomalyGenerator(spike_amount=5000.0),
        clean=AnomalyGenerator(spike_amount=10.0),  # spike normalized
    )


def test_recursive_invariant_self_validates() -> None:
    _assert_self_validates(
        MoneyTrailInvariant(),
        dirty=MoneyTrailGenerator(depth=2),
        clean=MoneyTrailGenerator(depth=0),
    )


def test_instance_coupled_invariant_self_validates() -> None:
    # The generator is resolved AGAINST THE SHAPE (cap pulled from the L2's
    # LimitSchedule); magnitude is relative to that cap. dirty = cap + $1,
    # clean = cap − $1. Disproves the "blind local generator".
    _assert_self_validates(
        LimitBreachInvariant(),
        dirty=LimitBreachGenerator.from_instance(delta=1.0),
        clean=LimitBreachGenerator.from_instance(delta=-1.0),
    )


def test_scenario_composes_many_generators_across_the_shape() -> None:
    # "The input may be the scenario." A scenario is a composition of
    # (invariant, generator) requests against ONE shape — here drift on a
    # role + limit_breach FANNED across every declared LimitSchedule (2 in
    # spec_example: outbound + inbound). All apply to one DB, one refresh,
    # and every intended violation is detected together.
    #
    # NOTE (a finding for AP.2): generators co-mingle in the shared
    # matviews after one refresh. For Local invariants (drift, limit_breach)
    # that's fine — they're per-account/per-group. For a Populational
    # invariant the population would now include every other generator's
    # legs, so its z-statistics depend on the rest of the scenario. The
    # baseline a windowed generator perturbs is therefore the WHOLE
    # scenario, not its private fixture — confirms finding #4's stream model.
    pairs: list[tuple[Invariant, ViolationGenerator]] = [
        (DriftInvariant(), DriftInvariant.scenario_for("CustomerSubledger")),
    ]
    pairs += [
        (LimitBreachInvariant(), g)
        for g in LimitBreachGenerator.from_instance_all(delta=1.0)
    ]
    assert len(pairs) == 3, "expected drift + 2 fanned-out limit breaches"

    conn = _fresh_db()
    try:
        for _inv, gen in pairs:
            gen.emit(conn)
        conn.commit()
        _refresh(conn)
        for inv, gen in pairs:
            detected = inv.detect(conn)
            assert gen.intended in detected, (
                f"scenario: {inv.name} did not confirm {gen.intended}\n"
                f"  detected: {detected}"
            )
    finally:
        conn.close()


def test_limit_breach_generator_is_not_constructible_without_a_shape() -> None:
    # The signature isn't () -> rows: the cap (selector + threshold) comes
    # from the shape. A generator with a made-up (parent_role, rail) plants
    # rows that insert fine but trip NOTHING — the matview's cap CASE yields
    # NULL, so outbound_total > cap is never true. Proven directly: emit a
    # breach-magnitude leg on a bogus pair, refresh, detect ∅.
    conn = _fresh_db()
    try:
        bogus = LimitBreachGenerator(
            account_id="acct-bogus", parent_role="NoSuchRole",
            rail_name="NoSuchRail", direction="Outbound", cap=5000.0,
            delta=1_000_000.0,
        )
        bogus.emit(conn)
        conn.commit()
        _refresh(conn)
        assert LimitBreachInvariant().detect(conn) == set(), (
            "a made-up (parent_role, rail) tripped limit_breach — it must "
            "not: the cap is shape-derived, so an off-shape generator is inert"
        )
    finally:
        conn.close()
