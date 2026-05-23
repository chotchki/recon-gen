"""Anomaly family — windowed-statistical L2 invariant + simple generator.

Promoted from `tests/unit/test_at0_anomaly_full_spine.py` (AT.0 spike).
The matview ``<prefix>_inv_pair_rolling_anomalies`` computes a rolling
2-day SUM per (sender, recipient) pair, then z-scores against the
population mean+stddev of all pair-windows. The `AnomalyInvariant`
detector projects rows with `z_bucket` >= '3-4 sigma' as Violations.

Per AP.3 finding #2: statistical invariants CAN'T be generated from a
single row — they need a POPULATION + a spike. `AnomalyGenerator` plants
N baseline pairs (small uniform amounts) + 1 spike pair (large amount
between the target sender + recipient). The spike's z-score against the
population distribution → high σ bucket → detector fires.

AT.0 finding (caught mid-spike, encoded as default + docstring): the
spike's z-score is REDUCED by its own contribution to the mean (outlier
self-shift). With small baselines (e.g. 8 pairs) + 100k spike, z ≈ 2.67
(too low to fire 3σ). Default `baseline_pair_count=100` dilutes the
outlier effect to ~1% → z ≈ 9.95 (clearly '4+ sigma').

Per AP.3 finding #3: the σ threshold (which buckets count as "anomalous")
lives on the View, not the detector. AT.2 will move the threshold to a
View knob; AT.1 bakes in 3σ as the spine-default cutoff.

AT.2 will refactor the generator to use AS.3's `AccountSimulation`
stateful-fold base; AT.1 keeps the single-emission shape from the AT.0
spike. The detector + scenario_for are stable across the refactor.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import ClassVar

from recon_gen.common.l2.primitives import L2Instance
from recon_gen.common.spine._emit_helpers import (
    find_internal_with_role,
    insert_tx,
    load_spec_example,
    to_date,
    ts,
)
from recon_gen.common.spine.violation import Violation


# AT.0 finding: 100 baseline pairs is the minimum to dilute the spike's
# outlier-effect-on-mean enough for 3σ to fire on a 1000:1 spike ratio.
_DEFAULT_BASELINE_PAIR_COUNT = 100
_DEFAULT_BASELINE_AMOUNT = 100.0
_DEFAULT_SPIKE_MAGNITUDE = 100_000.0


@dataclass(frozen=True)
class AnomalyInvariant:
    """Pair-rolling-anomaly detector. Reads
    `<prefix>_inv_pair_rolling_anomalies` and projects EVERY row as a
    Violation — every (pair, window_end) the matview computed, across
    every `z_bucket` (including '0-1 sigma' background).

    Per AP.3 finding #3, the σ threshold belongs on the **View**, not
    the detector. AT.2 promoted `AnomalyView` (`anomaly_view.py`) that
    slices over the detected violation set on `sigma_threshold`. The
    detector here is now bucket-agnostic — `AnomalyView(3.0).slice(...)`
    reproduces AT.1's behaviour exactly; other thresholds (2.0 for
    deep-dive triage, etc.) work over the same `detect()` result with
    no re-query.
    """

    name: ClassVar[str] = "inv_pair_rolling_anomalies"
    prefix: str = "spec_example"

    def detect(self, conn: sqlite3.Connection) -> set[Violation]:
        rows = conn.execute(
            f"SELECT sender_account_id, recipient_account_id, window_end, "
            f"z_bucket "
            f"FROM {self.prefix}_inv_pair_rolling_anomalies",
        ).fetchall()
        return {
            Violation.of(
                "inv_pair_rolling_anomalies",
                sender_account_id=str(said),
                recipient_account_id=str(raid),
                window_end=to_date(we),
                z_bucket=str(zb),
            )
            for said, raid, we, zb in rows
        }

    def scenario_for(
        self,
        sender_role: str,
        recipient_role: str,
        *,
        spike_magnitude: float = _DEFAULT_SPIKE_MAGNITUDE,
        baseline_pair_count: int = _DEFAULT_BASELINE_PAIR_COUNT,
        baseline_amount: float = _DEFAULT_BASELINE_AMOUNT,
        anchor_day: date = date(2030, 1, 1),
        instance: L2Instance | None = None,
    ) -> "AnomalyGenerator":
        """Resolve sender + recipient roles; return a generator that
        plants `baseline_pair_count` baseline pairs + 1 spike between
        sender + recipient.

        See AT.0 spike's docstring for the full statistical-coverage
        argument. The defaults (100 baseline / 100_000 spike) give a
        clear ~10σ separation; tweak for tests that explore the
        threshold boundary (set spike=baseline to defuse).

        Raises `ValueError` if either role is missing from the shape's
        internal accounts (sender) or leaf internal accounts (recipient
        — the matview's recipient filter requires
        `account_parent_role IS NOT NULL`).
        """
        inst = instance if instance is not None else load_spec_example()
        sender = find_internal_with_role(
            inst, sender_role, error_kind="anomaly sender",
        )
        recipient = find_internal_with_role(
            inst, recipient_role, must_be_leaf=True,
            error_kind="anomaly recipient",
        )
        # Recipient's parent_role is guaranteed non-None by must_be_leaf.
        assert recipient.parent_role is not None
        return AnomalyGenerator(
            sender_account_id=f"acct-anomaly-sender-{sender_role}",
            sender_account_role=sender_role,
            sender_account_parent_role=sender.parent_role,
            recipient_account_id=f"acct-anomaly-recipient-{recipient_role}",
            recipient_account_role=recipient_role,
            recipient_account_parent_role=recipient.parent_role,
            anchor_day=anchor_day,
            spike_magnitude=spike_magnitude,
            baseline_pair_count=baseline_pair_count,
            baseline_amount=baseline_amount,
        )


@dataclass
class AnomalyGenerator:
    """Plant a baseline distribution + a spike between sender ↔ recipient.

    Emits `baseline_pair_count` extra pairs of background accounts with
    small uniform amounts on the anchor day (populates the matview's
    pop_stddev) plus ONE spike pair (sender → recipient) with
    `spike_magnitude` (sits far above baseline → high z-score → fires).

    Per AP.3 finding #2 (statistical invariants are multi-row by
    nature): the generator's `emit()` writes ALL the rows in one call
    — the Protocol stays minimal; the per-row-iterator shape isn't
    pushed onto the Generator contract. AT.2 refactors to use AS.3's
    `AccountSimulation` stateful-fold base, but the public Protocol
    contract doesn't change.
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
        # Identity: (sender, recipient, window_end). Bucket depends on
        # z-score; for spike >> baseline, expect '4+ sigma'.
        return Violation.of(
            "inv_pair_rolling_anomalies",
            sender_account_id=self.sender_account_id,
            recipient_account_id=self.recipient_account_id,
            window_end=self.anchor_day,
            z_bucket="4+ sigma",
        )

    def emit(self, conn: sqlite3.Connection) -> None:
        # Background pairs populate the distribution.
        for i in range(self.baseline_pair_count):
            self._emit_pair(
                conn,
                sender_account_id=f"acct-anomaly-bg-sender-{i}",
                recipient_account_id=f"acct-anomaly-bg-recipient-{i}",
                transfer_id=f"xfer-anomaly-bg-{i}",
                amount=self.baseline_amount,
                slot=f"bg-{i}",
            )
        # The spike — between sender + recipient with spike_magnitude.
        self._emit_pair(
            conn,
            sender_account_id=self.sender_account_id,
            recipient_account_id=self.recipient_account_id,
            transfer_id="xfer-anomaly-spike",
            amount=self.spike_magnitude,
            slot="spike",
        )

    def _emit_pair(
        self,
        conn: sqlite3.Connection,
        *,
        sender_account_id: str,
        recipient_account_id: str,
        transfer_id: str,
        amount: float,
        slot: str,
    ) -> None:
        # Sender leg (Debit, money < 0)
        insert_tx(
            conn,
            id=f"tx-{slot}-sender",
            account_id=sender_account_id,
            account_name=f"Anomaly Sender ({slot})",
            account_role=self.sender_account_role,
            account_scope="internal",
            account_parent_role=self.sender_account_parent_role,
            amount_money=-amount,
            amount_direction="Debit",
            status="Posted",
            posting=ts(self.anchor_day),
            transfer_id=transfer_id,
            rail_name="ach",
            origin="etl",
        )
        # Recipient leg (Credit, money > 0)
        insert_tx(
            conn,
            id=f"tx-{slot}-recipient",
            account_id=recipient_account_id,
            account_name=f"Anomaly Recipient ({slot})",
            account_role=self.recipient_account_role,
            account_scope="internal",
            account_parent_role=self.recipient_account_parent_role,
            amount_money=amount,
            amount_direction="Credit",
            status="Posted",
            posting=ts(self.anchor_day),
            transfer_id=transfer_id,
            rail_name="ach",
            origin="etl",
        )
