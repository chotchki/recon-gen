"""Anomaly family — windowed-statistical L2 invariant + generator.

Promoted from `tests/unit/test_at0_anomaly_full_spine.py` (AT.0 spike).
The matview ``<prefix>_inv_pair_rolling_anomalies`` computes a rolling
2-day SUM per (sender, recipient) pair, then z-scores against the
**per-pair** distribution (CV.2: PARTITION BY recipient, sender). The
`AnomalyInvariant` detector projects EVERY row as a Violation; the
`AnomalyView` (AT.2, `anomaly_view.py`) slices on σ threshold.

Per AP.3 finding #2: statistical invariants CAN'T be generated from a
single row — they need a POPULATION + a spike. `AnomalyGenerator` plants
N background pairs at small uniform amounts AND N historical windows
on the spike pair itself, then the spike on `anchor_day`.

**CV.1 restructure — the per-pair history is load-bearing.** Prior shape
(pre-CV) emitted the spike pair only on the anchor day. With the CV.2
matview switch to per-pair PARTITION BY z, a pair with no prior history
has ``STDDEV_SAMP = NULL`` (no samples to compute against); the
``CASE WHEN pair_stddev = 0 THEN 0`` arm collapses the spike to z=0 and
it vanishes. The fix: each `AnomalyGenerator` instance emits a
`historical_window_count`-deep baseline FOR THE SPIKE PAIR over the
business days preceding `anchor_day`, at `historical_window_amount_cents`
each. The matview's per-pair `STDDEV_SAMP` then has N historical
observations to anchor against; the spike on anchor_day fires at a
meaningful z-score (e.g. for default N=20 historical @ $250 + spike
$100K, z >> 4 trivially because the spike is ~400× the per-pair mean).

The 100 unrelated background pairs still exist — they exercise the
matview's per-pair-aggregate over the population so other pairs surface
their own distributions (and so the matview produces ≥100 rows in
testing, not 11). Per-pair z is naturally scale-invariant: each pair
gets z-scored against ITS OWN historical baseline, so the long-tail
busy-pair payday clusters (which trip global-z at z>>4) sit at z≈0
under per-pair (their "spike" IS their normal traffic).

The min-n floor is enforced in the matview (CV.2): pairs with
< INV_MIN_HISTORICAL_WINDOWS observations get z=0 regardless. Default
floor is 3; AnomalyGenerator's default N=20 sits comfortably above it.

AT.3 refactored `emit()` onto the `Transfer` / `LedgerSimulation`
primitive — every leg pair goes through the same `_emit_transfer` path
that `MoneyTrailGenerator` uses. Single-edge property preserved
(transfers-only ledger → no balance rows → no drift trip). The
detector + scenario_for are stable across the refactor.
"""

from __future__ import annotations

from recon_gen.common.db import SyncConnection
from dataclasses import dataclass
from datetime import date, timedelta
from typing import ClassVar

from recon_gen.common.l2.primitives import POSTED_STATUS, L2Instance
from recon_gen.common.spine._db import fetch_all
from recon_gen.common.spine._emit_helpers import (
    find_internal_with_role,
    load_spec_example,
    to_date,
)
from recon_gen.common.spine.ledger_simulation import (
    LedgerSimulation,
    Transfer,
    TransferLeg,
)
from recon_gen.common.spine.violation import RuleViolation, Violation
from recon_gen.common.spine.math_invariant import math_invariant
from recon_gen.common.spine.residuals import (
    MathKind,
)


# CV.1 — defaults for the per-pair z geometry.
#
# `_DEFAULT_BASELINE_PAIR_COUNT = 100` keeps the historical (pre-CV)
# 100-unrelated-pair distribution: the matview's per-pair aggregate
# needs >>1 pair to surface a useful distribution at all, and 100
# matches what the prior global-z shape relied on.
#
# `_DEFAULT_BASELINE_AMOUNT = 100.0` is the per-pair background amount
# on the anchor day for the 100 unrelated pairs. With per-pair z each
# of those pairs has only ONE window (their anchor-day emission), so
# they hit the min-n floor in CV.2 and sit at z=0. They exist for
# matview-row-count reasons + future overlay composition, not for the
# z-score math.
#
# `_DEFAULT_SPIKE_MAGNITUDE = 100_000.0` is the spike amount on
# anchor_day for the (sender_role, recipient_role) pair. Under per-pair
# z this is z-scored against the spike pair's OWN historical baseline
# (default $250 × N=20 windows), so 100K → z ≈ 400 against a baseline
# stddev of ~0 — clearly fires '4+ sigma'. The magnitude no longer
# fights a global population mean it pollutes.
#
# `_DEFAULT_HISTORICAL_WINDOW_COUNT = 20` is the number of small-amount
# historical windows the generator emits on the spike pair BEFORE
# anchor_day. The matview's per-pair PARTITION BY z (CV.2) includes
# the current row in its own divisor (inclusive frame); for a single
# outlier among N+1 values, z caps at N/sqrt(N+1). For N=20 the
# ceiling is ≈ 4.36 — clears the 4σ band with margin. For N=10 the
# ceiling would be ≈ 3.02 — below 4σ. Sits 6× above the matview's
# `INV_MIN_HISTORICAL_WINDOWS=3` floor.
#
# `_DEFAULT_HISTORICAL_WINDOW_AMOUNT = 250.0` (~$250) is the CENTER per-
# window baseline amount on the spike pair. Picked small so the spike
# stands out at a high z; under per-pair PARTITION BY z with the
# inclusive (current-row-included) frame, the spike z asymptotes at
# `N / sqrt(N+1)` regardless of magnitude — what matters is that
# magnitude >> baseline so the asymptote is reached. With center=$250
# and spike=$100K the magnitude ratio is ~400×, well into the
# asymptotic regime.
#
# CV.1 deterministic variance: historical windows are emitted with
# amounts `center ± k * VARIANCE_STEP` walking the window index — see
# `_historical_amount_for_index` below. This keeps `pair_n` from
# falling below the matview's min-n floor by sitting above 0; the
# absolute stddev value matters less than the magnitude ratio.
_DEFAULT_BASELINE_PAIR_COUNT = 100
_DEFAULT_BASELINE_AMOUNT = 100.0
_DEFAULT_SPIKE_MAGNITUDE = 100_000.0
_DEFAULT_HISTORICAL_WINDOW_COUNT = 20
_DEFAULT_HISTORICAL_WINDOW_AMOUNT = 250.0

# 10% of default historical-window amount; the variance step the
# generator walks across the N historical windows so per-pair
# `STDDEV_SAMP` is non-zero. Centered on
# `historical_window_amount` (window k offsets by
# `(k - (N + 1) / 2) * step` so the sum-of-offsets averages to 0).
_HISTORICAL_VARIANCE_FRACTION = 0.10


@math_invariant(
    matview="inv_pair_rolling_anomalies",
    kind=MathKind.PROBABILISTIC,
    note="DS.4 exact-Q tolerance contract in anomaly_contract.py",
)
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

    def detect(self, conn: SyncConnection) -> set[Violation]:
        rows = fetch_all(
            conn,
            f"SELECT sender_account_id, recipient_account_id, window_end, "
            f"z_bucket "
            f"FROM {self.prefix}_inv_pair_rolling_anomalies",
        )
        return {
            RuleViolation.of(
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
        historical_window_count: int = _DEFAULT_HISTORICAL_WINDOW_COUNT,
        historical_window_amount: float = _DEFAULT_HISTORICAL_WINDOW_AMOUNT,
        anchor_day: date = date(2030, 1, 1),
        instance: L2Instance | None = None,
        sender_account_id: str | None = None,
        recipient_account_id: str | None = None,
    ) -> "AnomalyGenerator":
        """Resolve sender + recipient roles; return a generator that
        plants `baseline_pair_count` unrelated baseline pairs + N
        historical windows on the spike pair + 1 spike on anchor_day.

        **CV.1: per-pair historical baseline is load-bearing.** The
        matview (post-CV.2) z-scores each pair against its OWN history.
        Without historical windows on the spike pair, per-pair
        `STDDEV_SAMP` is NULL and the spike collapses to z=0.

        Args:
            sender_role: L2 internal account role for the spike sender.
            recipient_role: L2 leaf internal account role for the spike
                recipient (matview's recipient filter requires
                `account_parent_role IS NOT NULL`).
            spike_magnitude: Amount in dollars for the spike transfer
                on `anchor_day`. Default 100_000 → z >> 4 against a
                $250 historical baseline (1/400 ratio).
            baseline_pair_count: Number of UNRELATED background pairs
                emitted on `anchor_day` (each is one window for that
                pair → hits min-n floor → z=0; they exist for matview
                row-count + future composition, not z math).
            baseline_amount: Amount per background pair transfer.
            historical_window_count: Number of pair-windows the spike
                pair gets BEFORE `anchor_day` (one per business day,
                walking backwards). Default 10. Per-pair `STDDEV_SAMP`
                needs n≥2; the matview's min-n floor (CV.2) is 3; n=20
                sits 3× above the floor (safety margin) and gives a
                stable estimate.
            historical_window_amount: Amount per historical window on
                the spike pair. Default 250 (~$250). With spike=$100K
                this yields ~400× ratio → z >> 4 (even with stddev
                ≈ 0, the matview-side floor + non-zero stddev guarantee
                z is finite + large).
            anchor_day: The day the spike fires. Historical windows
                emit on the `historical_window_count` business days
                preceding this (`anchor_day - 1`, `anchor_day - 2`,
                ...; weekday assumption omitted — matview operates on
                calendar days).
            instance: L2 instance for role resolution. Defaults to
                `spec_example`.
            sender_account_id / recipient_account_id: Override the
                synthetic IDs (AY.4.c — N anomaly plants on the same
                role pair need distinct IDs).

        Raises:
            ValueError: When a role doesn't resolve, or when
                `historical_window_count < 2` (per-pair `STDDEV_SAMP`
                degenerate at n<2 → matview returns NULL → spike
                vanishes; loud-fail at construction beats silent zero
                z-scores in downstream tests).
        """
        if historical_window_count < 2:
            raise ValueError(
                f"AnomalyGenerator: historical_window_count must be ≥ 2 "
                f"(per-pair STDDEV_SAMP requires n ≥ 2 samples); got "
                f"{historical_window_count}. The matview's min-n floor "
                f"is 3 — recommended N=20 for a safe stable estimate."
            )
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
            sender_account_id=(
                sender_account_id or f"acct-anomaly-sender-{sender_role}"
            ),
            sender_account_role=sender_role,
            sender_account_parent_role=sender.parent_role,
            recipient_account_id=(
                recipient_account_id
                or f"acct-anomaly-recipient-{recipient_role}"
            ),
            recipient_account_role=recipient_role,
            recipient_account_parent_role=recipient.parent_role,
            anchor_day=anchor_day,
            spike_magnitude=spike_magnitude,
            baseline_pair_count=baseline_pair_count,
            baseline_amount=baseline_amount,
            historical_window_count=historical_window_count,
            historical_window_amount=historical_window_amount,
            # CV.4: thread invariant's prefix through so callers that
            # construct AnomalyInvariant(prefix=...) get the generator
            # writing to the same prefix's `_transactions` table.
            prefix=self.prefix,
        )


@dataclass
class AnomalyGenerator:
    """Plant a baseline distribution + per-pair history + a spike.

    CV.1 emits THREE strata, each via the AT.3 `Transfer` primitive:

    1. **Unrelated background pairs** (`baseline_pair_count` of them) at
       `baseline_amount` on `anchor_day`. These don't share accounts
       with the spike — they exist so the matview surfaces a population
       of distinct (sender, recipient) pairs (≥100 rows for testing,
       not 11). Under per-pair z each background pair gets exactly ONE
       window → hits the matview's min-n floor → z=0. No false
       positives.
    2. **Historical windows on the spike pair** (`historical_window_count`
       of them) at `historical_window_amount` on the business days
       preceding `anchor_day` (`anchor_day - 1`, `-2`, ..., walking
       back). These give the spike pair a per-pair `STDDEV_SAMP` history
       to z-score against. Without them, the matview's per-pair stddev
       is NULL and the spike collapses to z=0.
    3. **The spike** at `spike_magnitude` on `anchor_day`. Z-scored
       against strata-2's history; for default 100K spike vs 250 ×
       N=20 baseline, z ≈ (100_000 − 250) / pair_stddev. The matview's
       `pair_stddev = 0 ⇒ z=0` guard fires only if the historical
       windows have IDENTICAL amounts (stddev = 0); the CV.4 regression
       test owns empirical verification.

    AT.3 single-edge property preserved (transfers-only `LedgerSimulation`
    → no balance rows → no drift trip).

    Per AP.3 finding #2 (statistical invariants are multi-row by
    nature): `emit()` writes ALL the rows in one call; the Protocol
    stays minimal.
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
    historical_window_count: int = _DEFAULT_HISTORICAL_WINDOW_COUNT
    historical_window_amount: float = _DEFAULT_HISTORICAL_WINDOW_AMOUNT
    prefix: str = "spec_example"

    @property
    def intended(self) -> RuleViolation:
        # Identity: (sender, recipient, window_end). Bucket depends on
        # z-score; under CV.2 per-pair z, the spike pair's anchor-day
        # window is z-scored against `historical_window_count` prior
        # windows. With magnitude 100K vs ~$250 baseline, z >> 4 →
        # '4+ sigma' bucket.
        return RuleViolation.of(
            "inv_pair_rolling_anomalies",
            sender_account_id=self.sender_account_id,
            recipient_account_id=self.recipient_account_id,
            window_end=self.anchor_day,
            z_bucket="4+ sigma",
        )

    @property
    def claimed_accounts(self) -> frozenset[str]:
        """The 2 + 2*baseline_pair_count account_ids this plant touches.

        The historical-window stratum (CV.1) reuses the same spike
        sender + recipient IDs, so no new accounts are claimed — only
        the unrelated background pairs add IDs.
        """
        accounts: set[str] = {
            self.sender_account_id, self.recipient_account_id,
        }
        for i in range(self.baseline_pair_count):
            accounts.add(f"acct-anomaly-bg-sender-{i}")
            accounts.add(f"acct-anomaly-bg-recipient-{i}")
        return frozenset(accounts)

    def emit(
        self,
        conn: SyncConnection,
        *,
        scenario_id: str | None = None,
    ) -> None:
        LedgerSimulation(
            transfers=list(self._transfers()),
            prefix=self.prefix,
        ).emit(conn, scenario_id=scenario_id)

    def _transfers(self) -> list[Transfer]:
        """Build the three strata as `Transfer`s. Pure (no IO) —
        composable for callers that want to compose anomaly with other
        transfer-shaped generators.

        Emission order: unrelated background pairs → spike-pair
        historical windows (oldest first, walking forward) → spike on
        anchor_day. Order is presentation-only; the matview's per-pair
        SQL window doesn't depend on insertion order.
        """
        out: list[Transfer] = []

        # Stratum 1: unrelated background pairs (anchor_day, one each).
        for i in range(self.baseline_pair_count):
            out.append(self._build_pair(
                sender_account_id=f"acct-anomaly-bg-sender-{i}",
                recipient_account_id=f"acct-anomaly-bg-recipient-{i}",
                transfer_id=f"xfer-anomaly-bg-{i}",
                amount=self.baseline_amount,
                slot=f"bg-{i}",
                day=self.anchor_day,
            ))

        # Stratum 2: historical windows on the spike pair.
        # CV.1 — walk back `historical_window_count` calendar days. The
        # matview's rolling window is calendar-day based (range INTERVAL
        # over `posted_day`); no business-day filter applies. Step day
        # k goes from k=historical_window_count (furthest back) to k=1
        # (the day before anchor_day) so the emitted rows read in
        # chronological order — easier on debug logs.
        #
        # Amounts vary deterministically around the center so per-pair
        # `STDDEV_SAMP` is non-zero (see `_historical_amount_for_index`).
        for k in range(self.historical_window_count, 0, -1):
            history_day = self.anchor_day - timedelta(days=k)
            out.append(self._build_pair(
                sender_account_id=self.sender_account_id,
                recipient_account_id=self.recipient_account_id,
                transfer_id=f"xfer-anomaly-history-{k}",
                amount=self._historical_amount_for_index(k),
                slot=f"history-{k}",
                day=history_day,
            ))

        # Stratum 3: the spike on anchor_day.
        out.append(self._build_pair(
            sender_account_id=self.sender_account_id,
            recipient_account_id=self.recipient_account_id,
            transfer_id="xfer-anomaly-spike",
            amount=self.spike_magnitude,
            slot="spike",
            day=self.anchor_day,
        ))
        return out

    def _historical_amount_for_index(self, k: int) -> float:
        """CV.1 — deterministic per-window amount that varies symmetrically
        around `historical_window_amount` so per-pair `STDDEV_SAMP > 0`.

        Window index `k` runs from `historical_window_count` (furthest
        back) down to `1`. The offset walks linearly through
        ``[-step, +step]`` so the mean across all windows is exactly
        the center amount and the stddev is non-degenerate.

        With center=$250, N=20, fraction=0.10:
            step = $25, amounts range ~ $222 .. $278, stddev ≈ $17.
            Spike at $100K → z ≈ (100_000 - 250) / 17 ≈ 5870 σ.
        Far above any practical 4σ threshold.
        """
        N = self.historical_window_count
        if N < 2:
            # Defensive: scenario_for raises at construction. Keep the
            # generator pure here — return center if anyone bypasses
            # the smart constructor.
            return self.historical_window_amount
        step = self.historical_window_amount * _HISTORICAL_VARIANCE_FRACTION
        # Map k ∈ [1, N] to offset_index ∈ [-(N-1)/2, +(N-1)/2] centered
        # on 0 (so sum of offsets across all windows is 0 and the mean
        # is exactly `historical_window_amount`).
        offset_index = (k - 1) - (N - 1) / 2.0
        return self.historical_window_amount + offset_index * step

    def _build_pair(
        self,
        *,
        sender_account_id: str,
        recipient_account_id: str,
        transfer_id: str,
        amount: float,
        slot: str,
        day: date,
    ) -> Transfer:
        """One 2-leg balanced Posted Transfer: sender Debit + recipient
        Credit. `slot` flavors the account display names so test
        introspection can tell baseline from history from spike.

        `day` (CV.1) lets the historical-window stratum emit on past
        days while sharing the same builder.
        """
        return Transfer(
            day=day,
            transfer_id=transfer_id,
            rail_name="_spine_plant",
            status=POSTED_STATUS,
            legs=(
                TransferLeg(
                    account_id=sender_account_id,
                    amount=-amount,
                    account_name=f"Anomaly Sender ({slot})",
                    account_role=self.sender_account_role,
                    account_scope="internal",
                    account_parent_role=self.sender_account_parent_role,
                ),
                TransferLeg(
                    account_id=recipient_account_id,
                    amount=amount,
                    account_name=f"Anomaly Recipient ({slot})",
                    account_role=self.recipient_account_role,
                    account_scope="internal",
                    account_parent_role=self.recipient_account_parent_role,
                ),
            ),
        )
