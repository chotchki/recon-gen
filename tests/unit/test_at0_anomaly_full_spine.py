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

import duckdb
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pytest

from recon_gen.common.as_of_frame import LOCKED_ANCHOR, AsOfFrame
from recon_gen.common.db import execute_script
from recon_gen.common.l2.loader import load_instance
from recon_gen.common.l2.primitives import L2Instance, SCOPE_INTERNAL
from recon_gen.common.l2.schema import emit_schema, refresh_matviews_sql
from recon_gen.common.spine import (
    Invariant,
    Violation,
    ViolationGenerator,
)
from recon_gen.common.sql import Dialect
from recon_gen.common.tree import DateView
from tests._test_helpers import fetch_scalar

_SPEC_EXAMPLE = Path(__file__).resolve().parents[1] / "l2" / "spec_example.yaml"
_PREFIX = "spec_example"
_DIALECT = Dialect.DUCKDB


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

    def detect(self, conn: duckdb.DuckDBPyConnection) -> set[Violation]:
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
        historical_window_count: int = 10,
        historical_window_amount: float = 250.0,
        instance: L2Instance | None = None,
    ) -> "AnomalyGenerator":
        """Resolve sender + recipient roles against the L2; return a
        generator that plants `baseline_pair_count` background pairs +
        `historical_window_count` historical windows on the spike pair
        + one SPIKE on anchor day.

        **AT.0 finding (caught mid-spike).** Pre-CV the spike magnitude
        alone didn't determine z-score — under GLOBAL z, the outlier
        shifted the population mean toward itself. **CV.2 switched the
        matview to per-pair PARTITION BY z with a PRIOR-ROWS-ONLY frame**
        (ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) so the
        spike's row is excluded from its own divisor. The new
        load-bearing param is `historical_window_count` — the spike
        pair needs ≥`_INV_MIN_HISTORICAL_WINDOWS=3` prior windows or
        the matview's min-n floor collapses z to 0. Default 10 sits
        3× the floor (safety margin).

        baseline_pair_count > 0 keeps the population-of-pairs shape;
        post-CV.2 it no longer affects the spike's z (per-pair, not
        global).
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
            historical_window_count=historical_window_count,
            historical_window_amount=historical_window_amount,
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
    historical_window_count: int = 10
    historical_window_amount: float = 250.0

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

    def emit(self, conn: duckdb.DuckDBPyConnection) -> None:
        # Stratum 1 — unrelated background pairs (anchor day, one each).
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

        # Stratum 2 (CV.1) — historical windows on the spike pair so the
        # matview's per-pair PARTITION BY z has a divisor.
        from datetime import timedelta as _td  # local; no top-level dep
        N = self.historical_window_count
        for k in range(N, 0, -1):
            history_day = self.anchor_day - _td(days=k)
            # Vary amounts deterministically around the center so per-pair
            # STDDEV_SAMP > 0. Same shape as spine/anomaly.py.
            offset_index = (k - 1) - (N - 1) / 2.0
            step = self.historical_window_amount * 0.10
            amount = self.historical_window_amount + offset_index * step
            self._emit_pair(
                conn,
                sender_account_id=self.sender_account_id,
                sender_account_role=self.sender_account_role,
                sender_account_parent_role=self.sender_account_parent_role,
                recipient_account_id=self.recipient_account_id,
                recipient_account_role=self.recipient_account_role,
                recipient_account_parent_role=self.recipient_account_parent_role,
                transfer_id=f"xfer-anomaly-history-{k}",
                amount=amount,
                day=history_day,
                slot=f"history-{k}",
            )

        # Stratum 3 — the spike on anchor day.
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
        conn: duckdb.DuckDBPyConnection,
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


def _fresh_db() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    instance = load_instance(_SPEC_EXAMPLE)
    cur = conn.cursor()
    execute_script(
        cur, emit_schema(instance, prefix=_PREFIX, dialect=_DIALECT),
        dialect=_DIALECT,
    )
    conn.commit()
    return conn


def _refresh(conn: duckdb.DuckDBPyConnection) -> None:
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
            and getattr(a, "scope", None) == SCOPE_INTERNAL
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
            and getattr(a, "scope", None) == SCOPE_INTERNAL
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


def _insert_tx(conn: duckdb.DuckDBPyConnection, **vals: object) -> None:
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


def test_below_min_n_floor_does_not_fire_anomaly() -> None:
    """CV.2 rewrite — pre-CV asserted the "AP.3 finding #2 / one-pair
    population" path. Under per-pair PARTITION BY z (CV.2) the
    governing parameter is the spike pair's PRIOR-WINDOW count, not
    the unrelated-baseline-pair count. The matview's min-n floor
    (default 3) collapses the spike to '0-1 sigma' when the spike
    pair has < 3 prior windows.

    With `historical_window_count=2`, the spike pair has only 2 prior
    windows → `pair_n < min_n_floor` → z forced to 0 → no high-bucket
    row → empty detected set.
    """
    inv = AnomalyInvariant()
    gen = inv.scenario_for(
        "CustomerSubledger", "CustomerSubledger",
        spike_magnitude=100_000.0,
        baseline_pair_count=10,
        baseline_amount=100.0,
        historical_window_count=2,  # below matview's min_n=3 floor
    )

    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        _refresh(conn)
        detected = inv.detect(conn)
    finally:
        conn.close()

    # No anomalies should fire — pair_n < min_n_floor means matview
    # forces z=0 (bucket '0-1 sigma') for every row, including the spike.
    assert detected == set(), (
        f"with spike-pair history below the min-n floor (2 < 3), no "
        f"anomaly should fire; got {detected}"
    )


def test_no_spike_no_anomaly() -> None:
    """The non-violating shape: spike == historical-window center → the
    spike day's window_sum sits inside the historical distribution → no
    anomaly fires.

    CV.2 update — under per-pair z, "no perturbation" means
    `spike_magnitude` matches the `historical_window_amount`. The
    pre-CV form (spike == baseline_amount) was about the global
    population; that path is gone.
    """
    inv = AnomalyInvariant()
    gen = inv.scenario_for(
        "CustomerSubledger", "CustomerSubledger",
        spike_magnitude=250.0,  # ← matches historical center
        baseline_pair_count=8,
        baseline_amount=100.0,
        historical_window_amount=250.0,
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


@pytest.mark.skip(reason="set_trace_callback was SQLite-only; DuckDB has no equivalent. CB.8 backlog #set_trace.")
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
        inv.detect(conn)
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
    fire from a single emission. CV.1 expands the multi-row requirement
    to include the spike-pair history stratum.

    Emit count: (baseline_pair_count + historical_window_count + 1) * 2
    legs per pair.
    """
    gen = AnomalyInvariant().scenario_for(
        "CustomerSubledger", "CustomerSubledger",
        baseline_pair_count=8,
        historical_window_count=4,
    )
    conn = _fresh_db()
    try:
        gen.emit(conn)
        conn.commit()
        tx_count = fetch_scalar(conn, f"SELECT COUNT(*) FROM {_PREFIX}_transactions",)
    finally:
        conn.close()
    # (8 baseline + 4 history + 1 spike) * 2 legs = 26 rows
    assert tx_count == 26, (
        f"expected 26 transactions (8 baseline + 4 history + 1 spike, "
        f"2 legs each); got {tx_count}"
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
        drift_rows = fetch_scalar(conn, f"SELECT COUNT(*) FROM {_PREFIX}_drift",)
    finally:
        conn.close()
    assert drift_rows == 0, (
        f"anomaly plant unexpectedly tripped drift; expected 0 rows "
        f"(no balance plant ⇒ no drift JOIN match), got {drift_rows}"
    )
