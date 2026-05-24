"""Investigation fanout (seed-color) family — `ViolationGenerator` only.

AY.2.b promotion of `common/l2/seed.py::InvFanoutPlant` (N.4.h
fuzzer Investigation coverage). This is a SEED-COLOR generator: it
plants N two-leg transfers (each sender → one shared recipient) on
the anchor day so the Investigation dashboard's `inv_money_trail_edges`
+ `inv_pair_rolling_anomalies` matviews have data to operate on.

Per the AY.2.b evidence-currency layering:

  - `intended` returns a `CoverageObservation` keyed on
    `(recipient_account_id, sender_count, rail_name, anchor_day)`.
    The seed claims "I planted a healthy N-sender fanout to recipient R."

  - **Edge to MoneyTrailInvariant** (registered): every emitted
    transfer has `transfer_parent_id=NULL` → each is a depth-0
    (root) edge the `inv_money_trail_edges` recursive CTE surfaces.
    The registered edge documents this deterministic side-effect; it
    does NOT make InvFanoutGenerator a "rule violation" generator
    (the primary intent is still seed-color coverage). AU.5's gate
    permits coverage generators to carry edges; the bucket
    membership (`ALL_COVERAGE_GENERATORS`) is the discriminator on
    "this generator's primary purpose."

  - **NOT registered for AnomalyInvariant**: the rolling-window
    z-score is probabilistic. A 2-sender × $100 plant on a fresh DB
    won't pop the bucket threshold (the OLD `boost_inv_fanout_plants
    (5×)` deliberately multiplies to push it over). The minimal-
    viable smart constructor stays under threshold; a deferred
    `scenario_for_anomaly_spike(...)` variant would emit at higher
    density + carry an Anomaly edge.

Recipient is caller-supplied (the factory provides a deterministic
synthetic default + the leaf-internal fields the
`inv_pair_rolling_anomalies` filter requires:
`account_scope='internal' AND account_parent_role IS NOT NULL`).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import ClassVar

from recon_gen.common.spine._emit_helpers import insert_tx
from recon_gen.common.spine.violation import CoverageObservation


@dataclass(frozen=True)
class InvFanoutFactory:
    """Smart constructor namespace for `InvFanoutGenerator`.

    No L2-instance resolution needed — the Investigation matviews
    don't read rail/template declarations (they walk transactions
    by `account_scope` + `account_parent_role` + `transfer_id`).
    The factory just supplies sensible defaults + synthetic accounts.
    """

    name: ClassVar[str] = "inv_fanout"
    prefix: str = "spec_example"

    def scenario_for_fanout(
        self,
        *,
        sender_count: int = 2,
        recipient_account_id: str = "acct-inv-fanout-recipient",
        rail_name: str = "ach",
        amount_per_transfer: float = 100.0,
        anchor_day: date = date(2030, 1, 1),
    ) -> "InvFanoutGenerator":
        """Build a generator that plants `sender_count` distinct
        senders all crediting one recipient on the anchor day.

        Defaults are minimal-viable: 2 senders × $100 stays under
        the anomaly z-score threshold (the OLD plant's `boost_inv_
        fanout_plants(5×)` multiplies for anomaly density; the spine
        generator delegates that decision to the picker layer).
        """
        if sender_count < 1:
            raise ValueError(
                f"sender_count must be ≥1; got {sender_count}"
            )
        sender_ids = tuple(
            f"acct-inv-fanout-sender-{i:02d}"
            for i in range(sender_count)
        )
        return InvFanoutGenerator(
            recipient_account_id=recipient_account_id,
            sender_account_ids=sender_ids,
            rail_name=rail_name,
            amount_per_transfer=amount_per_transfer,
            anchor_day=anchor_day,
            prefix=self.prefix,
        )


@dataclass
class InvFanoutGenerator:
    """Emit N two-leg transfers (one per sender) all crediting one
    leaf-internal recipient on the anchor day. Each transfer is a
    debit on the sender + credit on the recipient summing to zero,
    sharing one `transfer_id`, both legs stamped with
    `metadata.sender_id` / `recipient_id` for downstream investigation.

    Recipient's account fields (`role`, `parent_role`) MUST satisfy
    the `inv_pair_rolling_anomalies` matview filter
    (`account_scope='internal' AND account_parent_role IS NOT NULL`)
    — defaults below populate them; production picker threads real
    `TemplateInstance.account_id` / role tuples post-AY.4.

    `intended` returns a CoverageObservation. Registered edge to
    `MoneyTrailInvariant`: each transfer is a depth-0 edge the
    `inv_money_trail_edges` recursive CTE surfaces.
    """

    recipient_account_id: str
    sender_account_ids: tuple[str, ...]
    rail_name: str
    amount_per_transfer: float
    anchor_day: date
    prefix: str = "spec_example"
    # Leaf-internal recipient fields — required by the anomaly matview
    # filter. Defaults pick a generic CustomerSubledger leaf shape.
    recipient_role: str = "CustomerSubledger"
    recipient_parent_role: str = "CustomerLedger"
    # Senders default to ExternalCounterparty (the natural fanout
    # source); senders may be any scope — the matview only filters
    # the recipient side.
    sender_role: str = "ExternalCounterparty"
    sender_scope: str = "external"
    sender_parent_role: str | None = None

    @property
    def transfer_ids(self) -> tuple[str, ...]:
        """The `len(sender_account_ids)` transfer_ids this plant
        emits, in stable sender-sorted order (matches the OLD
        emitter's deterministic ordering)."""
        return tuple(
            f"tr-inv-fanout-{sender}-{self.anchor_day.isoformat()}"
            for sender in sorted(self.sender_account_ids)
        )

    @property
    def intended(self) -> CoverageObservation:
        """Presence evidence: a fanout of `sender_count` senders to
        `recipient_account_id` landed on `anchor_day`. Identity carries
        the natural-key tuple a coverage detector would round-trip
        against if/when one lands."""
        return CoverageObservation.of(
            "inv_fanout",
            recipient_account_id=self.recipient_account_id,
            sender_count=len(self.sender_account_ids),
            rail_name=self.rail_name,
            anchor_day=self.anchor_day.isoformat(),
        )

    @property
    def claimed_accounts(self) -> frozenset[str]:
        """AV.5 contract: recipient + every sender. Two fanouts to
        the same recipient on the same day would collide via the
        recipient account_id."""
        return frozenset({
            self.recipient_account_id, *self.sender_account_ids,
        })

    def emit(
        self,
        conn: sqlite3.Connection,
        *,
        scenario_id: str | None = None,
    ) -> None:
        from recon_gen.common.spine.scenario_context import scenario_metadata
        # AY.2.b twist on metadata: AV.5's scenario_metadata gives us the
        # `scenario_id` payload; we union in the sender/recipient pair
        # values (the OLD path emits these for downstream investigation
        # filters). When untagged, just emit the pair values.
        for idx, sender_id in enumerate(sorted(self.sender_account_ids)):
            # Stratify posting time across the day; wraps at 12 like
            # the OLD path to keep a multi-sender plant inside the day.
            hour = 10 + (idx % 12)
            posting = (
                f"{self.anchor_day.isoformat()} "
                f"{hour:02d}:00:00"
            )
            transfer_id = (
                f"tr-inv-fanout-{sender_id}-{self.anchor_day.isoformat()}"
            )
            base_metadata = {
                "sender_id": sender_id,
                "recipient_id": self.recipient_account_id,
            }
            if scenario_id is not None:
                tagged = scenario_metadata(
                    scenario_id, generator="InvFanoutGenerator",
                )
                # scenario_metadata returns a JSON string; merge by
                # deserializing + adding pair keys + reserializing so
                # the row's JSON column carries everything.
                merged = {**json.loads(tagged), **base_metadata}
                metadata: str | None = json.dumps(
                    merged, sort_keys=True,
                    separators=(",", ":"),  # typing-smell: ignore[json-indent]: compact deterministic per-row DB metadata, not a human-diffable file
                )
            else:
                metadata = json.dumps(
                    base_metadata, sort_keys=True,
                    separators=(",", ":"),  # typing-smell: ignore[json-indent]: compact deterministic per-row DB metadata, not a human-diffable file
                )
            # Sender debit leg.
            insert_tx(
                conn,
                prefix=self.prefix,
                id=f"tx-inv-fanout-{sender_id}-src",
                account_id=sender_id,
                account_name=f"InvFanout sender {sender_id}",
                account_role=self.sender_role,
                account_scope=self.sender_scope,
                account_parent_role=self.sender_parent_role,
                amount_money=-self.amount_per_transfer,
                amount_direction="Debit",
                status="Posted",
                posting=posting,
                transfer_id=transfer_id,
                rail_name=self.rail_name,
                origin="ExternalInitiated",
                metadata=metadata,
            )
            # Recipient credit leg.
            insert_tx(
                conn,
                prefix=self.prefix,
                id=f"tx-inv-fanout-{sender_id}-dst",
                account_id=self.recipient_account_id,
                account_name=f"InvFanout recipient {self.recipient_account_id}",
                account_role=self.recipient_role,
                account_scope="internal",
                account_parent_role=self.recipient_parent_role,
                amount_money=self.amount_per_transfer,
                amount_direction="Credit",
                status="Posted",
                posting=posting,
                transfer_id=transfer_id,
                rail_name=self.rail_name,
                origin="ExternalInitiated",
                metadata=metadata,
            )
