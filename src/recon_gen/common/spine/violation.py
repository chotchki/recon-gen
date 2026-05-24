"""`Violation` — the typed evidence currency of the spine.

The spine emits + detects typed evidence about the institution's data
shape. Three concrete subtypes per the AY.0 design lock:

- `RuleViolation` — L1/L2 matview detects a rule break (drift,
  chain_parent_disagreement, anomaly, etc.). The post-AS-AT-AU-AX
  shape; every spine invariant promoted before AY emits these. THE
  default subtype that `Violation.of()` constructs (backward compat
  with the AS-era code that pre-dates the layering).
- `CoverageObservation` — seed-color presence claim (RailFiring,
  TransferTemplate, InvFanout, etc.). The GOOD signal — its ABSENCE
  is the bug; a coverage detector's failure to return one trips the
  regression gate.
- `AuditFixture` — auxiliary data the audit PDF consumes
  (SupersessionPlant, FailedTransactionPlant). Not a rule violation;
  not a coverage observation; an audit-PDF-specific row presence
  claim.

Identity is the analyst-facing columns naming the evidence: account
+ day + magnitude for drift; pair + sigma band for anomaly;
root_transfer + depth for a money trail. Two same-subtype instances
compare equal iff they name the same evidence, REGARDLESS of which
detector emitted them or which generator was the cause.

Cross-subtype equality is FALSE even when identity matches —
`RuleViolation(a, b) != CoverageObservation(a, b)`. The runtime
class is the discriminator (frozen dataclass __eq__ checks
`self.__class__ is other.__class__`); pyright narrows via
`isinstance(v, RuleViolation)`. AY.0 picked this over a
Literal[...] field discriminator for the stronger typing per
``[[feedback-invariants-in-types]]``.

Why a frozenset of `(column, value)` pairs and not a fixed schema:
different invariants have different identity columns. A drift row IS
`(account_id, business_day, drift_amount)`; an anomaly row is
`(sender_id, recipient_id, sigma_band)`. A fixed schema would force a
common denominator that loses information. The frozenset shape makes
equality column-aware while letting per-invariant identity vary.

Smart constructors `RuleViolation.of(...)` / `CoverageObservation.of(...)`
/ `AuditFixture.of(...)` are the blessed way to build each subtype.
`Violation.of(...)` survives as a backward-compat alias that returns a
`RuleViolation` (pre-AY.2.a code expected the rule-violation shape).

Promoted from `tests/unit/test_as0_drift_full_spine.py` by AS.1;
layered into subtypes by AY.2.a.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Violation:
    """Abstract base for typed spine evidence.

    Two fields:
      - ``invariant``: the matview / coverage / fixture name
        (e.g. ``"drift"``, ``"rail_firing"``, ``"supersession"``). The
        string discriminator the lock dict keys on; pairs with the
        runtime subtype to identify the specific shape.
      - ``identity``: a frozenset of ``(column, value)`` pairs naming
        the row.

    NEVER constructed directly in new code — use one of the three
    concrete subtypes (`RuleViolation` / `CoverageObservation` /
    `AuditFixture`). The base exists for typing + the legacy
    `Violation.of(...)` alias only.
    """

    invariant: str
    identity: frozenset[tuple[str, object]]

    @classmethod
    def of(cls, invariant: str, **identity: object) -> "Violation":
        """Backward-compat alias — returns a `RuleViolation` (a
        `Violation` subtype).

        Pre-AY.2.a, every spine generator + detector built Violations
        via this constructor; AY.2.a layered in subtypes but kept the
        alias so the migration is incremental. New code should
        prefer the subtype-explicit constructors
        (``RuleViolation.of(...)`` etc.) for type clarity.

        Return type is the abstract `Violation` for liskov / covariant
        override compatibility — subclasses' `.of()` can return their
        own concrete subtype without tripping pyright. At runtime this
        method returns a `RuleViolation` (the AS-era default).
        """
        return RuleViolation(
            invariant=invariant, identity=frozenset(identity.items()),
        )


@dataclass(frozen=True)
class RuleViolation(Violation):
    """A matview-detected rule break.

    The post-AS shape — every L1 accounting / L2-shape integrity /
    L2 investigation invariant promoted pre-AY emits these. The
    semantic_lock dict's values are `frozenset[RuleViolation]` for
    every spine `Invariant` shipped to date.
    """

    @classmethod
    def of(cls, invariant: str, **identity: object) -> "RuleViolation":
        """Build a typed `RuleViolation`. The blessed constructor
        for rule-violation evidence."""
        return cls(
            invariant=invariant, identity=frozenset(identity.items()),
        )


@dataclass(frozen=True)
class CoverageObservation(Violation):
    """A seed-color presence claim.

    AY.2.b's 5 coverage generators (TwoTemplateChainGenerator,
    TransferTemplateGenerator, RailFiringGenerator,
    InvFanoutGenerator, FanInChainGenerator(healthy)) emit these.
    Their `intended` carries the row the matching coverage detector
    (when one exists) reads back to confirm the seed met the
    documented coverage shape. ABSENCE is the regression — a
    `CoverageInvariant.detect()` returning empty means the seed
    didn't emit what the docs claim.
    """

    @classmethod
    def of(cls, invariant: str, **identity: object) -> "CoverageObservation":
        return cls(
            invariant=invariant, identity=frozenset(identity.items()),
        )


@dataclass(frozen=True)
class AuditFixture(Violation):
    """An audit-PDF input row marker.

    AY.2.b's 2 audit-fixture generators (SupersessionGenerator,
    FailedTransactionGenerator) emit these. Supersession +
    FailedTransaction rows surface only in the audit PDF (no
    matview, no coverage detector); the spine carries them for
    substrate uniformity (claimed_accounts collision check, AV.5
    metadata tagging, ScenarioContext cleanup attribution).
    """

    @classmethod
    def of(cls, invariant: str, **identity: object) -> "AuditFixture":
        return cls(
            invariant=invariant, identity=frozenset(identity.items()),
        )
