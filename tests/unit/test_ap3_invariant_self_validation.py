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
    row = {c: vals.get(c) for c in _TX_COLS}
    placeholders = ", ".join("?" for _ in _TX_COLS)
    conn.execute(
        f"INSERT INTO {_PREFIX}_transactions ({', '.join(_TX_COLS)}) "
        f"VALUES ({placeholders})",
        [row[c] for c in _TX_COLS],
    )


def _insert_balance(conn: sqlite3.Connection, **vals: object) -> None:
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
        return {
            Violation.of("drift", account_id=aid, drift=round(float(d), 2))
            for aid, d in rows
        }


@dataclass
class DriftGenerator:
    """Emit a leaf internal account with a stored balance that does NOT
    equal Σ of its posted legs. drift = stored − computed.

    FINDING: the focused row-set is small (1 balance + 1 leg) but it must
    satisfy the detector's structural preconditions, which are NOT
    obvious from the violation alone — account_scope='internal' AND
    account_parent_role IS NOT NULL (leaf), and the leg's posting must
    fall within the balance's [start, end). A ViolationGenerator has to
    carry those preconditions; they're exactly what a developer forgets
    today (silent-empty matview).
    """

    account_id: str = "acct-drift"
    stored: float = 105.0
    posted: float = 100.0  # clean ⇒ stored == posted

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
            account_role="customer_dda", account_scope="internal",
            account_parent_role="dda_pool", business_day_start=start,
            business_day_end=end, money=self.stored,
        )
        _insert_tx(conn, **_credit_leg(
            id="tx-drift-1", account_id=self.account_id,
            account_name="Drift Acct", account_role="customer_dda",
            account_scope="internal", account_parent_role="dda_pool",
            amount_money=self.posted, posting=_ts(_DAY),
            transfer_id="xfer-drift-1", rail_name="ach", origin="etl",
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
    _assert_self_validates(
        DriftInvariant(),
        dirty=DriftGenerator(stored=105.0, posted=100.0),
        clean=DriftGenerator(stored=100.0, posted=100.0),
    )


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
