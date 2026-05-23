"""AT.0 spike — pair-rolling anomaly through the spine, end-to-end.

Parallel to AS.0 (drift) and AU.0 (overdraft), but pilots the
**windowed-statistical** complexity class — the first AT-phase
deliverable.

The spine vocab (`Violation` / `Invariant` / `ViolationGenerator`)
already exists in src/recon_gen/common/spine/ from AS.1. The spike
uses it directly (matching AU.0's pattern: pilot lives in tests/,
promotion lives in src/). Local `AnomalyInvariant` +
`AnomalyGenerator` are the AT.1 promotion-shape proposal verbatim.

What's STRUCTURALLY DISTINCT from L1 (AP.3 finding #2):

1. **Statistical, not single-row.** The matview's z-score is `(window_
   sum − pop_mean) / pop_stddev` — a z-score needs a population.
   Single-transfer plant has pop_stddev=0 → matview defaults the bucket
   to '0-1 sigma' (the empty/degenerate case). To make anomaly FIRE
   high-sigma, the generator MUST plant a baseline (normal-volume
   pairs) + a spike (the target pair, far above the baseline mean).
2. **Windowed.** The matview's rolling 2-day SUM per pair means a
   spike on day N either:
   - shows as a 1-day window (if there's no day-N-1 activity for the
     same pair), or
   - aggregates with day-N-1 (if there IS prior activity for the same
     pair).
   The plant places the spike on day N with no day-N-1 history for
   the target pair to get a clean spike-only window.
3. **Threshold owned by the View (AP.3 finding #3).** The matview
   returns rows for every pair-window with their bucket annotation
   ('0-1 sigma' ... '4+ sigma'). What counts as a "violation" depends
   on the threshold. The View owns the slice; for this spike, detect()
   bakes in a default of >= 3σ to match the L1 invariants' "the
   detector returns the set of breaches" contract. AT.1's View
   integration moves the threshold to a knob.

Spike scope (what this proves):

- The promoted spine vocab handles windowed-statistical (no Protocol
  change needed).
- `scenario_for(sender_role, recipient_role)` is the natural shape
  selector — sender + recipient are both account-role-typed in the L2.
- Baseline-plus-spike is a NEW emission shape (multi-row from one
  generator call). The Protocol stays minimal — emit() takes a
  connection and writes whatever rows it needs.
- Empirical edge check still applies: an anomaly plant writes Posted
  transactions, which COULD fire L1 invariants (drift/overdraft) IF
  we also planted balance rows. We don't, so the plant is single-edge
  to anomaly. The cross-class composition (anomaly + drift in one
  scenario) is AT.2.b territory (or AT.2's analogue of AU.2).

Spike scope (what this does NOT prove):

- Promotion to src/ (AT.1 work — the spike vocab is the proposal).
- View integration / threshold knob (AT.1 + AT.2's responsibility).
- Recursive-graph case (AT.3 — money_trail's WITH RECURSIVE).
- 4-way agreement extension to Investigation (AT.5 MANDATORY GATE).

The AT.0 audit subsection (to land in
`docs/audits/date_range_model_audit.md` §5 "AT.0 result") captures
the lessons + locks AT.1-AT.6's migration order.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pytest

from recon_gen.common.as_of_frame import LOCKED_ANCHOR, AsOfFrame
from recon_gen.common.db import _register_sqlite_aggregates, execute_script
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.primitives import L2Instance
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.spine import (
    Invariant,
    Violation,
    ViolationGenerator,
)
from recon_gen.common.sql import Dialect
from recon_gen.common.tree import DateView

_SPEC_EXAMPLE = Path(__file__).resolve().parents[1] / "l2" / "spec_example.yaml"
_PREFIX = "spec_example"
_DIALECT = Dialect.SQLITE


# ---------------------------------------------------------------------------
# AT.0 spike vocab — local concrete spine impls (AT.1 promotes verbatim).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnomalyInvariant:
    """Pair-rolling-anomaly detector. Reads
    ``<prefix>_inv_pair_rolling_anomalies`` and projects rows where
    ``z_bucket`` ∈ {'3-4 sigma', '4+ sigma'} into Violations.

    The 3σ threshold is baked in for the spike — AT.1 + AT.2 hand
    threshold ownership to the View per AP.3 finding #3. Detector
    interface stays as `detect(conn) -> set[Violation]` to keep the
    L1 spine link contract; the View slices over what detect returns
    AND additionally filters on threshold parameters.
    """

    name: str = "inv_pair_rolling_anomalies"
    prefix: str = _PREFIX

    def detect(self, conn: sqlite3.Connection) -> set[Violation]:
        rows = conn.execute(
            f"SELECT sender_account_id, recipient_account_id, window_end, "
            f"z_bucket "
            f"FROM {self.prefix}_inv_pair_rolling_anomalies "
            f"WHERE z_bucket IN ('3-4 sigma', '4+ sigma')",
        ).fetchall()
        return {
            Violation.of(
                "inv_pair_rolling_anomalies",
                sender_account_id=str(said),
                recipient_account_id=str(raid),
                window_end=_to_date(we),
                z_bucket=str(zb),
            )
            for said, raid, we, zb in rows
        }

    def scenario_for(
        self,
        sender_role: str,
        recipient_role: str,
        *,
        spike_magnitude: float = 100_000.0,
        baseline_pair_count: int = 100,
        baseline_amount: float = 100.0,
        instance: L2Instance | None = None,
    ) -> "AnomalyGenerator":
        """Resolve sender + recipient roles against the L2; return a
        generator that plants `baseline_pair_count` background pairs
        (small `baseline_amount` per pair per day, day=anchor) PLUS one
        SPIKE between the first sender + recipient with `spike_
        magnitude`.

        **AT.0 finding (caught mid-spike).** The spike magnitude alone
        doesn't determine z-score — the OUTLIER ITSELF SHIFTS THE MEAN
        toward itself, reducing its own z-score. With 8 baseline pairs +
        spike=100_000, z ≈ 2.67 (only '2-3 sigma'). With 100 baseline
        pairs + same spike, the spike contributes ~1% to the mean, so
        z ≈ 9.95 — well into '4+ sigma'. Default baseline_pair_count is
        100 to ensure clear separation; smaller values risk the
        outlier-effect masking the anomaly.

        baseline_pair_count > 1 is REQUIRED — a single baseline pair
        gives pop_stddev=0 → matview defaults bucket to '0-1 sigma'.
        Spike sits in the same bucket; nothing fires.
        """
        inst = instance if instance is not None else _spec_example()
        sender = _find_internal_with_role(inst, sender_role)
        recipient = _find_internal_with_role_and_parent(inst, recipient_role)
        return AnomalyGenerator(
            sender_account_id=f"acct-anomaly-sender-{sender_role}",
            sender_account_role=sender_role,
            sender_account_parent_role=(
                str(getattr(sender, "parent_role"))
                if getattr(sender, "parent_role", None) is not None
                else None
            ),
            recipient_account_id=f"acct-anomaly-recipient-{recipient_role}",
            recipient_account_role=recipient_role,
            recipient_account_parent_role=str(
                getattr(recipient, "parent_role"),
            ),
            anchor_day=LOCKED_ANCHOR,
            spike_magnitude=spike_magnitude,
            baseline_pair_count=baseline_pair_count,
            baseline_amount=baseline_amount,
        )


@dataclass
class AnomalyGenerator:
    """Plant a baseline distribution + a spike between sender ↔ recipient.

    Emits:
    - `baseline_pair_count` extra pairs of background accounts with
      small amounts on the anchor day — these populate the matview's
      population so pop_stddev > 0.
    - ONE spike pair (sender → recipient) on the anchor day with
      `spike_magnitude` — sits far above the baseline → high z-score
      → fires anomaly's '3-4 sigma' or '4+ sigma' bucket.

    Per AP.3 finding #2 — statistical invariants are multi-row by
    nature. The generator's emit() writes ALL the rows in one call;
    the spine Protocol stays minimal (no per-row-iterator).
    """

    sender_account_id: str
    sender_account_role: str
    sender_account_parent_role: str | None
    recipient_account_id: str
    recipient_account_role: str
    recipient_account_parent_role: str
    anchor_day: date
    spike_magnitude: float
    baseline_pair_count: int
    baseline_amount: float

    @property
    def intended(self) -> Violation:
        # Identity is (sender, recipient, window_end). The spike pair
        # lands on window_end == anchor_day. Bucket depends on z-score
        # magnitude; for spike >> baseline, expect '4+ sigma'.
        return Violation.of(
            "inv_pair_rolling_anomalies",
            sender_account_id=self.sender_account_id,
            recipient_account_id=self.recipient_account_id,
            window_end=self.anchor_day,
            z_bucket="4+ sigma",
        )

    def emit(self, conn: sqlite3.Connection) -> None:
        # Background pairs — populate the distribution so pop_stddev > 0.
        # Each is a 2-leg transfer (sender_leg with amount < 0; recipient
        # leg with amount > 0). The matview's pair_legs CTE joins on
        # transfer_id where amount signs differ.
        for i in range(self.baseline_pair_count):
            bg_sender_id = f"acct-anomaly-bg-sender-{i}"
            bg_recipient_id = f"acct-anomaly-bg-recipient-{i}"
            bg_transfer_id = f"xfer-anomaly-bg-{i}"
            self._emit_pair(
                conn,
                sender_account_id=bg_sender_id,
                sender_account_role=self.sender_account_role,
                sender_account_parent_role=self.sender_account_parent_role,
                recipient_account_id=bg_recipient_id,
                recipient_account_role=self.recipient_account_role,
                recipient_account_parent_role=self.recipient_account_parent_role,
                transfer_id=bg_transfer_id,
                amount=self.baseline_amount,
                day=self.anchor_day,
                slot=f"bg-{i}",
            )

        # The spike — between sender + recipient with spike_magnitude.
        self._emit_pair(
            conn,
            sender_account_id=self.sender_account_id,
            sender_account_role=self.sender_account_role,
            sender_account_parent_role=self.sender_account_parent_role,
            recipient_account_id=self.recipient_account_id,
            recipient_account_role=self.recipient_account_role,
            recipient_account_parent_role=self.recipient_account_parent_role,
            transfer_id=f"xfer-anomaly-spike",
            amount=self.spike_magnitude,
            day=self.anchor_day,
            slot="spike",
        )

    def _emit_pair(
        self,
        conn: sqlite3.Connection,
        *,
        sender_account_id: str,
        sender_account_role: str,
        sender_account_parent_role: str | None,
        recipient_account_id: str,
        recipient_account_role: str,
        recipient_account_parent_role: str,
        transfer_id: str,
        amount: float,
        day: date,
        slot: str,
    ) -> None:
        # Sender leg (Debit, money < 0)
        _insert_tx(
            conn,
            id=f"tx-{slot}-sender",
            account_id=sender_account_id,
            account_name=f"Anomaly Sender ({slot})",
            account_role=sender_account_role,
            account_scope="internal",
            account_parent_role=sender_account_parent_role,
            amount_money=-amount,
            amount_direction="Debit",
            status="Posted",
            posting=_ts(day),
            transfer_id=transfer_id,
            rail_name="ach",
            origin="etl",
        )
        # Recipient leg (Credit, money > 0)
        _insert_tx(
            conn,
            id=f"tx-{slot}-recipient",
            account_id=recipient_account_id,
            account_name=f"Anomaly Recipient ({slot})",
            account_role=recipient_account_role,
            account_scope="internal",
            account_parent_role=recipient_account_parent_role,
            amount_money=amount,
            amount_direction="Credit",
            status="Posted",
            posting=_ts(day),
            transfer_id=transfer_id,
            rail_name="ach",
            origin="etl",
        )


# ---------------------------------------------------------------------------
# In-process harness — mirrors AS.0 + AU.0.
# ---------------------------------------------------------------------------


def _fresh_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON;")
    _register_sqlite_aggregates(conn)
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


def _spec_example() -> L2Instance:
    return load_instance(_SPEC_EXAMPLE)


def _find_internal_with_role(instance: L2Instance, role: str) -> object:
    for a in instance.accounts:
        if (
            getattr(a, "role", None) == role
            and getattr(a, "scope", None) == "internal"
        ):
            return a
    raise ValueError(
        f"shape has no internal account with role {role!r}; "
        f"cannot manufacture an anomaly scenario"
    )


def _find_internal_with_role_and_parent(instance: L2Instance, role: str) -> object:
    """The matview's recipient filter requires
    `account_parent_role IS NOT NULL` AND `account_scope = 'internal'`."""
    for a in instance.accounts:
        if (
            getattr(a, "role", None) == role
            and getattr(a, "scope", None) == "internal"
            and getattr(a, "parent_role", None) is not None
        ):
            return a
    raise ValueError(
        f"shape has no leaf-internal account (parent_role set) with role "
        f"{role!r}; cannot manufacture an anomaly recipient scenario"
    )


_TX_COLS = (
    "id", "account_id", "account_name", "account_role", "account_scope",
    "account_parent_role", "amount_money", "amount_direction", "status",
    "posting", "transfer_id", "transfer_parent_id", "rail_name", "origin",
)


def _insert_tx(conn: sqlite3.Connection, **vals: object) -> None:
    placeholders = ", ".join("?" for _ in _TX_COLS)
    conn.execute(
        f"INSERT INTO {_PREFIX}_transactions ({', '.join(_TX_COLS)}) "
        f"VALUES ({placeholders})",
        [vals.get(c) for c in _TX_COLS],
    )


def _ts(day: date, hour: int = 12) -> str:
    return datetime(day.year, day.month, day.day, hour).strftime(
        "%Y-%m-%d %H:%M:%S",
    )


def _to_date(bd: object) -> date:
    return datetime.strptime(str(bd)[:10], "%Y-%m-%d").date()


# ---------------------------------------------------------------------------
# The end-to-end slice — full spine round-trip.
# ---------------------------------------------------------------------------


def test_anomaly_threads_the_full_spine() -> None:
    """The AT.0 proving ground: anomaly threaded through every spine
    type — Violation ⋈ Invariant.detect ⋈ scenario_for ⋈
    ViolationGenerator (multi-row baseline-plus-spike) ⋈ View (the AR
    primitive). Real emitted `<prefix>_inv_pair_rolling_anomalies` SQL
    runs in-process."""
    inv = AnomalyInvariant()
    assert inv.name == "inv_pair_rolling_anomalies"
    # Protocol satisfaction — the AS.1 spine vocab handles the windowed-
    # statistical case without specialization hooks.
    assert isinstance(inv, Invariant)

    gen = inv.scenario_for(
        "CustomerSubledger", "CustomerSubledger",
        spike_magnitude=100_000.0,
        baseline_pair_count=100,  # ← AT.0 finding: outlier-shifts-mean
        baseline_amount=100.0,
    )
    assert isinstance(gen, ViolationGenerator)
    intended = gen.intended

    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        _refresh(conn)

        detected = inv.detect(conn)
        # The spike's window should land in the high-sigma bucket. Bucket
        # exact value depends on population — assert "fires", not exact
        # bucket, since baseline noise might push it to 3-4 or 4+.
        spike_violations = {
            v for v in detected
            if (
                dict(v.identity).get("sender_account_id")
                == gen.sender_account_id
                and dict(v.identity).get("recipient_account_id")
                == gen.recipient_account_id
            )
        }
        assert spike_violations, (
            f"anomaly detector did not pick up the spike pair.\n"
            f"  intended (any bucket): {intended}\n"
            f"  detected: {detected}"
        )

        # View presents the violation — the anchor day is the window's
        # right edge by construction.
        view = DateView(frame=AsOfFrame.locked())
        resolved = view.resolve_day([LOCKED_ANCHOR])
        assert resolved == LOCKED_ANCHOR
        assert view.is_satisfied_by([LOCKED_ANCHOR])
    finally:
        conn.close()


def test_single_baseline_pair_does_not_fire_anomaly() -> None:
    """The AP.3 finding #2 pinned: statistical invariants need a
    POPULATION, not just two points. With baseline_pair_count=1 + spike,
    the matview's population has only 2 values; the spike's z-score is
    ~0.71 ('0-1 sigma' bucket) because the spike itself shifts the
    mean toward itself enough that its "distance from mean" in
    stddev-units stays small. The spike DOES exist, but isn't anomalous
    by the statistical measure.

    AT.0 finding: the bar for "anomaly fires" is NOT just
    "spike-to-baseline ratio is large." It's "spike sits enough sigma
    above the population mean that the outlier-effect-on-mean doesn't
    swallow it." Needs ~100 baseline points for a 1000:1 spike-ratio
    to clear 3σ."""
    inv = AnomalyInvariant()
    # baseline_pair_count=1 → only 2 population values (1 baseline +
    # spike). The pair-of-points spread inflates pop_stddev relative to
    # the (mean-shifted-by-spike) ⇒ spike's z ≈ 0.71 ⇒ '0-1 sigma'.
    # AP.3 finding #2 manifests slightly differently than my first-write
    # docstring claimed (which said pop_stddev=0 — that's only true with
    # ZERO baseline pairs, which would have no rows for the matview at
    # all). The actual mechanism: with a near-empty population, the
    # spike's outlier-effect-on-mean dominates the z calculation.
    gen = inv.scenario_for(
        "CustomerSubledger", "CustomerSubledger",
        spike_magnitude=100_000.0,
        baseline_pair_count=1,
        baseline_amount=100.0,
    )

    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        _refresh(conn)
        detected = inv.detect(conn)
    finally:
        conn.close()

    # No anomalies should fire — pop_stddev = 0 means matview defaults
    # bucket to '0-1 sigma' for every row, including the spike.
    assert detected == set(), (
        f"with degenerate population (1 baseline pair), no anomaly "
        f"should fire; got {detected}"
    )


def test_no_spike_no_anomaly() -> None:
    """The non-violating shape: baseline ONLY (spike_magnitude == baseline_
    amount) → no pair stands out → no anomaly in high-sigma bucket. AP.2
    convention adapted: 'no perturbation' is the same generator with the
    spike normalized to baseline."""
    inv = AnomalyInvariant()
    gen = inv.scenario_for(
        "CustomerSubledger", "CustomerSubledger",
        spike_magnitude=100.0,  # ← spike == baseline ⇒ no spike
        baseline_pair_count=8,
        baseline_amount=100.0,
    )

    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        _refresh(conn)
        detected = inv.detect(conn)
    finally:
        conn.close()

    # Everyone's in '0-1 sigma'; detect() returns empty (no high-bucket).
    assert detected == set()


def test_scenario_for_unknown_role_fails_loud() -> None:
    """Smart-constructor discipline — same as drift / overdraft / ..."""
    with pytest.raises(ValueError, match="no .* account with role"):
        AnomalyInvariant().scenario_for("NoSuchRole", "CustomerSubledger")
    with pytest.raises(ValueError, match="no leaf-internal account"):
        AnomalyInvariant().scenario_for("CustomerSubledger", "NoSuchRole")


def test_view_anchored_at_frame_carries_one_anchor_through_the_spine() -> None:
    """AR.1 promise extends to anomaly: the generator's anchor IS the
    frame's `as_of` by construction; the view's `required_coverage`
    contains the spike day."""
    frame = AsOfFrame.locked(window_days=7)
    view = DateView(frame=frame)
    gen = AnomalyInvariant().scenario_for(
        "CustomerSubledger", "CustomerSubledger",
    )
    assert gen.anchor_day == frame.as_of
    lo, hi = view.required_coverage
    assert lo <= gen.anchor_day <= hi


def test_detect_does_not_cross_a_sql_pushdown_surface() -> None:
    """AR.5 lesson extends — anomaly's detect() reads the matview with
    static SQL; no `<<$param>>` substitution surface. Note: the
    threshold-bucket filter (`WHERE z_bucket IN ('3-4 sigma', '4+ sigma')`)
    is a literal IN-clause, not a parameter — same as L1's matview-direct
    reads."""
    inv = AnomalyInvariant()
    conn = _fresh_db()
    try:
        captured: list[str] = []
        conn.set_trace_callback(captured.append)
        inv.detect(conn)
        conn.set_trace_callback(None)
    finally:
        conn.close()
    assert captured
    for sql in captured:
        assert "<<$" not in sql, (
            f"anomaly detector unexpectedly crossed a SQL-pushdown "
            f"surface; AR.5-style substitution-path test required.\n"
            f"  sql: {sql!r}"
        )


# ---------------------------------------------------------------------------
# Statistical/windowed-specific findings.
# ---------------------------------------------------------------------------


def test_anomaly_plant_is_multi_row_by_construction() -> None:
    """AP.3 finding #2 made structural: a statistical invariant CANNOT
    fire from a single emission. The generator's emit() writes
    baseline_pair_count*2 + 2 transactions (each pair = sender + recipient
    legs). Pinning this so AT.1's promotion can't accidentally optimize
    to a single-row plant."""
    gen = AnomalyInvariant().scenario_for(
        "CustomerSubledger", "CustomerSubledger",
        baseline_pair_count=8,
    )
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        tx_count = conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_transactions",
        ).fetchone()[0]
    finally:
        conn.close()
    # 8 baseline pairs * 2 legs/pair + 1 spike pair * 2 legs = 18 rows
    assert tx_count == 18, (
        f"expected 18 transactions (8 baseline pairs + 1 spike, 2 legs "
        f"each); got {tx_count}"
    )


def test_anomaly_emission_does_not_trip_drift_without_balance_rows() -> None:
    """Anomaly plants Posted transactions but no balance rows. Drift's
    matview JOINs current_daily_balances to computed_subledger_balance —
    no balance row means no drift row materializes. So a standalone
    anomaly plant is single-edge to anomaly. Composition (anomaly +
    drift in one scenario) would surface cross-class edges — AT.x's
    analogue of AU.2's composition test.

    This pins AT.0's prediction: AnomalyGenerator registers as
    `(AnomalyInvariant,)` — single-edge, matching the
    stuck_unbundled / limit_breach pattern (Posted leg, no balance row).
    """
    gen = AnomalyInvariant().scenario_for(
        "CustomerSubledger", "CustomerSubledger",
    )
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        _refresh(conn)
        # Check that drift matview is empty for these accounts.
        drift_rows = conn.execute(
            f"SELECT COUNT(*) FROM {_PREFIX}_drift",
        ).fetchone()[0]
    finally:
        conn.close()
    assert drift_rows == 0, (
        f"anomaly plant unexpectedly tripped drift; expected 0 rows "
        f"(no balance plant ⇒ no drift JOIN match), got {drift_rows}"
    )
